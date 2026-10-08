from __future__ import annotations

import asyncio
import io
import math
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelvault_vision.model import (
    DIMENSION,
    IMAGE_MODEL,
    IMAGE_REVISION,
    MAX_IMAGE_BYTES,
    MODEL_ID,
    TEXT_MODEL,
    TEXT_REVISION,
    Models,
    normalized,
    prepare,
    snapshot,
)
from reelvault_vision.server import create_app

TOKEN = "test-token-" + "x" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeModels:
    def text_vectors(self, texts):
        return [[1.0] + [0.0] * (DIMENSION - 1) for _ in texts]

    def image_vector(self, data):
        if data != b"valid-image":
            raise ValueError("bad image")
        return [1.0] + [0.0] * (DIMENSION - 1)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, models=FakeModels())) as client:
        yield client


def test_authenticated_contract_and_payload_limits(client):
    for url in ("/health", "/docs", "/openapi.json"):
        assert client.get(url).status_code in {401, 404}
    assert client.get("/health", headers=HEADERS).json() == {
        "protocol": 1,
        "model": MODEL_ID,
        "dimension": DIMENSION,
        "ready": True,
    }
    result = client.post("/embed/text", headers=HEADERS, json={"texts": ["红色", "blue"]})
    assert result.status_code == 200
    assert result.json()["model"] == MODEL_ID
    assert len(result.json()["vectors"]) == 2
    for texts in ([], [""], [" "], ["a" * 513], ["a"] * 17):
        assert client.post("/embed/text", headers=HEADERS, json={"texts": texts}).status_code == 422
    assert client.post("/embed/image", headers=HEADERS, content=b"valid-image").status_code == 415
    image_headers = {**HEADERS, "Content-Type": "image/png"}
    assert (
        client.post("/embed/image", headers=image_headers, content=b"valid-image").status_code
        == 200
    )
    assert client.post("/embed/image", headers=image_headers, content=b"bad").status_code == 400
    oversized = b"x" * (MAX_IMAGE_BYTES + 1)
    assert client.post("/embed/image", headers=image_headers, content=oversized).status_code == 413
    assert (
        client.post(
            "/embed/text",
            headers={**HEADERS, "Content-Type": "application/json"},
            content=oversized,
        ).status_code
        == 413
    )


def test_busy_service_preserves_health_and_rejects_second_inference(tmp_path):
    started, release = threading.Event(), threading.Event()

    class Slow(FakeModels):
        def text_vectors(self, texts):
            started.set()
            assert release.wait(10)
            return super().text_vectors(texts)

    with TestClient(create_app(tmp_path, TOKEN, models=Slow())) as client:
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                client.post("/embed/text", headers=HEADERS, json={"texts": ["red"]})
            )
        )
        worker.start()
        try:
            assert started.wait(5)
            assert client.get("/health", headers=HEADERS).status_code == 200
            busy = client.post("/embed/text", headers=HEADERS, json={"texts": ["blue"]})
            assert busy.status_code == 503
            assert busy.headers["Retry-After"] == "1"
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive()
        assert results[0].status_code == 200


@pytest.mark.parametrize("fail", [False, True])
def test_cancelled_request_retains_inference_lock_until_thread_stops(tmp_path, fail):
    import httpx

    started, release = threading.Event(), threading.Event()

    class Slow(FakeModels):
        def text_vectors(self, texts):
            started.set()
            assert release.wait(10)
            if fail:
                raise ValueError("Inference failed after cancellation")
            return super().text_vectors(texts)

    async def scenario():
        app = create_app(tmp_path, TOKEN, models=Slow())
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            request = asyncio.create_task(
                client.post("/embed/text", headers=HEADERS, json={"texts": ["red"]})
            )
            try:
                assert await asyncio.to_thread(started.wait, 5)
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                busy = await client.post("/embed/text", headers=HEADERS, json={"texts": ["blue"]})
                assert busy.status_code == 503
                assert not request.done()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request, 5)
            assert not app.state.lock.locked()

    asyncio.run(scenario())


