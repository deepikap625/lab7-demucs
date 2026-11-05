#!/usr/bin/env python3

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import redis
import requests
from minio import Minio


TRACKS = ("bass", "drums", "vocals", "other")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _as_bool(value):
    return str(value).lower() in {"1", "true", "yes", "on"}


class Worker:
    def __init__(self, redis_client=None, minio_client=None, runner=None, http_post=None):
        self.queue_key = os.getenv("REDIS_QUEUE_KEY", "toWorker")
        self.log_key = os.getenv("REDIS_LOG_KEY", "logging")
        self.bucket = os.getenv("MINIO_BUCKET", "demucs-bucket")
        self.redis = redis_client or redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=0,
            decode_responses=True,
        )
        self.minio = minio_client or Minio(
            os.getenv("MINIO_HOST", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "rootuser"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "rootpass123"),
            secure=_as_bool(os.getenv("MINIO_SECURE", "false")),
        )
        self.runner = runner or subprocess.run
        self.http_post = http_post or requests.post

    def log(self, level, message):
        entry = f"{platform.node()}.worker.{level}:{message}"
        print(f"{level.upper()}: {message}")
        self.redis.lpush(self.log_key, entry)
        sys.stdout.flush()

    def _validate_job(self, job):
        songhash = job.get("hash")
        object_name = job.get("object")
        model = job.get("model", os.getenv("DEMUCS_MODEL", "mdx_extra_q"))
        if not isinstance(songhash, str) or not HASH_PATTERN.fullmatch(songhash):
            raise ValueError("job hash must be a 64-character lowercase SHA-256 value")
        if not isinstance(object_name, str) or not object_name:
            raise ValueError("job object is required")
        if not isinstance(model, str) or not MODEL_PATTERN.fullmatch(model):
            raise ValueError("job model contains unsupported characters")
        return songhash, object_name, model

    def process_job(self, job):
        songhash, object_name, model = self._validate_job(job)
        self.log("info", f"starting {songhash}")

        with tempfile.TemporaryDirectory(prefix="demucs-") as workdir:
            workdir = Path(workdir)
            input_path = workdir / f"{songhash}.mp3"
            output_root = workdir / "output"
            self.minio.fget_object(self.bucket, object_name, str(input_path))
            self.log("debug", f"downloaded {object_name}")

            command = [
                "python3",
                "-m",
                "demucs",
                "-n",
                model,
                "--out",
                str(output_root),
                "--mp3",
                "--shifts",
                os.getenv("DEMUCS_SHIFTS", "1"),
                "--overlap",
                os.getenv("DEMUCS_OVERLAP", "0.1"),
                str(input_path),
            ]
            self.runner(command, check=True)

            result_dir = output_root / model / songhash
            for track in TRACKS:
                track_path = result_dir / f"{track}.mp3"
                if not track_path.is_file():
                    raise FileNotFoundError(f"Demucs did not produce {track_path}")
                object_path = f"{songhash}-{track}.mp3"
                self.minio.fput_object(
                    self.bucket,
                    object_path,
                    str(track_path),
                    content_type="audio/mpeg",
                )
                self.log("debug", f"uploaded {object_path}")

        self.log("info", f"completed {songhash}")
        self._send_callback(job.get("callback"), songhash)
        return songhash

    def _send_callback(self, callback, songhash):
        if not isinstance(callback, dict) or not callback.get("url"):
            return
        try:
            self.http_post(callback["url"], json=callback.get("data"), timeout=10)
            self.log("debug", f"callback attempted for {songhash}")
        except requests.RequestException as exc:
            self.log("debug", f"callback failed for {songhash}: {exc}")

    def run_once(self, timeout=0):
        work = self.redis.blpop(self.queue_key, timeout=timeout)
        if work is None:
            return False
        try:
            job = json.loads(work[1])
            self.process_job(job)
        except Exception as exc:
            self.log("info", f"job failed: {exc}")
        return True

    def run(self):
        self.log("info", "worker ready")
        once = _as_bool(os.getenv("WORKER_ONCE", "false"))
        while True:
            self.run_once(timeout=0)
            if once:
                return


if __name__ == "__main__":
    Worker().run()
