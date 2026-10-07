from __future__ import annotations

from datetime import UTC, datetime

import pytest

from reelvault.models import Video

from .conftest import upload_ready, wait_ready
from .test_edit import run_edit
from .test_metadata import tagged_sample


def seed(client, title, *, date=None, make=None, model=None, width=1920, height=1080, **fields):
    meta = {"MAKE": make, "Com.Apple.QuickTime.Model": model}
    with client.app.state.sessionmaker() as db:
        video = Video(
            title=title,
            file_path=f"library/{title}.mp4",
            status="ready",
            captured_at=date,
            meta=meta,
            width=width,
            height=height,
            **fields,
        )
        db.add(video)
        db.commit()
        return video.id


def test_auto_catalog_dates_devices_shapes_filters_and_dynamic_changes(client):
    a = seed(
        client,
        "portrait 4K",
        date=datetime(2024, 1, 1, tzinfo=UTC),
        make=" Apple ",
        model=" Phone ",
        width=2160,
        height=3840,
        rating=5,
    )
    b = seed(
        client, "landscape", date=datetime(2024, 2, 1, tzinfo=UTC), make="Apple", model="Phone"
    )
    unknown = seed(client, "unknown", width=0, height=0)
    square = seed(
        client,
        "square",
        date=datetime(2023, 12, 31, tzinfo=UTC),
        model="Phone",
        width=100,
        height=100,
    )
    ignored = seed(client, "deleted", deleted_at=datetime.now(UTC))
    assert (
        client.get("/api/videos", params={"auto": "date:unknown", "trash": True}).json()["total"]
        == 0
    )
    processing = seed(client, "processing")
    with client.app.state.sessionmaker() as db:
        db.get(Video, processing).status = "processing"
        db.commit()
    response = client.get("/api/auto-groups")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 4
    assert [(g["group"]["value"], g["group"]["count"]) for g in result["years"]] == [
        ("2024", 2),
        ("2023", 1),
    ]
    assert [g["value"] for g in result["years"][0]["months"]] == ["2024-02", "2024-01"]
    device = next(g for g in result["devices"] if g["device_make"] == "Apple")
    assert device["device_model"] == "Phone" and device["count"] == 2
    assert len(result["devices"]) == 3
    assert {g["value"]: g["count"] for g in result["resolutions"]} == {
        "portrait": 1,
        "landscape": 1,
        "square": 1,
        "4k": 1,
        "unknown": 1,
    }
    assert result["unknown_date"]["count"] == 1
    for key, expected in [
        ("year:2024", {a, b}),
        ("month:2024-01", {a}),
        (device["key"], {a, b}),
        ("date:unknown", {unknown}),
        ("resolution:square", {square}),
        ("resolution:4k", {a}),
    ]:
        rows = client.get("/api/videos", params={"auto": key}).json()
        assert {v["id"] for v in rows["items"]} == expected
        assert client.get(f"/api/auto-groups/{key}").json()["count"] == len(expected)
    assert (
        client.get(
            "/api/videos", params={"auto": "year:2024", "q": "portrait", "rating_min": 5}
        ).json()["total"]
        == 1
    )
    assert (
        client.get("/api/videos", params={"auto": device["key"], "page_size": 1, "page": 2}).json()[
            "total"
        ]
        == 2
    )
    # Group membership changes immediately; a saved classifier never becomes all videos.
    client.patch(
        f"/api/videos/{a}/metadata",
        json={
            "captured_at": "2025-01-01T00:30:00+08:00",
            "device_make": None,
            "device_model": None,
        },
    )
    assert client.get("/api/videos", params={"auto": "year:2025"}).json()["total"] == 0
    assert client.get("/api/videos", params={"auto": "month:2024-12"}).json()["total"] == 1
    assert client.get(f"/api/auto-groups/{device['key']}").json()["count"] == 1
    assert client.get("/api/auto-groups/device:unknown").json()["count"] == 2
    client.delete(f"/api/videos/{b}")
    assert client.get(f"/api/auto-groups/{device['key']}").json()["count"] == 0
    assert client.get("/api/videos", params={"auto": device["key"]}).json()["total"] == 0
    assert client.post(f"/api/videos/{b}/restore").status_code == 200
    assert client.get(f"/api/auto-groups/{device['key']}").json()["count"] == 1
    assert client.get(f"/api/videos/{a}").json()["folder_id"] is None
    assert ignored not in {
        v["id"] for v in client.get("/api/videos", params={"auto": "date:unknown"}).json()["items"]
    }


