"""More built-in edit presets covering everyday compress, rotate, speed, audio and look edits."""

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

PRESETS: list[tuple[str, dict[str, Any]]] = [
    ("1080p 通用分享", {"op": "compress", "codec": "h264", "resolution": 1080, "quality": "medium"}),
    ("480p 省流量", {"op": "compress", "codec": "h264", "resolution": 480, "max_fps": 30, "quality": "low", "audio_bitrate": 96}),
    ("4K H.265 高画质", {"op": "compress", "codec": "h265", "resolution": 2160, "quality": "high", "preset": "slow"}),
    ("邮件附件 20 MB", {"op": "compress", "codec": "h264", "resolution": 720, "target_size_mb": 20, "audio_bitrate": 96}),
    ("快速草稿预览", {"op": "compress", "codec": "h264", "resolution": 480, "max_fps": 24, "quality": "low", "preset": "ultrafast"}),
    ("顺时针旋转 90°", {"op": "rotate", "angle": 90, "flip": "none"}),
    ("逆时针旋转 90°", {"op": "rotate", "angle": 270, "flip": "none"}),
    ("旋转 180°", {"op": "rotate", "angle": 180, "flip": "none"}),
    ("水平镜像", {"op": "rotate", "angle": 0, "flip": "horizontal"}),
    ("2 倍速", {"op": "speed", "factor": 2}),
    ("4 倍速延时", {"op": "speed", "factor": 4}),
    ("0.5 倍慢放", {"op": "speed", "factor": 0.5}),
    ("去除声音", {"op": "mute"}),
    ("转为 WebM", {"op": "convert", "format": "webm"}),
    ("转为 MKV", {"op": "convert", "format": "mkv"}),
    ("提取 MP3 音频", {"op": "extract_audio", "format": "mp3"}),
    ("提取 M4A 音频", {"op": "extract_audio", "format": "m4a"}),
    ("响度标准化 -16 LUFS", {"op": "audio", "mode": "adjust", "normalize": True, "target_lufs": -16}),
    ("广播响度 -23 LUFS", {"op": "audio", "mode": "adjust", "normalize": True, "target_lufs": -23}),
    ("声音淡入淡出 1 秒", {"op": "audio", "mode": "adjust", "fade_in": 1, "fade_out": 1}),
    ("音量增大 6 dB", {"op": "audio", "mode": "adjust", "gain_db": 6}),
    ("黑白", {"op": "adjust", "saturation": 0}),
    ("鲜艳色彩", {"op": "adjust", "contrast": 1.1, "saturation": 1.3}),
    ("轻度降噪", {"op": "adjust", "denoise": 4}),
    ("手持防抖", {"op": "adjust", "stabilize": True}),
    ("表情包 GIF（前 3 秒）", {"op": "animation", "format": "gif", "start": 0, "end": 3, "fps": 10, "width": 320}),
    ("WebP 动图（前 5 秒）", {"op": "animation", "format": "webp", "start": 0, "end": 5, "fps": 15, "width": 480, "quality": 75}),
]

presets = sa.table("edit_presets", sa.column("name", sa.String), sa.column("edit", sa.JSON), sa.column("created_at", sa.DateTime))


def upgrade() -> None:
    # Names are unique; skip any the user already took for a preset of their own.
    taken = set(op.get_bind().scalars(sa.select(presets.c.name)))
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [{"name": name, "edit": edit, "created_at": now} for name, edit in PRESETS if name not in taken]
    if rows:
        op.bulk_insert(presets, rows)


def downgrade() -> None:
    # Only remove presets still matching what this revision inserted.
    bind = op.get_bind()
    seeded = dict(PRESETS)
    for name, edit in bind.execute(sa.select(presets.c.name, presets.c.edit).where(presets.c.name.in_(seeded))):
        if edit == seeded[name]:
            bind.execute(presets.delete().where(presets.c.name == name))