@pytest.mark.parametrize("token", ["", "short", "x" * 257, "x" * 32 + " ", "中文" * 32])
def test_service_requires_deployment_token(tmp_path, token):
    with pytest.raises(ValueError):
        create_app(tmp_path, token, models=FakeModels())


def test_vector_contract_rejects_nonfinite_empty_and_wrong_dimensions():
    for values in ([0] * DIMENSION, [math.inf] * DIMENSION, [math.nan] * DIMENSION, [1, 2]):
        with pytest.raises(ValueError):
            normalized(values)
    vector = normalized([2.0] * DIMENSION)
    assert sum(v * v for v in vector) == pytest.approx(1)


def test_model_cache_is_offline_except_explicit_preparation(tmp_path, monkeypatch):
    import huggingface_hub

    calls = []

    def download(model, **kwargs):
        calls.append((model, kwargs))
        return str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    assert snapshot(tmp_path, IMAGE_MODEL, IMAGE_REVISION) == str(tmp_path)
    prepare(tmp_path)
    assert [model for model, _ in calls] == [IMAGE_MODEL, IMAGE_MODEL, TEXT_MODEL]
    assert [args["revision"] for _, args in calls] == [
        IMAGE_REVISION,
        IMAGE_REVISION,
        TEXT_REVISION,
    ]
    assert [args["local_files_only"] for _, args in calls] == [True, False, False]
    assert all(args["allow_patterns"] == ["*.json", "*.txt", "*.safetensors"] for _, args in calls)


def test_decode_limits_precede_image_inference():
    from PIL import Image

    models = object.__new__(Models)
    models.image = None  # A rejected image must never reach the encoder.
    for data in (b"", b"not an image", b"x" * (MAX_IMAGE_BYTES + 1)):
        with pytest.raises((ValueError, OSError)):
            models.image_vector(data)
    buffer = io.BytesIO()
    Image.new("RGB", (1025, 1)).save(buffer, format="PNG")
    with pytest.raises(ValueError, match="dimensions"):
        models.image_vector(buffer.getvalue())
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, format="GIF")
    with pytest.raises(ValueError, match="static"):
        models.image_vector(buffer.getvalue())
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(
        buffer,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")],
        duration=100,
    )
    with pytest.raises(ValueError, match="static"):
        models.image_vector(buffer.getvalue())


def test_query_images_apply_exif_rotation_and_rgb_conversion():
    from PIL import Image

    seen = []

    class Encoder:
        def encode(self, image, **kwargs):
            seen.append((image.mode, image.size))
            return [1.0] + [0.0] * (DIMENSION - 1)

    models = object.__new__(Models)
    models.image = Encoder()
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (64, 32), "red").save(buffer, format="JPEG", exif=exif)
    assert models.image_vector(buffer.getvalue())[0] == 1
    assert seen == [("RGB", (32, 64))]


@pytest.mark.skipif(
    not os.environ.get("REELVAULT_TEST_CLIP_CACHE"), reason="Prepared CLIP models required"
)
def test_real_offline_multilingual_clip():
    from PIL import Image, ImageDraw

    cache = Path(os.environ["REELVAULT_TEST_CLIP_CACHE"])
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    with TestClient(create_app(cache, TOKEN)) as client:
        vectors = []
        for color in ("red", "blue"):
            image = Image.new("RGB", (256, 256), "white")
            ImageDraw.Draw(image).rectangle((32, 32, 224, 224), fill=color)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            response = client.post(
                "/embed/image",
                headers={**HEADERS, "Content-Type": "image/png"},
                content=buffer.getvalue(),
            )
            assert response.status_code == 200
            vectors.append(response.json()["vectors"][0])
        response = client.post(
            "/embed/text", headers=HEADERS, json={"texts": ["a red square", "蓝色方块", "藍色方塊"]}
        )
        assert response.status_code == 200
        for index, text_vector in enumerate(response.json()["vectors"]):
            scores = [
                sum(a * b for a, b in zip(text_vector, image_vector, strict=True))
                for image_vector in vectors
            ]
            assert scores[0 if index == 0 else 1] > scores[1 if index == 0 else 0], scores
        assert all(
            len(v) == DIMENSION and sum(x * x for x in v) == pytest.approx(1) for v in vectors
        )
