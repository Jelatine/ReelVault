from __future__ import annotations

from pathlib import Path

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from reelvault.config import Settings
from reelvault.db import make_engine
from reelvault.main import create_app
from reelvault.migrate import alembic_config, upgrade

from .conftest import HEADERS, login, upload_ready, wait_job, wait_ready
from .test_edit import result_video, run_edit


def test_chain_survives_job_cleanup_and_recreates(
    client: TestClient, samples: dict[str, Path]
) -> None:
    source = upload_ready(client, samples["a"])
    rotated = result_video(client, run_edit(client, source["id"], {"op": "rotate", "angle": 90}))
    muted = result_video(client, run_edit(client, rotated["id"], {"op": "mute"}))
    assert muted["source_video_id"] == rotated["id"]
    assert muted["edit_params"]["op"] == "mute"
    client.delete("/api/jobs")
    history = client.get(f"/api/videos/{muted['id']}/history").json()
    assert [node["id"] for node in history["nodes"]] == [muted["id"], rotated["id"], source["id"]]
    assert history["nodes"][0]["can_recreate"]
    replay = client.post(f"/api/videos/{muted['id']}/recreate")
    assert replay.status_code == 200
    job = wait_job(client, replay.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    recreated = wait_ready(client, job["result_video_id"])
    assert recreated["id"] != muted["id"]
    assert (recreated["width"], recreated["height"], recreated["audio_codec"]) == (240, 320, None)
    client.delete(f"/api/videos/{rotated['id']}?permanent=true")
    node = client.get(f"/api/videos/{muted['id']}/history").json()["nodes"][0]
    assert not node["can_recreate"] and node["sources"][0]["reason"] == "源视频已彻底删除"
    assert client.post(f"/api/videos/{muted['id']}/recreate").status_code == 409


def test_merge_and_replaced_source_versions(client: TestClient, samples: dict[str, Path]) -> None:
    a = upload_ready(client, samples["a"])
    b = upload_ready(client, samples["b"])
    ids = [b["id"], a["id"]]
    merged = result_video(client, run_edit(client, b["id"], {"op": "merge", "video_ids": ids}))
    node = client.get(f"/api/videos/{merged['id']}/history").json()["nodes"][0]
    assert [source["id"] for source in node["sources"]] == ids
    run_edit(client, a["id"], {"op": "rotate", "angle": 90}, {"mode": "replace"})
    wait_ready(client, a["id"])
    trash = client.get("/api/videos", params={"trash": True}).json()["items"]
    backup = wait_ready(client, trash[0]["id"])
    parent = client.get(f"/api/videos/{a['id']}/history").json()["nodes"][0]
    assert parent["sources"][0]["id"] == backup["id"]
    assert parent["can_recreate"]
    node = client.get(f"/api/videos/{merged['id']}/history").json()["nodes"][0]
    assert node["sources"][1]["id"] == backup["id"]
    replay = client.post(f"/api/videos/{merged['id']}/recreate")
    assert replay.status_code == 200
    job = wait_job(client, replay.json()["id"])
    assert job["params"]["edit"]["video_ids"] == [b["id"], backup["id"]]
    assert job["status"] == "succeeded", job["error"]
    video = wait_ready(client, job["result_video_id"])
    assert (video["width"], video["height"]) == (320, 240)
    assert 6.5 < video["duration"] < 7.5


def test_fast_cut_and_embedded_cover_history(client: TestClient, samples: dict[str, Path]) -> None:
    a = upload_ready(client, samples["a"])
    cut = result_video(
        client,
        run_edit(
            client,
            a["id"],
            {
                "op": "trim",
                "mode": "fast",
                "segments": [{"start": 1.15, "end": 2.2}],
            },
        ),
    )
    assert cut["edit_params"]["segments"] == [{"start": 1.0, "end": 3.0}]
    client.post(f"/api/videos/{a['id']}/cover", json={"time": 1.0})
    run_edit(client, a["id"], {"op": "embed_cover"})
    wait_ready(client, a["id"])
    node = client.get(f"/api/videos/{a['id']}/history").json()["nodes"][0]
    wait_ready(client, node["sources"][0]["id"])
    replay = client.post(f"/api/videos/{a['id']}/recreate")
    assert replay.status_code == 200
    job = wait_job(client, replay.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["result_video_id"] != a["id"]
    recreated = wait_ready(client, job["result_video_id"])
    assert recreated["edit_params"]["op"] == "embed_cover"


def test_migration_backfills_old_jobs_without_claiming_source_version(
    settings: Settings,
    samples: dict[str, Path],
) -> None:
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        a = upload_ready(client, samples["a"])
        edited = result_video(client, run_edit(client, a["id"], {"op": "rotate", "angle": 90}))
    engine = make_engine(settings.db_path)
    command.downgrade(alembic_config(str(engine.url)), "0006")
    upgrade(engine)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT source_video_id, edit_params FROM videos WHERE id=:id"),
            {"id": edited["id"]},
        ).one()
        assert row[0] == a["id"] and '"rotate"' in row[1]
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM video_search WHERE video_id=:id"), {"id": edited["id"]}
            ).scalar()
            == 1
        )
    engine.dispose()
    with TestClient(create_app(settings), headers=HEADERS) as client:
        login(client)
        node = client.get(f"/api/videos/{edited['id']}/history").json()["nodes"][0]
        assert not node["can_recreate"]
        assert node["sources"][0]["reason"] == "旧记录未保存源文件版本"
