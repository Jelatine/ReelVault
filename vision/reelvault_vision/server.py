from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .model import DIMENSION, MAX_IMAGE_BYTES, MODEL_ID, Models


class BodyLimit:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        size = 0

        async def limited_receive() -> Message:
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > MAX_IMAGE_BYTES:
                raise HTTPException(413, "Request body exceeds 1 MiB")
            return message

        await self.app(scope, limited_receive, send)


class TextInput(BaseModel):
    texts: Annotated[list[str], Field(min_length=1, max_length=16)]

    @field_validator("texts")
    @classmethod
    def valid_texts(cls, value: list[str]) -> list[str]:
        if any(not text.strip() or len(text) > 512 for text in value):
            raise ValueError("Each description must contain 1–512 characters")
        return value


def create_app(cache: Path, token: str, threads: int = 2, models: Any = None) -> FastAPI:
    if not 32 <= len(token) <= 256 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("VISION_TOKEN requires 32–256 printable ASCII characters without spaces")
    if not 1 <= threads <= 32:
        raise ValueError("Thread count must be between 1 and 32")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.models = (
            models if models is not None else await asyncio.to_thread(Models, cache, threads)
        )
        app.state.lock = asyncio.Lock()
        yield

    async def authenticate(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(authenticate)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimit)

    def result(vectors: list[list[float]]) -> dict[str, Any]:
        return {"protocol": 1, "model": MODEL_ID, "dimension": DIMENSION, "vectors": vectors}

    async def infer(function: Any, argument: Any) -> dict[str, Any]:
        if app.state.lock.locked():
            raise HTTPException(503, "Vision service is busy", headers={"Retry-After": "1"})
        async with app.state.lock:
            # A disconnected client must not release the inference lock while its thread runs.
            task = asyncio.create_task(asyncio.to_thread(function, argument))
            try:
                return result(await asyncio.shield(task))
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                raise
            except (ValueError, OSError) as error:
                raise HTTPException(400, "Invalid inference input") from error

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"protocol": 1, "model": MODEL_ID, "dimension": DIMENSION, "ready": True}

    @app.post("/embed/text")
    async def text(body: TextInput) -> dict[str, Any]:
        return await infer(app.state.models.text_vectors, body.texts)

    @app.post("/embed/image")
    async def image(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").split(";")[0] not in {
            "image/png",
            "image/jpeg",
            "image/webp",
            "application/octet-stream",
        }:
            raise HTTPException(415, "Expected an image body")

        def encode(data: bytes) -> list[list[float]]:
            return [app.state.models.image_vector(data)]

        return await infer(encode, await request.body())

    return app
