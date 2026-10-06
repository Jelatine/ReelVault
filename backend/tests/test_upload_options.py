from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from .conftest import wait_ready


def test_folder_upload_options_persist_and_share_hierarchy(client, samples):
    target = client.post("/api/folders", json={"name": "target"}).json()["id"]
    data = samples["a"].read_bytes()
    ids = []
    for relative in ("Trip/day1/clip.mp4", "Trip/day1/other.mp4", "Trip/day2/clip.mp4"):
        result = client.post(
            "/api/uploads",
            json={
                "filename": relative.split("/")[-1],
                "size": len(data),
                "folder_id": target,
                "relative_path": relative,
                "tags": [" travel ", "travel", "family"],
            },
        )
        assert result.status_code == 200, result.text
        upload = result.json()
        assert client.get(f"/api/uploads/{upload['id']}").json()["relative_path"] == relative
        assert upload["tags"] == ["family", "travel"]
        assert client.put(f"/api/uploads/{upload['id']}?offset=0", content=data).status_code == 200
        ids.append(upload["id"])
    # No folders are left behind merely by initiating or sending an upload.
    assert len(client.get("/api/folders").json()) == 1
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda id: client.post(f"/api/uploads/{id}/complete"), ids))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    videos = [wait_ready(client, r.json()["id"]) for r in responses]
    assert all(v["tags"] == ["family", "travel"] for v in videos)
    folders = client.get("/api/folders").json()
    assert len(folders) == 4
    trip = next(f for f in folders if f["name"] == "Trip")
    assert trip["parent_id"] == target
    assert videos[0]["folder_id"] == videos[1]["folder_id"] != videos[2]["folder_id"]
    assert all(f["parent_id"] == trip["id"] for f in folders if f["name"].startswith("day"))


@pytest.mark.parametrize(
    "relative",
    [
        "/a.mp4",
        "../a.mp4",
        "Trip/../a.mp4",
        "Trip//a.mp4",
        "Trip/./a.mp4",
        "C:\\Trip\\a.mp4",
        "Trip/other.mp4",
        "Trip/\x00/a.mp4",
        "x/" * 33 + "a.mp4",
    ],
)
def test_upload_rejects_unsafe_paths(client, relative):
    assert (
        client.post(
            "/api/uploads", json={"filename": "a.mp4", "size": 8, "relative_path": relative}
        ).status_code
        == 422
    )
    assert client.get("/api/folders").json() == []


def test_cancel_and_deleted_destination(client, samples):
    folder = client.post("/api/folders", json={"name": "destination"}).json()["id"]
    data = samples["a"].read_bytes()
    body = {
        "filename": "a.mp4",
        "size": len(data),
        "folder_id": folder,
        "relative_path": "Trip/a.mp4",
        "tags": ["family"],
    }
    up = client.post("/api/uploads", json=body).json()["id"]
    assert client.delete(f"/api/uploads/{up}").status_code == 200
    assert client.get(f"/api/uploads/{up}").status_code == 404
    up = client.post("/api/uploads", json=body).json()["id"]
    assert client.put(f"/api/uploads/{up}?offset=0", content=data).status_code == 200
    assert client.delete(f"/api/folders/{folder}").status_code == 200
    assert client.post(f"/api/uploads/{up}/complete").status_code == 409
    assert client.get("/api/folders").json() == []
    assert client.get(f"/api/uploads/{up}").json()["received"] == len(data)


def test_upload_options_require_login(anon: TestClient):
    assert (
        anon.post(
            "/api/uploads",
            json={
                "filename": "a.mp4",
                "size": 8,
                "relative_path": "Trip/a.mp4",
                "tags": ["private"],
            },
        ).status_code
        == 401
    )


def test_failed_completion_restores_part_and_rolls_back_folders(client, samples, monkeypatch):
    data = samples["a"].read_bytes()
    upload = client.post(
        "/api/uploads",
        json={
            "filename": "a.mp4",
            "size": len(data),
            "relative_path": "Trip/day1/a.mp4",
            "tags": ["family"],
        },
    ).json()["id"]
    client.put(f"/api/uploads/{upload}?offset=0", content=data)

    def fail(*args, **kwargs):
        raise RuntimeError("queue unavailable")

    with monkeypatch.context() as m:
        m.setattr(client.app.state.jobs, "submit", fail)
        with pytest.raises(RuntimeError, match="queue unavailable"):
            client.post(f"/api/uploads/{upload}/complete")
    assert client.get("/api/folders").json() == []
    assert client.get("/api/videos").json()["total"] == 0
    assert client.get(f"/api/uploads/{upload}").json()["received"] == len(data)
    part = client.app.state.settings.tmp_dir / f"upload-{upload}.part"
    assert part.read_bytes() == data
    assert client.post(f"/api/uploads/{upload}/complete").status_code == 200
