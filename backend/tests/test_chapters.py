from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.config import Settings
from reelvault.media import chapters, ops
from reelvault.media.probe import embedded_chapters
from reelvault.models import Video

from .conftest import make_video, upload_ready, wait_job, wait_ready


def with_file_chapters(path: Path, marks: list[tuple[float, float, str]]) -> Path:
    meta = path.with_suffix(".meta.txt")
    meta.write_text(chapters.ffmetadata([{"start": s, "end": e, "title": t} for s, e, t in marks]))
    out = path.with_name("chaptered-" + path.name)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-f", "ffmetadata", "-i", str(meta)]
        + ["-map", "0", "-map_chapters", "1", "-c", "copy", str(out)],
        check=True,
    )
    return out


def file_chapters(path: Path) -> list[tuple[float, float, str]]:
    data = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json", str(path)]
        )
    )
    return [(c["start"], c["end"], c["title"]) for c in embedded_chapters(data)]


def test_embedded_chapters_are_relative_to_the_file_start() -> None:
    data = {
        "format": {"start_time": "1.5"},
        "chapters": [
            {"start_time": "3.5", "end_time": "6.5", "tags": {"title": " Two "}},
            {"start_time": "1.5", "end_time": "3.5"},
            {"start_time": "7", "end_time": "7"},
            {"start_time": "nan", "end_time": "9"},
        ],
    }
    assert embedded_chapters(data) == [
        {"start": 0, "end": 2, "title": "章节 2"},
        {"start": 2, "end": 5, "title": "Two"},
    ]


def test_map_chapters_through_cuts_speed_and_merges() -> None:
    source = [
        {"start": 0, "end": 4, "title": "A"},
        {"start": 4, "end": 10, "title": "B"},
    ]
    trim = ops.TrimParams(
        segments=[
            ops.Segment(start=1, end=3),
            ops.Segment(start=5, end=6),
            ops.Segment(start=7, end=9),
        ]
    )
    # B is split only by a removed range, so it continues as one chapter.
    assert chapters.map_chapters(trim, [source], [10], ["v"]) == [
        {"start": 0, "end": 2, "title": "A"},
        {"start": 2, "end": 5, "title": "B"},
    ]
    assert chapters.map_chapters(ops.SpeedParams(factor=2), [source], [10], ["v"]) == [
        {"start": 0, "end": 2, "title": "A"},
        {"start": 2, "end": 5, "title": "B"},
    ]
    mixed = ops.MixedParams(trim=ops.Segment(start=3, end=9), speed=ops.SpeedParams(factor=0.5))
    assert chapters.map_chapters(mixed, [source], [10], ["v"]) == [
        {"start": 0, "end": 2, "title": "A"},
        {"start": 2, "end": 12, "title": "B"},
    ]
    merge = ops.MergeParams(video_ids=["a", "b"])
    assert chapters.map_chapters(merge, [source, []], [10, 3], ["a", "Second"]) == [
        *source,
        {"start": 10, "end": 13, "title": "Second"},
    ]
    assert (
        chapters.map_chapters(
            ops.MergeParams(video_ids=["a", "b"], transition="fade"),
            [source, []],
            [10, 3],
            ["a", "b"],
        )
        is None
    )
    assert chapters.map_chapters(ops.RotateParams(), [source], [10], ["v"]) == source
    assert (
        chapters.map_chapters(
            ops.EffectParams(mode="freeze", start=1, duration=1), [source], [10], ["v"]
        )
        is None
    )


