from datetime import UTC, datetime

from reelvault.models import Folder, Video


def test_folder_playlist_order_exclusions_unclassified_and_no_page_limit(client):
    with client.app.state.sessionmaker() as db:
        folder = Folder(name="播放")
        child = Folder(name="子目录")
        db.add_all([folder, child])
        db.flush()
        child.parent_id = folder.id
        created = datetime(2026, 1, 1, tzinfo=UTC)
        db.add_all(
            [
                Video(
                    id=f"item-{i:04}",
                    title=str(i),
                    file_path=f"{i}.mp4",
                    folder_id=folder.id,
                    status="ready",
                    created_at=created,
                )
                for i in range(502)
            ]
        )
        db.add_all(
            [
                Video(id="waiting", title="wait", file_path="wait.mp4", folder_id=folder.id),
                Video(
                    id="trashed",
                    title="trash",
                    file_path="trash.mp4",
                    folder_id=folder.id,
                    status="ready",
                    deleted_at=created,
                ),
                Video(
                    id="child",
                    title="child",
                    file_path="child.mp4",
                    folder_id=child.id,
                    status="ready",
                ),
                Video(id="none", title="none", file_path="none.mp4", status="ready"),
            ]
        )
        db.commit()
    result = client.get("/api/videos/item-0000/playlist")
    assert result.status_code == 200
    data = result.json()
    assert data["name"] == "播放"
    assert [v["id"] for v in data["items"]] == [f"item-{i:04}" for i in range(502)]
    assert [v["id"] for v in client.get("/api/videos/none/playlist").json()["items"]] == ["none"]
    assert client.get("/api/videos/trashed/playlist").status_code == 404
    assert client.get("/api/videos/missing/playlist").status_code == 404
    client.cookies.clear()
    assert client.get("/api/videos/item-0000/playlist").status_code == 401
