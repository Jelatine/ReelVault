"""Controlled CLIP suggestions and complete-link face matching in a private snapshot."""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

from ..ai import FACE_MODEL_ID, AiParams, face_embedding, face_values, request
from ..models import new_id
from ..vector_sql import register_vectors
from ..vision import MODEL_ID, VisionClient, vector


def progress(value: float) -> None:
    print(f"out_time_us={int(value * 1000000)}\nprogress=continue", flush=True)


def analyse(params: dict) -> dict:
    body = AiParams.model_validate(params["body"])
    client = VisionClient(params["url"], params["token"])
    health = client.request("/health")
    if body.faces and (
        health.get("face_model") != FACE_MODEL_ID or health.get("face_dimension") != 128
    ):
        raise ValueError("人脸模型未启用或版本不兼容")
    labels: list[tuple[str, list[float]]] = []
    for offset in range(0, len(body.candidates), 16):
        names = body.candidates[offset : offset + 16]
        result = request(client, "/embed/text", json.dumps({"texts": names}).encode())
        vectors = result.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(names):
            raise ValueError("Invalid scene label vectors")
        labels.extend(zip(names, [vector(v) for v in vectors], strict=True))
        progress(0.2 * min(offset + 16, len(body.candidates)) / len(body.candidates))
    best: dict[str, dict[str, Any]] = {}
    found = []
    for position, frame in enumerate(params["frames"]):
        values = struct.unpack("<512f", base64.b64decode(frame["embedding"], validate=True))
        for name, label in labels:
            score = sum(a * b for a, b in zip(values, label, strict=True))
            if name not in best or score > best[name]["score"]:
                best[name] = {
                    "name": name,
                    "score": max(-1, min(1, score)),
                    "frame_id": frame["id"],
                    "time": frame["time"],
                }
        if body.faces:
            path = Path(params["frames_dir"]) / f"{frame['ordinal']:04d}.jpg"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise ValueError("索引缩略图缺失，请重新生成画面索引")
            result = request(client, "/embed/faces", path.read_bytes(), "image/jpeg")
            items = result.get("faces")
            if (
                result.get("face_model") != FACE_MODEL_ID
                or result.get("face_dimension") != 128
                or not isinstance(items, list)
                or len(items) > 16
            ):
                raise ValueError("Invalid face service response")
            for ordinal, item in enumerate(items):
                found.append(
                    {
                        **face_values(item),
                        "id": new_id(),
                        "frame_id": frame["id"],
                        "ordinal": ordinal,
                    }
                )
        progress(0.2 + 0.6 * (position + 1) / len(params["frames"]))
    suggestions = sorted(best.values(), key=lambda row: (-row["score"], row["name"]))
    return {
        "model": MODEL_ID,
        "face_model": FACE_MODEL_ID if body.faces else None,
        "suggestions": [row for row in suggestions if row["score"] >= body.min_score][
            : body.max_tags
        ],
        "faces": found,
    }


def cluster(params: dict, data: dict) -> None:
    body = AiParams.model_validate(params["body"])
    new_groups = []
    with sqlite3.connect(params["snapshot"]) as db:
        register_vectors(db)
        for position, face in enumerate(data["faces"]):
            values = face_embedding(face["vector"])
            frame_key = f"{params['video_id']}:{params['generation']}:{face['frame_id']}"
            fixed = db.execute(
                "SELECT group_id, ignored FROM fixed WHERE frame_id=? AND ordinal=?",
                (face["frame_id"], face["ordinal"]),
            ).fetchone()
            group = None
            face["manual"], face["ignored"] = bool(fixed), bool(fixed and fixed[1])
            if fixed:
                group = fixed[0]
            else:
                candidates = db.execute(
                    "SELECT group_id, vec_distance_cosine(prototype, ?) AS distance FROM groups "
                    "WHERE distance <= ? ORDER BY distance, group_id",
                    (values, 1 - body.face_threshold),
                ).fetchall()
                for candidate, _ in candidates:
                    if db.execute(
                        "SELECT 1 FROM refs WHERE group_id=? AND frame_key=? LIMIT 1",
                        (candidate, frame_key),
                    ).fetchone():
                        continue
                    maximum = db.execute(
                        "SELECT max(vec_distance_cosine(embedding, ?)) FROM refs WHERE group_id=?",
                        (values, candidate),
                    ).fetchone()[0]
                    if maximum is not None and maximum <= 1 - body.face_threshold + 1e-6:
                        group = candidate
                        break
                if group is None:
                    group = new_id()
                    new_groups.append(group)
                    db.execute("INSERT INTO groups VALUES (?, ?)", (group, values))
            face["group_id"] = group
            if group and not face["ignored"]:
                db.execute(
                    "INSERT INTO refs VALUES (?, ?, ?, ?)", (face["id"], group, frame_key, values)
                )
            progress(0.8 + 0.19 * (position + 1) / max(1, len(data["faces"])))
    data["new_groups"] = new_groups


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cluster-only", action="store_true")
    args = parser.parse_args()
    try:
        params = json.loads(args.manifest.read_text())
        data = json.loads(args.output.read_text()) if args.cluster_only else analyse(params)
        cluster(params, data)
        args.output.write_text(json.dumps(data))
        progress(1)
    except Exception as error:
        import sys

        from ..errors import APIError

        print(str(error.detail) if isinstance(error, APIError) else str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