def test_ffmetadata_escaping_and_command_insertion(tmp_path: Path) -> None:
    text = chapters.ffmetadata([{"start": 0.0005, "end": 1.25, "title": "a=b;c#d\\e\nf"}])
    assert text == (
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1250\n"
        "title=a\\=b\;c\\#d\\\\e\\\nf\n"
    )
    command = ["-i", "a.mp4", "-stream_loop", "-1", "-i", "b.m4a", "-map", "0:v", "out.mp4"]
    changed = chapters.with_chapters(command, tmp_path / "c.txt")
    assert changed == [
        "-i", "a.mp4", "-stream_loop", "-1", "-i", "b.m4a",
        "-f", "ffmetadata", "-i", str(tmp_path / "c.txt"),
        "-map", "0:v", "-map_chapters", "2", "out.mp4",
    ]  # fmt: skip
    assert chapters.with_chapters(command, None)[-3:] == ["-map_chapters", "-1", "out.mp4"]
    combined = chapters.combine(
        10, [{"start": 2, "end": 5, "title": "File"}], [(0, "场景 1"), (2, "场景 2")], [(6, "Mine")]
    )
    assert combined == [
        {"start": 0, "end": 2, "title": "场景 1"},
        {"start": 2, "end": 6, "title": "File"},
        {"start": 6, "end": 10, "title": "Mine"},
    ]


@pytest.fixture
def chaptered(samples: dict[str, Path], tmp_path: Path) -> Path:
    source = make_video(tmp_path / "six.mp4", duration=6)
    return with_file_chapters(source, [(0, 2, "Intro"), (2, 4.5, "Main"), (4.5, 6, "Outro")])


def output_path(client: TestClient, settings: Settings, job: dict[str, Any]) -> Path:
    wait_ready(client, job["result_video_id"])
    with client.app.state.sessionmaker() as db:
        return settings.data_dir / db.get(Video, job["result_video_id"]).file_path


def run(client: TestClient, video_id: str, edit: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"/api/videos/{video_id}/edit", json={"edit": edit})
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    return job


def test_file_chapters_listed_imported_and_written_through_edits(
    client: TestClient, settings: Settings, chaptered: Path, samples: dict[str, Path]
) -> None:
    video = upload_ready(client, chaptered)
    url = f"/api/videos/{video['id']}/bookmarks"
    listed = client.get(url).json()
    assert [c["title"] for c in listed["embedded_chapters"]] == ["Intro", "Main", "Outro"]
    assert [(c["start"], c["title"]) for c in listed["chapters"]] == [
        (0, "Intro"),
        (2, "Main"),
        (4.5, "Outro"),
    ]
    # A manual chapter wins over the file's chapter at the same start.
    client.post(url, json={"position": 4.5, "title": "Credits", "kind": "chapter"})
    assert client.post(url + "/import-chapters").json() == {"created": 2}
    assert client.post(url + "/import-chapters").json() == {"created": 0}
    marks = client.get(url).json()["bookmarks"]
    assert [(m["position"], m["title"], m["kind"]) for m in marks] == [
        (0, "Intro", "chapter"),
        (2, "Main", "chapter"),
        (4.5, "Credits", "chapter"),
    ]

    trimmed = run(
        client,
        video["id"],
        {"op": "trim", "segments": [{"start": 1, "end": 3}, {"start": 5, "end": 6}]},
    )
    assert file_chapters(output_path(client, settings, trimmed)) == [
        (0, 1, "Intro"),
        (1, 2, "Main"),
        (2, 3, "Credits"),
    ]
    faster = run(client, video["id"], {"op": "speed", "factor": 2})
    assert [t for _, _, t in file_chapters(output_path(client, settings, faster))] == [
        "Intro",
        "Main",
        "Credits",
    ]
    assert file_chapters(output_path(client, settings, faster))[1][:2] == (1, 2.25)
    # Merging names a source without chapters after its title.
    other = upload_ready(client, samples["b"])
    merged = run(client, video["id"], {"op": "merge", "video_ids": [video["id"], other["id"]]})
    assert file_chapters(output_path(client, settings, merged))[-1] == (6, 9, "clip_b")
    # Chapters do not survive into an edit of a file without any.
    plain = upload_ready(client, samples["a"])
    rotated = run(client, plain["id"], {"op": "rotate", "mode": "reencode"})
    assert file_chapters(output_path(client, settings, rotated)) == []
