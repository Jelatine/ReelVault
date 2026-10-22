from __future__ import annotations

import asyncio
import subprocess

import pytest

from reelvault.media.ffmpeg import FFmpegError
from reelvault.media.validation import validate_output

from .conftest import make_video, upload_ready, wait_job


def check(path, **kwargs):
    asyncio.run(validate_output("ffmpeg", "ffprobe", path, **kwargs))


def test_validate_real_video_and_audio(samples, tmp_path):
    check(samples["a"], expected_duration=4, audio=True)
    audio = tmp_path / "sound.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(samples["a"]), "-vn", "-c:a", "copy", str(audio)],
        check=True,
    )
    check(audio, expected_duration=4, video=False, audio=True)
    with pytest.raises(FFmpegError, match="缺少视频轨道"):
        check(audio, expected_duration=4)


def test_validation_rejects_missing_audio_wrong_duration_and_corruption(samples, tmp_path):
    with pytest.raises(FFmpegError, match="缺少音频轨道"):
        check(samples["portrait"], expected_duration=3, audio=True)
    with pytest.raises(FFmpegError, match="预期时长"):
        check(samples["a"], expected_duration=12)
    damaged = tmp_path / "damaged.mp4"
    damaged.write_bytes(samples["a"].read_bytes()[:100])
    with pytest.raises(FFmpegError):
        check(damaged, expected_duration=4)


def test_variable_frame_rate_and_nonzero_timestamps(tmp_path):
    source = make_video(tmp_path / "source.mp4", duration=4)
    shifted = tmp_path / "shifted.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            "select='if(lt(t,2),not(mod(n,2)),1)',setpts=PTS+5/TB",
            "-af",
            "asetpts=PTS+5/TB",
            "-fps_mode",
            "vfr",
            str(shifted),
        ],
        check=True,
    )
    # Container duration can include the timestamp offset. Boundary decoding must
    # still accept ordinary VFR output; edit integration covers timeline changes.
    check(shifted, expected_duration=9, audio=True)


def test_failed_validation_never_replaces_original(client, samples, settings, monkeypatch):
    from reelvault.jobs import handlers
    from reelvault.library import abs_path
    from reelvault.models import Video

    original = upload_ready(client, samples["a"])
    factory = client.app.state.jobs.sessionmaker
    with factory() as db:
        video = db.get(Video, original["id"])
        path = abs_path(settings, video.file_path)
        contents = path.read_bytes()

    async def reject(*args, **kwargs):
        raise FFmpegError("输出校验失败：模拟损坏")

    monkeypatch.setattr(handlers, "validate_output", reject)
    response = client.post(
        f"/api/videos/{original['id']}/edit",
        json={"edit": {"op": "rotate", "angle": 90}, "output": {"mode": "replace"}},
    )
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "failed" and "输出校验失败" in job["error"]
    assert path.read_bytes() == contents
    assert client.get(f"/api/videos/{original['id']}").json()["width"] == 320
    assert client.get("/api/videos", params={"trash": True}).json()["total"] == 0
