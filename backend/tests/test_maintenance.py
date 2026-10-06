from datetime import timedelta
from pathlib import Path

from reelvault.config import Settings
from reelvault.db import make_engine, make_sessionmaker
from reelvault.maintenance import purge_expired_trash
from reelvault.migrate import upgrade
from reelvault.models import Job, Video, utcnow


def test_retention_purges_only_expired_unused_media(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, trash_retention_days=30)
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as db:
        videos = []
        for index, age in enumerate((31, 29, None, 60)):
            path = settings.library_dir / f"{index}.mp4"
            path.write_bytes(b"video")
            video = Video(
                title=str(index),
                file_path=f"library/{index}.mp4",
                deleted_at=utcnow() - timedelta(days=age) if age else None,
            )
            db.add(video)
            videos.append(video)
        db.commit()
        ids = [video.id for video in videos]
        derived = settings.derived_dir / ids[0]
        derived.mkdir()
        (derived / "poster.jpg").write_bytes(b"poster")
        db.add(Job(kind="edit", status="queued", video_ids=[ids[3]]))
        db.commit()
    assert purge_expired_trash(settings, sessions) == 1
    assert not (settings.library_dir / "0.mp4").exists()
    assert not derived.exists()
    with sessions() as db:
        assert db.get(Video, ids[0]) is None
        assert all(db.get(Video, video_id) for video_id in ids[1:])
        job = db.query(Job).one()
        job.status = "paused"
        db.commit()
    assert purge_expired_trash(settings, sessions) == 0
    with sessions() as db:
        job = db.query(Job).one()
        job.status = "succeeded"
        db.commit()
    settings.trash_retention_days = 0
    assert purge_expired_trash(settings, sessions) == 0
    assert (settings.library_dir / "3.mp4").exists()
    settings.trash_retention_days = 30
    assert purge_expired_trash(settings, sessions) == 1
    assert (settings.library_dir / "1.mp4").exists()
    assert (settings.library_dir / "2.mp4").exists()
    engine.dispose()