def test_device_classifier_matches_effective_metadata_aliases_clear_and_unicode(client):
    import json

    from reelvault.metadata import device_key

    a = seed(client, "unicode")
    with client.app.state.sessionmaker() as db:
        row = db.get(Video, a)
        row.meta = {
            "MODEL": " \t相机/型号\u3000",
            "camera_model": "ignored",
            "MANUFACTURER": "厂商:名称",
            "model": "",
        }
        # source_metadata lowercases the keys, with the last same-case spelling winning.
        row.meta.pop("model")
        db.commit()
    effective = client.get(f"/api/videos/{a}").json()["metadata"]
    assert effective["device_model"] == "相机/型号"
    catalog = client.get("/api/auto-groups").json()
    device = catalog["devices"][0]
    assert device["device_model"] == effective["device_model"]
    assert device["device_make"] == effective["device_make"]
    old_key = device["key"]
    client.patch(f"/api/videos/{a}/metadata", json={"device_model": None})
    assert client.get(f"/api/auto-groups/{old_key}").json()["count"] == 0
    current = client.get("/api/auto-groups").json()["devices"][0]
    assert current["device_model"] is None and current["device_make"] == "厂商:名称"
    assert current["key"] == "device:" + device_key(
        json.dumps({"make": "厂商:名称"}), json.dumps({"device_model": None})
    )


@pytest.mark.parametrize(
    "key",
    [
        "year:0000",
        "year:10000",
        "year:２０２４",
        "month:2024-13",
        "month:2024-00",
        "month:2024-1",
        "device:bad",
        "resolution:all",
        "date:all",
        "unknown",
        "",
    ],
)
def test_invalid_auto_rules_never_broaden_results(client, key):
    seed(client, "existing")
    response = client.get("/api/videos", params={"auto": key})
    assert response.status_code == 400 and response.json()["code"] == "auto_group_invalid"
    response = client.post("/api/smart-folders", json={"name": "invalid", "filters": {"auto": key}})
    assert response.status_code == 400 and response.json()["code"] == "auto_group_invalid"


def test_saved_auto_rules_auth_empty_catalog_and_real_edit_pipeline(client, samples, tmp_path):
    assert client.get("/api/auto-groups").json() == {
        "years": [],
        "devices": [],
        "resolutions": [],
        "unknown_date": None,
        "total": 0,
    }
    video = upload_ready(client, tagged_sample(samples, tmp_path))
    assert client.get("/api/auto-groups/month:2024-06").json()["count"] == 1
    saved = client.post(
        "/api/smart-folders",
        json={"name": "dated capture", "filters": {"auto": "month:2024-06", "sort": "captured"}},
    ).json()
    assert client.get(f"/api/smart-folders/{saved['id']}/videos").json()["total"] == 1
    job = run_edit(client, video["id"], {"op": "rotate", "angle": 90})
    new = wait_ready(client, job["result_video_id"])
    assert client.get(f"/api/smart-folders/{saved['id']}/videos").json()["total"] == 2
    assert new["height"] > new["width"]
    assert new["id"] in {
        v["id"]
        for v in client.get("/api/videos", params={"auto": "resolution:portrait"}).json()["items"]
    }
    client.patch(f"/api/videos/{video['id']}/metadata", json={"captured_at": None})
    assert client.get(f"/api/smart-folders/{saved['id']}/videos").json()["total"] == 1
    assert client.get("/api/auto-groups/date:unknown").json()["count"] == 1
    client.cookies.clear()
    assert client.get("/api/auto-groups").status_code == 401
    assert client.get("/api/auto-groups/year:2024").status_code == 401
    assert client.get("/api/videos", params={"auto": "year:2024"}).status_code == 401
