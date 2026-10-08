from __future__ import annotations

import hashlib
import io
import math
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault_vision import faces
from reelvault_vision.model import MAX_IMAGE_BYTES
from reelvault_vision.server import create_app

TOKEN = "face-test-token-" + "x" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "image/jpeg"}


class FakeClip:
    def text_vectors(self, texts):
        return [[1.0] + [0.0] * 511 for _ in texts]


class FakeFaces:
    def detect(self, data):
        if data != b"image":
            raise ValueError("Invalid image")
        return [{"box": [0.1, 0.1, 0.2, 0.2], "score": 0.99, "vector": [1.0] + [0.0] * 127}]


def test_optional_face_contract_and_limits(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, models=FakeClip())) as client:
        assert "face_model" not in client.get("/health", headers=HEADERS).json()
        assert client.post("/embed/faces", content=b"image").status_code == 401
        assert client.post("/embed/faces", headers=HEADERS, content=b"image").status_code == 403
    with TestClient(create_app(tmp_path, TOKEN, models=FakeClip(), faces=FakeFaces())) as client:
        assert client.get("/health", headers=HEADERS).json()["face_model"] == faces.FACE_MODEL_ID
        result = client.post("/embed/faces", headers=HEADERS, content=b"image").json()
        assert result["face_dimension"] == 128
        assert result["face_model"] == faces.FACE_MODEL_ID
        assert len(result["faces"][0]["vector"]) == 128
        assert client.post("/embed/faces", headers=HEADERS, content=b"bad").status_code == 400
        assert (
            client.post(
                "/embed/faces", headers={"Authorization": f"Bearer {TOKEN}"}, content=b"image"
            ).status_code
            == 415
        )
        assert (
            client.post(
                "/embed/faces", headers=HEADERS, content=b"x" * (MAX_IMAGE_BYTES + 1)
            ).status_code
            == 413
        )


def test_face_and_clip_share_one_inference_slot(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Slow(FakeFaces):
        def detect(self, data):
            entered.set()
            assert release.wait(5)
            return super().detect(data)

    with TestClient(create_app(tmp_path, TOKEN, models=FakeClip(), faces=Slow())) as client:
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                client.post("/embed/faces", headers=HEADERS, content=b"image")
            )
        )
        worker.start()
        try:
            assert entered.wait(5)
            assert client.get("/health", headers=HEADERS).status_code == 200
            assert (
                client.post(
                    "/embed/text",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    json={"texts": ["red"]},
                ).status_code
                == 503
            )
            assert client.post("/embed/faces", headers=HEADERS, content=b"image").status_code == 503
        finally:
            release.set()
            worker.join(5)
        assert results[0].status_code == 200


def test_face_model_download_is_explicit_atomic_and_verified(tmp_path, monkeypatch):
    data = b"verified-model"
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(faces, "WEIGHTS", (("model/model.onnx", digest, len(data)),))
    calls = []

    def download(url, timeout):
        calls.append(url)
        return io.BytesIO(data)

    monkeypatch.setattr(faces.urllib.request, "urlopen", download)
    with pytest.raises(ValueError, match="run --prepare-faces"):
        faces.Faces(tmp_path)
    assert not calls
    faces.prepare_faces(tmp_path)
    target = tmp_path / "faces/model.onnx"
    assert target.read_bytes() == data
    assert faces.REVISION in calls[0]
    faces.prepare_faces(tmp_path)
    assert len(calls) == 1
    target.write_bytes(b"old-cache")
    for bad in (b"short", b"x" * len(data), data + b"extra"):
        monkeypatch.setattr(
            faces.urllib.request,
            "urlopen",
            lambda *args, payload=bad, **kwargs: io.BytesIO(payload),
        )
        with pytest.raises(ValueError):
            faces.prepare_faces(tmp_path)
        assert target.read_bytes() == b"old-cache"
        assert not list(target.parent.glob(".prepare-*"))
    target.unlink()
    other = tmp_path / "other.onnx"
    other.write_bytes(data)
    target.symlink_to(other)
    assert not faces.verified(target, digest, len(data))


def test_face_vectors_are_finite_and_normalized():
    result = faces.normalized_face([2.0] + [0.0] * 127)
    assert result == [1.0] + [0.0] * 127
    for bad in ([0.0] * 128, [1.0] * 127, [math.nan] * 128, [math.inf] * 128):
        with pytest.raises(ValueError):
            faces.normalized_face(bad)


@pytest.mark.skipif(
    not os.environ.get("REELVAULT_TEST_FACE_CACHE")
    or not os.environ.get("REELVAULT_TEST_FACE_IMAGE"),
    reason="requires explicitly prepared face models and a face fixture",
)
def test_real_offline_face_detection_and_embeddings(tmp_path):
    from PIL import Image

    model = faces.Faces(Path(os.environ["REELVAULT_TEST_FACE_CACHE"]))
    data = Path(os.environ["REELVAULT_TEST_FACE_IMAGE"]).read_bytes()
    original = model.detect(data)
    assert original
    stream = io.BytesIO()
    with Image.open(io.BytesIO(data)) as image:
        image.resize((384, 384)).save(stream, format="JPEG", quality=90)
    changed = model.detect(stream.getvalue())
    assert changed
    first, second = original[0]["vector"], changed[0]["vector"]
    assert sum(a * b for a, b in zip(first, second, strict=True)) > 0.7
    for face in original + changed:
        assert len(face["vector"]) == 128
        assert sum(v * v for v in face["vector"]) == pytest.approx(1)
        x, y, w, h = face["box"]
        assert 0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
        assert x + w <= 1.000001 and y + h <= 1.000001
        assert 0.85 <= face["score"] <= 1
    empty = io.BytesIO()
    Image.new("RGB", (320, 240), "blue").save(empty, format="PNG")
    assert model.detect(empty.getvalue()) == []
    tiny = io.BytesIO()
    Image.new("RGB", (1, 1), "blue").save(tiny, format="PNG")
    assert model.detect(tiny.getvalue()) == []
    with TestClient(create_app(tmp_path, TOKEN, models=FakeClip(), faces=model)) as client:
        response = client.post("/embed/faces", headers=HEADERS, content=data)
        assert response.status_code == 200
        result = response.json()
        assert result["face_model"] == faces.FACE_MODEL_ID
        assert result["faces"][0]["vector"] == pytest.approx(original[0]["vector"])
        assert (
            client.post("/embed/faces", headers=HEADERS, content=empty.getvalue()).json()["faces"]
            == []
        )
