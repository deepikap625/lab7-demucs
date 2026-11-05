"""Smoke-test the REST API against locally forwarded Redis and MinIO."""

import base64
import importlib.util
import io
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rest_server", ROOT / "rest" / "rest-server.py"
)
REST_SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REST_SERVER)


def main():
    app = REST_SERVER.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    audio = (ROOT / "data" / "short-dreams.mp3").read_bytes()

    songhash = None
    queued_entry = None
    try:
        response = client.post(
            "/apiv1/separate",
            json={"mp3": base64.b64encode(audio).decode("ascii")},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        songhash = response.json["hash"]

        entries = app.redis_client.lrange(app.config["REDIS_QUEUE_KEY"], 0, -1)
        queued_entry = next(entry for entry in entries if json.loads(entry)["hash"] == songhash)
        app.minio_client.stat_object(
            app.config["MINIO_BUCKET"], f"queue/{songhash}.mp3"
        )

        track = b"ID3-integration-test-track"
        app.minio_client.put_object(
            app.config["MINIO_BUCKET"],
            f"{songhash}-vocals.mp3",
            io.BytesIO(track),
            len(track),
            content_type="audio/mpeg",
        )
        response = client.get(f"/apiv1/track/{songhash}/vocals.mp3")
        assert response.status_code == 200 and response.data == track

        response = client.delete(f"/apiv1/remove/{songhash}/vocals.mp3")
        assert response.status_code == 200
        print(f"UPLOAD=200 HASH={songhash}")
        print("REDIS_JOB=FOUND MINIO_INPUT=FOUND")
        print("TRACK_DOWNLOAD=200 TRACK_REMOVE=200")
    finally:
        if queued_entry is not None:
            app.redis_client.lrem(app.config["REDIS_QUEUE_KEY"], 1, queued_entry)
        if songhash is not None:
            app.minio_client.remove_object(
                app.config["MINIO_BUCKET"], f"queue/{songhash}.mp3"
            )
            for entry in app.redis_client.lrange(app.config["REDIS_LOG_KEY"], 0, -1):
                if songhash in entry:
                    app.redis_client.lrem(app.config["REDIS_LOG_KEY"], 0, entry)


if __name__ == "__main__":
    main()
