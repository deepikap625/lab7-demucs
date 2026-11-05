import base64
import importlib.util
import io
import json
import pathlib
import unittest

from minio.error import S3Error


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rest_server", ROOT / "rest" / "rest-server.py"
)
REST_SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REST_SERVER)


class FakeRedis:
    def __init__(self):
        self.lists = {}

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        return values[start:] if end == -1 else values[start : end + 1]


class FakeResponse(io.BytesIO):
    def close(self):
        pass

    def release_conn(self):
        pass


class FakeMinio:
    def __init__(self):
        self.buckets = set()
        self.objects = {}

    def bucket_exists(self, bucket):
        return bucket in self.buckets

    def make_bucket(self, bucket):
        self.buckets.add(bucket)

    def put_object(self, bucket, name, data, length, content_type=None):
        self.objects[(bucket, name)] = data.read(length)

    def get_object(self, bucket, name):
        try:
            return FakeResponse(self.objects[(bucket, name)])
        except KeyError as exc:
            raise S3Error(None, "NoSuchKey", "missing", name, None, None) from exc

    def stat_object(self, bucket, name):
        if (bucket, name) not in self.objects:
            raise S3Error(None, "NoSuchKey", "missing", name, None, None)
        return object()

    def remove_object(self, bucket, name):
        self.objects.pop((bucket, name), None)


class RestServerTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.minio = FakeMinio()
        app = REST_SERVER.create_app(self.redis, self.minio)
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_health(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

    def test_separate_uploads_and_enqueues(self):
        audio = b"ID3-test-audio"
        response = self.client.post(
            "/apiv1/separate",
            json={"mp3": base64.b64encode(audio).decode("ascii")},
        )
        self.assertEqual(response.status_code, 200)
        songhash = response.json["hash"]
        self.assertEqual(
            self.minio.objects[("demucs-bucket", f"queue/{songhash}.mp3")], audio
        )
        job = json.loads(self.redis.lists["toWorker"][0])
        self.assertEqual(job["hash"], songhash)
        self.assertEqual(job["model"], "mdx_extra_q")

        queue = self.client.get("/apiv1/queue").json["queue"]
        self.assertEqual(queue, [songhash])

    def test_rejects_invalid_audio(self):
        response = self.client.post("/apiv1/separate", json={"mp3": "not-base64"})
        self.assertEqual(response.status_code, 400)

    def test_download_and_remove_track(self):
        key = ("demucs-bucket", "abc-vocals.mp3")
        self.minio.objects[key] = b"track-data"

        response = self.client.get("/apiv1/track/abc/vocals.mp3")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"track-data")

        response = self.client.delete("/apiv1/remove/abc/vocals.mp3")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(key, self.minio.objects)

        response = self.client.get("/apiv1/track/abc/vocals.mp3")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
