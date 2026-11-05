#!/usr/bin/env python3

import base64
import hashlib
import io
import json
import os
import platform

import redis
from flask import Flask, jsonify, request, send_file
from minio import Minio
from minio.error import S3Error


TRACKS = {"bass", "drums", "vocals", "other"}


def _as_bool(value):
    return str(value).lower() in {"1", "true", "yes", "on"}


def create_app(redis_client=None, minio_client=None):
    app = Flask(__name__)

    app.config.update(
        REDIS_QUEUE_KEY=os.getenv("REDIS_QUEUE_KEY", "toWorker"),
        REDIS_LOG_KEY=os.getenv("REDIS_LOG_KEY", "logging"),
        MINIO_BUCKET=os.getenv("MINIO_BUCKET", "demucs-bucket"),
    )

    app.redis_client = redis_client or redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=0,
        decode_responses=True,
    )
    app.minio_client = minio_client or Minio(
        os.getenv("MINIO_HOST", "localhost:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "rootuser"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "rootpass123"),
        secure=_as_bool(os.getenv("MINIO_SECURE", "false")),
    )

    def log_info(message):
        entry = f"{platform.node()}.rest.info:{message}"
        try:
            app.redis_client.lpush(app.config["REDIS_LOG_KEY"], entry)
        except redis.RedisError:
            app.logger.warning("Unable to write Redis log entry: %s", message)

    def ensure_bucket():
        bucket = app.config["MINIO_BUCKET"]
        if not app.minio_client.bucket_exists(bucket):
            app.minio_client.make_bucket(bucket)

    def normalize_track(track):
        name = track.lower()
        if name.endswith(".mp3"):
            name = name[:-4]
        if name == "base":  # The lab handout uses both base and bass.
            name = "bass"
        return name if name in TRACKS else None

    def output_object(songhash, track):
        return f"{songhash}-{track}.mp3"

    @app.get("/")
    def health():
        return jsonify(service="music-separation-rest", status="ok")

    @app.post("/apiv1/separate")
    def separate():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("mp3"), str):
            return jsonify(reason="Request must contain a base64 encoded mp3 field"), 400

        try:
            audio = base64.b64decode(body["mp3"], validate=True)
        except (ValueError, TypeError):
            return jsonify(reason="mp3 is not valid base64 data"), 400
        if not audio:
            return jsonify(reason="mp3 must not be empty"), 400

        callback = body.get("callback")
        if callback is not None and not isinstance(callback, dict):
            return jsonify(reason="callback must be a JSON object"), 400

        songhash = hashlib.sha256(audio).hexdigest()
        object_name = f"queue/{songhash}.mp3"
        job = {
            "hash": songhash,
            "object": object_name,
            "model": body.get("model", os.getenv("DEMUCS_MODEL", "mdx_extra_q")),
            "callback": callback,
        }

        try:
            ensure_bucket()
            app.minio_client.put_object(
                app.config["MINIO_BUCKET"],
                object_name,
                io.BytesIO(audio),
                length=len(audio),
                content_type="audio/mpeg",
            )
            app.redis_client.lpush(app.config["REDIS_QUEUE_KEY"], json.dumps(job))
        except (S3Error, redis.RedisError, OSError) as exc:
            app.logger.exception("Unable to enqueue song")
            return jsonify(reason=f"Unable to enqueue song: {exc}"), 503

        log_info(f"queued {songhash}")
        return jsonify(hash=songhash, reason="Song enqueued for separation")

    @app.get("/apiv1/queue")
    @app.get("/apiv1/queue/")
    def queue():
        try:
            entries = app.redis_client.lrange(app.config["REDIS_QUEUE_KEY"], 0, -1)
        except redis.RedisError as exc:
            return jsonify(reason=f"Unable to read queue: {exc}"), 503

        queued = []
        for entry in entries:
            try:
                queued.append(json.loads(entry).get("hash", entry))
            except (json.JSONDecodeError, AttributeError):
                queued.append(entry)
        return jsonify(queue=queued)

    @app.get("/apiv1/track/<songhash>/<track>")
    def get_track(songhash, track):
        part = normalize_track(track)
        if part is None:
            return jsonify(reason="Unknown track"), 400

        try:
            response = app.minio_client.get_object(
                app.config["MINIO_BUCKET"], output_object(songhash, part)
            )
            data = response.read()
            response.close()
            response.release_conn()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return jsonify(reason="Track not found"), 404
            return jsonify(reason=f"Unable to retrieve track: {exc}"), 503

        return send_file(
            io.BytesIO(data),
            mimetype="audio/mpeg",
            as_attachment=True,
            download_name=f"{songhash}-{part}.mp3",
        )

    @app.route("/apiv1/remove/<songhash>/<track>", methods=["GET", "DELETE"])
    def remove_track(songhash, track):
        part = normalize_track(track)
        if part is None:
            return jsonify(reason="Unknown track"), 400

        try:
            app.minio_client.stat_object(
                app.config["MINIO_BUCKET"], output_object(songhash, part)
            )
            app.minio_client.remove_object(
                app.config["MINIO_BUCKET"], output_object(songhash, part)
            )
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return jsonify(reason="Track not found"), 404
            return jsonify(reason=f"Unable to remove track: {exc}"), 503

        log_info(f"removed {songhash}-{part}.mp3")
        return jsonify(hash=songhash, reason="Track removed", track=f"{part}.mp3")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("REST_PORT", "5000")))
