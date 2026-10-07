"""Fixtures for historical schemas, independent of current ORM defaults."""

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection


def insert_legacy_video(db: Connection, title: str, path: str, status: str = "processing") -> str:
    video_id = uuid4().hex
    db.execute(
        text(
            "INSERT INTO videos (id,title,description,original_name,file_path,status,"
            "size,duration,width,height,fps,bitrate,container,video_codec,meta,"
            "has_poster,has_preview,has_sprite,asset_version,created_at,updated_at,"
            "rating,favorite,edit_sources) VALUES "
            "(:id,:title,'','',:path,:status,0,0,0,0,0,0,'','','{}',0,0,0,1,"
            "'2024-01-01','2024-01-01',0,0,'[]')"
        ),
        {"id": video_id, "title": title, "path": path, "status": status},
    )
    return video_id
