from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from typing import Any

IMAGE_MODEL = "sentence-transformers/clip-ViT-B-32"
IMAGE_REVISION = "327ab6726d33c0e22f920c83f2ff9e4bd38ca37f"
TEXT_MODEL = "sentence-transformers/clip-ViT-B-32-multilingual-v1"
TEXT_REVISION = "58edf8cada9e398793dca955574a48cbb7f18be2"
DIMENSION = 512
MODEL_ID = (
    "clip-b32-multilingual-"
    + hashlib.sha256(f"{IMAGE_REVISION}:{TEXT_REVISION}:normalized-f32-v1".encode()).hexdigest()[
        :24
    ]
)
MAX_IMAGE_BYTES = 1024 * 1024
MAX_PIXELS = 1024 * 1024


def decode_image(data: bytes) -> Any:
    from PIL import Image, ImageOps

    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be between 1 byte and 1 MiB")
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Only static PNG, JPEG and WebP images are supported")
        width, height = image.size
        if min(width, height) <= 0 or max(width, height) > 1024 or width * height > MAX_PIXELS:
            raise ValueError("Image dimensions must not exceed 1024×1024")
        oriented = ImageOps.exif_transpose(image)
        try:
            return oriented.convert("RGB")
        finally:
            oriented.close()


def snapshot(cache: Path, model: str, revision: str, download: bool = False) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(
        model,
        revision=revision,
        cache_dir=str(cache),
        token=False,
        local_files_only=not download,
        allow_patterns=["*.json", "*.txt", "*.safetensors"],
    )


def prepare(cache: Path) -> None:
    """The only operation allowed to download public model weights."""
    snapshot(cache, IMAGE_MODEL, IMAGE_REVISION, download=True)
    snapshot(cache, TEXT_MODEL, TEXT_REVISION, download=True)


def normalized(values: Any) -> list[float]:
    result = [float(value) for value in values]
    if len(result) != DIMENSION or not all(math.isfinite(value) for value in result):
        raise ValueError("Invalid model embedding")
    norm = math.sqrt(sum(value * value for value in result))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("Empty model embedding")
    return [value / norm for value in result]


class Models:
    def __init__(self, cache: Path, threads: int = 2):
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        self.image = SentenceTransformer(
            snapshot(cache, IMAGE_MODEL, IMAGE_REVISION),
            device="cpu",
            trust_remote_code=False,
            local_files_only=True,
            token=False,
            model_kwargs={"use_safetensors": True},
        )
        self.text = SentenceTransformer(
            snapshot(cache, TEXT_MODEL, TEXT_REVISION),
            device="cpu",
            trust_remote_code=False,
            local_files_only=True,
            token=False,
            model_kwargs={"use_safetensors": True},
        )

    def text_vectors(self, texts: list[str]) -> list[list[float]]:
        return [
            normalized(vector)
            for vector in self.text.encode(texts, batch_size=8, show_progress_bar=False)
        ]

    def image_vector(self, data: bytes) -> list[float]:
        with decode_image(data) as rgb:
            return normalized(self.image.encode(rgb, show_progress_bar=False))
