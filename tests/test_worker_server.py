import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "worker_server", ROOT / "worker" / "worker-server.py"
)
WORKER_SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER_SERVER)


class FakeRedis:
    def __init__(self, jobs=None):
        self.jobs = list(jobs or [])
        self.logs = []

    def blpop(self, key, timeout=0):
        return (key, self.jobs.pop(0)) if self.jobs else None

    def lpush(self, key, value):
        self.logs.insert(0, value)


class FakeMinio:
    def __init__(self):
        self.uploads = {}

    def fget_object(self, bucket, object_name, file_path):
        pathlib.Path(file_path).write_bytes(b"ID3-input")

    def fput_object(self, bucket, object_name, file_path, content_type=None):
        self.uploads[(bucket, object_name)] = pathlib.Path(file_path).read_bytes()


class FakeRunner:
    def __call__(self, command, check=False):
        output_root = pathlib.Path(command[command.index("--out") + 1])
        model = command[command.index("-n") + 1]
        songhash = pathlib.Path(command[-1]).stem
        result_dir = output_root / model / songhash
        result_dir.mkdir(parents=True)
        for track in WORKER_SERVER.TRACKS:
            (result_dir / f"{track}.mp3").write_bytes(f"{track}-data".encode())
        self.command = command


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.songhash = "a" * 64
        self.job = {
            "hash": self.songhash,
            "object": f"queue/{self.songhash}.mp3",
            "model": "htdemucs",
            "callback": {"url": "http://callback", "data": {"done": True}},
        }
        self.redis = FakeRedis()
        self.minio = FakeMinio()
        self.runner = FakeRunner()
        self.callbacks = []
        self.worker = WORKER_SERVER.Worker(
            redis_client=self.redis,
            minio_client=self.minio,
            runner=self.runner,
            http_post=lambda *args, **kwargs: self.callbacks.append((args, kwargs)),
        )

    def test_process_job_uploads_four_tracks_and_calls_callback(self):
        result = self.worker.process_job(self.job)
        self.assertEqual(result, self.songhash)
        self.assertIn("--mp3", self.runner.command)
        self.assertEqual(len(self.minio.uploads), 4)
        for track in WORKER_SERVER.TRACKS:
            key = ("demucs-bucket", f"{self.songhash}-{track}.mp3")
            self.assertEqual(self.minio.uploads[key], f"{track}-data".encode())
        self.assertEqual(self.callbacks[0][1]["json"], {"done": True})

    def test_run_once_consumes_redis_job(self):
        self.redis.jobs.append(json.dumps(self.job))
        self.assertTrue(self.worker.run_once(timeout=1))
        self.assertFalse(self.worker.run_once(timeout=1))
        self.assertTrue(any("completed" in entry for entry in self.redis.logs))

    def test_rejects_invalid_job(self):
        with self.assertRaises(ValueError):
            self.worker.process_job({"hash": "bad", "object": "input.mp3"})


if __name__ == "__main__":
    unittest.main()
