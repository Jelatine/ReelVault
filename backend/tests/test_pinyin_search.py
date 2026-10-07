import shutil
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from reelvault import pinyin_search
from reelvault.backup import create_backup, restore_backup
from reelvault.config import Settings
from reelvault.db import make_engine
from reelvault.main import create_app
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import Tag, Video
from reelvault.pinyin_search import normalize_pinyin_query, title_pinyin

from .conftest import HEADERS, login, upload_ready
from .test_organization import seed_video
from .test_search import result_ids


@pytest.mark.parametrize(
    "query",
    [
        "lvxingriluo",
        "LVXINGRILUO",
        "lv xing ri luo",
        "lxrl",
        "lx",
        "lǚxíng",
        "lüxing",
        "lu:xing",
        "lvxing 日落",
    ],
)
def test_full_spelling_syllables_initials_and_umlaut(client: TestClient, query: str) -> None:
    video = seed_video(client, "旅行日落")
    response = client.get("/api/videos", params={"q": query}).json()
    assert [v["id"] for v in response["items"]] == [video]
    assert response["items"][0]["title"] == "旅行日落"
    assert response["items"][0]["search_pinyin"] is True
    assert response["items"][0]["search_excerpt"] == "旅行日落"


def test_phrase_reading_traditional_and_mixed_titles_keep_plain_search(client: TestClient) -> None:
    chongqing = seed_video(client, "重庆音乐")
    taiwan = seed_video(client, "臺灣旅遊")
    mixed = seed_video(client, "GoPro 2024 旅行")
    other = seed_video(client, "另一部")
    client.patch(f"/api/videos/{other}", json={"tags": ["旅行"], "description": "旅行"})
    with client.app.state.sessionmaker() as db:
        db.get(Video, other).original_name = "旅行.mp4"
        db.commit()
    assert result_ids(client, q="chongqing yinyue") == [chongqing]
    assert result_ids(client, q="cqyy") == [chongqing]
    assert result_ids(client, q="taiwanlvyou") == [taiwan]
    assert result_ids(client, q="twly") == [taiwan]
    assert result_ids(client, q="gopro2024lx") == [mixed]
    assert result_ids(client, q="lvxing") == [mixed]  # Only title pinyin is indexed.
    assert set(result_ids(client, q="旅行")) == {mixed, other}
    assert result_ids(client, q="2024") == [mixed]
    assert title_pinyin("Movie Trailer") == ("", "")
    assert normalize_pinyin_query("lǚxíng") == "lvxing"
    assert normalize_pinyin_query("l\u0075\u0308\u030cxíng") == "lvxing"
    for value in ("旅行", "%", "a_b", "*", "2024"):
        assert normalize_pinyin_query(value) is None


def test_literal_results_rank_first_and_pinyin_can_use_filters_saved_rules_and_pagination(
    client: TestClient,
) -> None:
    phonetic = seed_video(client, "旅行")
    literal = seed_video(client, "lvxing")
    client.patch(f"/api/videos/{phonetic}", json={"rating": 4, "tags": ["海"]})
    assert result_ids(client, q="lvxing") == [literal, phonetic]
    page = client.get("/api/videos", params={"q": "lvxing", "page_size": 1, "page": 2}).json()
    assert page["total"] == 2 and page["items"][0]["id"] == phonetic
    assert result_ids(client, q="lvxing tag:海 rating:>=4") == [phonetic]
    saved = client.post(
        "/api/smart-folders", json={"name": "拼音旅行", "filters": {"q": "lvxing tag:海"}}
    ).json()
    url = f"/api/smart-folders/{saved['id']}/videos"
    assert client.get(url).json()["items"][0]["id"] == phonetic
    client.patch(f"/api/videos/{phonetic}", json={"title": "家庭"})
    assert client.get(url).json()["total"] == 0
    assert result_ids(client, q="jiating") == [phonetic]


