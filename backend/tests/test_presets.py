from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from reelvault.media import ops

from .conftest import upload_ready, wait_job, wait_ready
from .test_organization import seed_video


def test_presets_validate_persist_update_and_delete(client: TestClient) -> None:
    presets = client.get("/api/edit-presets").json()
    names = {preset["name"] for preset in presets}
    assert {"720p 微信发送", "H.265 归档", "1080p 通用分享", "黑白", "转为 WebM"} <= names
    assert len(names) == 29
    for preset in presets:
        TypeAdapter(ops.EditParams).validate_python(preset["edit"])
    body = {"name": "静音素材", "edit": {"op": "mute"}}
    created = client.post("/api/edit-presets", json=body)
    assert created.status_code == 200
    preset_id = created.json()["id"]
    assert client.post("/api/edit-presets", json=body).status_code == 409
    assert client.post("/api/edit-presets", json={**body, "name": "   "}).status_code == 422
    assert (
        client.post(
            "/api/edit-presets", json={"name": "错误", "edit": {"op": "explode"}}
        ).status_code
        == 422
    )
    renamed = client.put(
        f"/api/edit-presets/{preset_id}",
        json={"name": "倍速", "edit": {"op": "speed", "factor": 2}},
    )
    assert renamed.json()["edit"]["factor"] == 2
    assert (
        client.put(
            f"/api/edit-presets/{preset_id}", json={**body, "name": presets[0]["name"]}
        ).status_code
        == 409
    )
    assert any(p["name"] == "倍速" for p in client.get("/api/edit-presets").json())
    assert client.delete(f"/api/edit-presets/{preset_id}").status_code == 200
    assert client.delete(f"/api/edit-presets/{preset_id}").status_code == 404


def test_batch_validation_is_atomic_and_rejects_merge(client: TestClient) -> None:
    video_id = seed_video(client)
    before = len(client.get("/api/jobs").json())
    response = client.post(
        "/api/jobs/batch", json={"video_ids": [video_id, "missing"], "edit": {"op": "mute"}}
    )
    assert response.status_code == 404
    assert len(client.get("/api/jobs").json()) == before
    for body in (
        {"video_ids": [video_id]},
        {"video_ids": [video_id], "edit": {"op": "mute"}, "preset_id": 1},
        {"video_ids": [], "edit": {"op": "mute"}},
        {"video_ids": [video_id], "edit": {"op": "merge", "video_ids": [video_id, "other"]}},
    ):
        assert client.post("/api/jobs/batch", json=body).status_code == 422
    assert (
        client.post(
            "/api/jobs/batch", json={"video_ids": [video_id], "preset_id": 99999}
        ).status_code
        == 404
    )
    client.delete(f"/api/videos/{video_id}")
    assert (
        client.post("/api/jobs/batch", json={"video_ids": [video_id], "preset_id": 1}).status_code
        == 404
    )


def test_batch_preset_creates_one_successful_job_per_video(
    client: TestClient, samples: dict[str, Path]
) -> None:
    a, b = upload_ready(client, samples["a"]), upload_ready(client, samples["b"])
    preset = client.post(
        "/api/edit-presets", json={"name": "统一静音", "edit": {"op": "mute"}}
    ).json()
    response = client.post(
        "/api/jobs/batch",
        json={"video_ids": [a["id"], b["id"], a["id"]], "preset_id": preset["id"]},
    )
    assert response.status_code == 200, response.text
    jobs = response.json()
    assert len(jobs) == 2
    assert [job["video_ids"] for job in jobs] == [[a["id"]], [b["id"]]]
    for job in jobs:
        done = wait_job(client, job["id"])
        assert done["status"] == "succeeded", done["error"]
        result = wait_ready(client, done["result_video_id"])
        assert result["audio_codec"] is None
        assert done["params"]["edit"] == {"op": "mute"}
    assert client.get(f"/api/videos/{a['id']}").json()["audio_codec"] == "aac"


def test_batch_accepts_direct_params_and_replace_mode(
    client: TestClient, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, samples["a"])
    response = client.post(
        "/api/jobs/batch",
        json={
            "video_ids": [video["id"]],
            "edit": {"op": "rotate", "angle": 90},
            "output": {"mode": "replace"},
        },
    )
    assert response.status_code == 200, response.text
    done = wait_job(client, response.json()[0]["id"])
    assert done["status"] == "succeeded", done["error"]
    assert done["result_video_id"] == video["id"]
    result = wait_ready(client, video["id"])
    assert result["width"] == 240
    assert client.get("/api/videos?trash=true").json()["total"] == 1
