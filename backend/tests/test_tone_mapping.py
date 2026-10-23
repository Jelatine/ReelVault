from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reelvault.media import color
from reelvault.media.ffmpeg import FFmpegError
from reelvault.media.probe import MediaInfo, parse_probe

HDR = MediaInfo(duration=4, width=64, height=64, video_codec="hevc", color_transfer="smpte2084")


def test_each_tone_mapper_builds_its_chain_and_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "hdr.mp4"
    command = ["-i", str(source), "-vf", "scale=32:-2", "-c:v", "libx264", "out.mp4"]
    assert "zscale=tin=smpte2084" in color.sdr_commands([command], [(source, HDR)])[0][3]
    monkeypatch.setattr(color.TONE, "mapper", "opencl")
    changed = color.sdr_commands([command], [(source, HDR)])[0]
    assert changed[:4] == ["-init_hw_device", "opencl=rv_tone", "-filter_hw_device", "rv_tone"]
    assert "hwupload,tonemap_opencl=" in changed[changed.index("-vf") + 1]
    assert changed[changed.index("-vf") + 1].endswith(",scale=32:-2")
    monkeypatch.setattr(color.TONE, "mapper", "vaapi")
    monkeypatch.setattr(color.TONE, "vaapi_device", "/dev/dri/renderD129")
    changed = color.sdr_commands([command], [(source, HDR)])[0]
    assert changed[1] == "vaapi=rv_tone:/dev/dri/renderD129" and "tonemap_vaapi=" in changed[7]
    monkeypatch.setattr(color.TONE, "mapper", "libplacebo")
    changed = color.sdr_commands([command], [(source, HDR)])[0]
    assert changed[0] == "-i" and changed[3].startswith("libplacebo=tonemapping=")
    # Stream copies keep HDR whatever the mapper.
    copy = ["-i", str(source), "-c", "copy", "out.mp4"]
    assert color.sdr_commands([copy], [(source, HDR)]) == [copy]


def test_detection_prefers_the_first_working_gpu_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def run(args: list[str], **kwargs: Any) -> None:
        calls.append(args)
        if "libplacebo" in " ".join(args):
            raise FFmpegError("Failed creating Vulkan device!")

    monkeypatch.setattr(color, "run_command", run)
    monkeypatch.setattr(color.TONE, "mapper", "zscale")
    filters = {"libplacebo", "tonemap_opencl", "tonemap_vaapi"}
    assert (
        asyncio.run(color.detect_tone_mapper("ffmpeg", filters, "/dev/dri/renderD128")) == "opencl"
    )
    assert color.TONE.mapper == "opencl" and len(calls) == 2
    assert asyncio.run(color.detect_tone_mapper("ffmpeg", {"scale"}, "")) == "zscale"
    assert color.TONE.mapper == "zscale"


def test_real_detection_falls_back_without_a_device(monkeypatch: pytest.MonkeyPatch) -> None:
    from reelvault.media.capabilities import detect_capabilities

    monkeypatch.setattr(color.TONE, "mapper", "zscale")
    capabilities = asyncio.run(detect_capabilities("ffmpeg"))
    assert capabilities is not None
    mapper = asyncio.run(color.detect_tone_mapper("ffmpeg", capabilities.filters, "/nonexistent"))
    assert mapper in {"zscale", *color.TONE_MAPPERS} and color.TONE.mapper == mapper


def test_dolby_vision_profile_5_needs_libplacebo(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = {
        "index": 0, "codec_type": "video", "codec_name": "hevc", "width": 64, "height": 64,
        "side_data_list": [
            {"side_data_type": "DOVI configuration record", "dv_profile": 5,
             "dv_bl_signal_compatibility_id": 0},
        ],
    }  # fmt: skip
    info = parse_probe({"format": {"duration": "4", "format_name": "mov,mp4"}, "streams": [stream]})
    assert info.dolby_vision_only and not info.is_hdr and not info.hevc_remuxable
    assert info.to_meta()["dolby_vision_profile"] == 5
    assert not color.needs_tone_mapping(info)
    monkeypatch.setattr(color.TONE, "mapper", "libplacebo")
    assert color.needs_tone_mapping(info)
    assert color.sdr_filter(info).startswith("libplacebo=")
    stream["side_data_list"][0].update(dv_profile=8, dv_bl_signal_compatibility_id=1)
    hdr10_compatible = parse_probe({"format": {"duration": "4"}, "streams": [stream]})
    assert hdr10_compatible.dolby_vision_profile == 8 and not hdr10_compatible.dolby_vision_only


def test_system_info_reports_the_tone_mapper(client: TestClient) -> None:
    assert client.get("/api/system/info").json()["hdr_tone_mapper"] in {
        "zscale",
        *color.TONE_MAPPERS,
    }