def test_title_trigger_raw_updates_rollback_tag_changes_and_delete_remain_consistent(
    client: TestClient,
) -> None:
    video = seed_video(client, "旅行")
    client.patch(f"/api/videos/{video}", json={"tags": ["标签", "合并"]})
    tags = {t["name"]: t["id"] for t in client.get("/api/tags").json()}
    client.patch(f"/api/tags/{tags['标签']}", json={"name": "新标签"})
    client.post(f"/api/tags/{tags['标签']}/merge", json={"target_id": tags["合并"]})
    assert result_ids(client, q="lvxing tag:合并") == [video]
    with client.app.state.sessionmaker() as db:
        db.execute(text("UPDATE videos SET title='重庆' WHERE id=:id"), {"id": video})
        db.rollback()
    assert result_ids(client, q="lvxing") == [video]
    with client.app.state.sessionmaker() as db:
        db.execute(text("UPDATE videos SET title='重庆' WHERE id=:id"), {"id": video})
        db.commit()
    assert result_ids(client, q="lvxing") == []
    assert result_ids(client, q="chongqing") == [video]
    client.delete(f"/api/videos/{video}")
    assert result_ids(client, q="cq") == []
    assert result_ids(client, q="cq", trash=True) == [video]
    client.post(f"/api/videos/{video}/restore")
    assert result_ids(client, q="cq") == [video]
    client.delete(f"/api/videos/{video}?permanent=true")
    assert result_ids(client, q="cq", trash=True) == []
    with client.app.state.sessionmaker() as db:
        assert (
            db.scalar(text("SELECT count(*) FROM video_search WHERE video_id=:id"), {"id": video})
            == 0
        )


def test_queries_read_persisted_index_without_transliterating_titles(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = seed_video(client, "旅行日落")

    def unexpected(title):
        raise AssertionError("Read queries must not regenerate title pinyin")

    monkeypatch.setattr(pinyin_search, "title_pinyin", unexpected)
    assert result_ids(client, q="lxrl") == [video]
    assert result_ids(client, q="lvxingriluo") == [video]


def test_upgrade_backfills_existing_titles_and_downgrade_preserves_original_fields(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path / "legacy.db")
    cfg = alembic_config(str(engine.url))
    command.upgrade(cfg, "0022")
    with Session(engine) as db:
        video = Video(
            id="legacy",
            title="旅行日落",
            file_path="library/old.mp4",
            description="旧库描述",
            status="ready",
        )
        video.tags = [Tag(name="旧标签")]
        db.add(video)
        db.commit()
    upgrade(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT title, description, tags, title_pinyin, title_initials FROM video_search")
        ).one()
        assert tuple(row) == (
            "旅行日落",
            "旧库描述",
            "旧标签",
            "lvxingriluo lv xing ri luo",
            "lxrl",
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM video_search WHERE video_search MATCH 'lxrl'"))
            == 1
        )
    command.downgrade(cfg, "0022")
    with engine.begin() as conn:
        assert len(conn.execute(text("PRAGMA table_info(video_search)")).all()) == 5
        conn.execute(text("UPDATE videos SET title='重庆音乐' WHERE id='legacy'"))
        assert conn.scalar(text("SELECT title FROM video_search")) == "重庆音乐"
        assert conn.scalar(text("SELECT tags FROM video_search")) == "旧标签"
    # The direct Alembic entry point also registers trigger functions on its connection.
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT title_initials FROM video_search")) == "cqyy"
    engine.dispose()


def test_metadata_backup_restore_keeps_and_updates_pinyin_index(
    client: TestClient, settings: Settings, tmp_path: Path, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, samples["a"])["id"]
    client.patch(f"/api/videos/{video}", json={"title": "旅行日落"})
    archive = create_backup(settings, tmp_path / "pinyin.zip")
    restored = settings.model_copy(update={"data_dir": tmp_path / "restored"})
    for name in ("library", "derived", "assets"):
        shutil.copytree(settings.data_dir / name, restored.data_dir / name, dirs_exist_ok=True)
    restore_backup(archive, restored)
    with TestClient(create_app(restored), headers=HEADERS) as other:
        login(other)
        assert result_ids(other, q="lxrl") == [video]
        other.patch(f"/api/videos/{video}", json={"title": "重庆音乐"})
        assert result_ids(other, q="chongqing") == [video]
        assert result_ids(other, q="lxrl") == []


def test_literal_unicode_case_matches_are_not_labeled_as_pinyin(client: TestClient) -> None:
    first = seed_video(client, "Café")
    second = seed_video(client, "CAFÉ旅行")
    response = client.get("/api/videos", params={"q": "café"}).json()
    assert {v["id"] for v in response["items"]} == {first, second}
    assert all(not v["search_pinyin"] for v in response["items"])
