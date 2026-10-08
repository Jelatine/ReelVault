"""Optional offline YuNet/SFace models with pinned, verified public weights."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from .model import decode_image

REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
WEIGHTS = (
    (
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        232589,
    ),
    (
        "face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        38696353,
    ),
)
FACE_DIMENSION = 128
FACE_MODEL_ID = (
    "yunet-sface-"
    + hashlib.sha256(
        (":".join(item[1] for item in WEIGHTS) + ":aligned-normalized-f32-v1").encode()
    ).hexdigest()[:24]
)
MAX_FACES = 16


def verified(path: Path, digest: str, size: int) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        return False
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest() == digest


def prepare_faces(cache: Path) -> None:
    """Explicit deployment action; serving never invokes this downloader."""
    folder = cache / "faces"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("YuNet-MIT.txt", "SFace-Apache-2.0.txt"):
        shutil.copyfile(Path(__file__).parent / "licenses" / name, folder / name)
    for name, digest, size in WEIGHTS:
        target = folder / Path(name).name
        if verified(target, digest, size):
            continue
        url = (
            f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/models/{name}"
        )
        descriptor, temporary = tempfile.mkstemp(prefix=".prepare-", dir=folder)
        temp = Path(temporary)
        try:
            with (
                os.fdopen(descriptor, "wb") as output,
                urllib.request.urlopen(url, timeout=30) as response,
            ):
                remaining = size
                while remaining:
                    chunk = response.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Incomplete face model download")
                    output.write(chunk)
                    remaining -= len(chunk)
                if response.read(1):
                    raise ValueError("Face model exceeds expected size")
            if not verified(temp, digest, size):
                raise ValueError("Face model checksum mismatch")
            os.chmod(temp, 0o644)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)


def normalized_face(values: Any) -> list[float]:
    result = [float(value) for value in values]
    if len(result) != FACE_DIMENSION or not all(math.isfinite(value) for value in result):
        raise ValueError("Invalid face embedding")
    norm = math.sqrt(sum(value * value for value in result))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("Empty face embedding")
    return [value / norm for value in result]


class Faces:
    def __init__(self, cache: Path, threads: int = 2):
        paths = []
        for name, digest, size in WEIGHTS:
            path = cache / "faces" / Path(name).name
            if not verified(path, digest, size):
                raise ValueError("Missing or invalid face models; run --prepare-faces explicitly")
            paths.append(str(path))
        import cv2

        cv2.setNumThreads(threads)
        self.detector = cv2.FaceDetectorYN.create(
            paths[0],
            "",
            (320, 320),
            0.85,
            0.3,
            5000,
            cv2.dnn.DNN_BACKEND_OPENCV,
            cv2.dnn.DNN_TARGET_CPU,
        )
        self.recognizer = cv2.FaceRecognizerSF.create(
            paths[1], "", cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU
        )

    def detect(self, data: bytes) -> list[dict[str, Any]]:
        import cv2
        import numpy as np

        with decode_image(data) as image:
            pixels = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        height, width = pixels.shape[:2]
        if min(width, height) < 24:
            return []
        self.detector.setInputSize((width, height))
        _, detected = self.detector.detect(pixels)
        result = []
        if detected is None:
            return result
        for face in sorted(detected, key=lambda f: float(f[-1]), reverse=True):
            if len(result) >= MAX_FACES:
                break
            if not np.isfinite(face).all():
                continue
            x, y, w, h = (float(value) for value in face[:4])
            left, top = max(0.0, x), max(0.0, y)
            right, bottom = min(float(width), x + w), min(float(height), y + h)
            if right - left < 24 or bottom - top < 24:
                continue
            aligned = self.recognizer.alignCrop(pixels, face)
            embedding = normalized_face(self.recognizer.feature(aligned).reshape(-1))
            result.append(
                {
                    "box": [
                        left / width,
                        top / height,
                        (right - left) / width,
                        (bottom - top) / height,
                    ],
                    "score": float(face[-1]),
                    "vector": embedding,
                }
            )
        return result
