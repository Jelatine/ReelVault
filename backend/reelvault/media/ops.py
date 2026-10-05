"""Edit operations. Each builds the ffmpeg command line(s) that produce one output file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .derive import even
from .probe import MP4_AUDIO_CODECS, MediaInfo

Source = tuple[Path, MediaInfo]


class OpError(ValueError):
    pass


@dataclass
class OpPlan:
    # ffmpeg argument lists, run in order (prefix added by the runner)
    commands: list[list[str]]
    # expected output duration in seconds, for progress reporting
    duration: float
    ext: str = "mp4"
    files: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------- parameters


class Segment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> Segment:
        if self.end <= self.start:
            raise ValueError("结束时间必须大于开始时间")
        return self


class RotateParams(BaseModel):
    op: Literal["rotate"] = "rotate"
    angle: Literal[0, 90, 180, 270] = 90
    flip: Literal["none", "horizontal", "vertical"] = "none"
    crf: int = Field(20, ge=0, le=51)

    @model_validator(mode="after")
    def _noop(self) -> RotateParams:
        if self.angle == 0 and self.flip == "none":
            raise ValueError("请选择旋转角度或翻转方向")
        return self


class TrimParams(BaseModel):
    op: Literal["trim"] = "trim"
    segments: list[Segment] = Field(min_length=1, max_length=50)
    # fast: stream copy, cuts land on keyframes. precise: re-encode, frame accurate.
    mode: Literal["fast", "precise"] = "precise"
    crf: int = Field(20, ge=0, le=51)


class MergeParams(BaseModel):
    op: Literal["merge"] = "merge"
    video_ids: list[str] = Field(min_length=2, max_length=50)
    mode: Literal["auto", "lossless", "reencode"] = "auto"
    # Target size for re-encoding; defaults to the first video.
    width: int | None = Field(None, ge=16, le=7680)
    height: int | None = Field(None, ge=16, le=4320)
    fps: float | None = Field(None, gt=0, le=120)
    crf: int = Field(21, ge=0, le=51)


class CompressParams(BaseModel):
    op: Literal["compress"] = "compress"
    codec: Literal["h264", "h265"] = "h264"
    quality: Literal["high", "medium", "low"] = "medium"
    # Shorter side in pixels; None keeps the original resolution.
    resolution: Literal[2160, 1440, 1080, 720, 480, 360] | None = None
    max_fps: float | None = Field(None, gt=0, le=120)
    # When set, two-pass encode aiming for this output size.
    target_size_mb: float | None = Field(None, gt=0)
    audio_bitrate: Literal[64, 96, 128, 160, 192, 256] = 128
    preset: Literal["ultrafast", "veryfast", "faster", "fast", "medium", "slow"] = "medium"


class CropParams(BaseModel):
    op: Literal["crop"] = "crop"
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    crf: int = Field(20, ge=0, le=51)


class SpeedParams(BaseModel):
    op: Literal["speed"] = "speed"
    factor: float = Field(ge=0.25, le=4)
    crf: int = Field(20, ge=0, le=51)


class MuteParams(BaseModel):
    op: Literal["mute"] = "mute"


class ConvertParams(BaseModel):
    op: Literal["convert"] = "convert"
    format: Literal["mp4", "webm", "mkv"] = "mp4"


class ExtractAudioParams(BaseModel):
    op: Literal["extract_audio"] = "extract_audio"
    format: Literal["mp3", "m4a"] = "mp3"


class EmbedCoverParams(BaseModel):
    op: Literal["embed_cover"] = "embed_cover"


EditParams = Annotated[
    RotateParams
    | TrimParams
    | MergeParams
    | CompressParams
    | CropParams
    | SpeedParams
    | MuteParams
    | ConvertParams
    | ExtractAudioParams
    | EmbedCoverParams,
    Field(discriminator="op"),
]

OP_LABELS = {
    "rotate": "旋转",
    "trim": "剪辑",
    "merge": "合并",
    "compress": "压缩",
    "crop": "裁切画面",
    "speed": "变速",
    "mute": "静音",
    "convert": "转换格式",
    "extract_audio": "提取音频",
    "embed_cover": "写入封面",
}

# ---------------------------------------------------------------- helpers

COPY_CONTAINERS = {"mp4", "mov", "m4v", "mkv", "webm"}


def copy_ext(info: MediaInfo) -> str:
    return info.container if info.container in COPY_CONTAINERS else "mkv"


def x264(crf: int, preset: str = "medium") -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


def aac(bitrate: int = 160) -> list[str]:
    return ["-c:a", "aac", "-b:a", f"{bitrate}k"]


def audio_for_mp4(info: MediaInfo) -> list[str]:
    if info.audio_codec in {"aac", "mp3"}:
        return ["-c:a", "copy"]
    return aac()


MP4_FLAGS = ["-movflags", "+faststart"]


def maps(info: MediaInfo, audio: bool = True) -> list[str]:
    out = ["-map", f"0:{info.video_index}"]
    if audio and info.audio_index is not None:
        out += ["-map", f"0:{info.audio_index}"]
    return out


def atempo_chain(factor: float) -> str:
    parts: list[str] = []
    while factor > 2.0:
        parts.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.6f}")
    return ",".join(parts)


def scaled_dims(info: MediaInfo, short_side: int | None) -> tuple[int, int]:
    w, h = info.width, info.height
    if short_side and min(w, h) > short_side:
        ratio = short_side / min(w, h)
        return even(w * ratio), even(h * ratio)
    return even(w), even(h)


# ---------------------------------------------------------------- builders


def plan_rotate(p: RotateParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    filters = {90: ["transpose=clock"], 180: ["hflip", "vflip"], 270: ["transpose=cclock"]}.get(
        p.angle, []
    )
    if p.flip == "horizontal":
        filters.append("hflip")
    elif p.flip == "vertical":
        filters.append("vflip")
    args = ["-i", str(path), *maps(info), "-vf", ",".join(filters), *x264(p.crf)]
    args += [*audio_for_mp4(info), *MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration)


def plan_trim(p: TrimParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    segs: list[tuple[float, float]] = []
    for seg in p.segments:
        end = min(seg.end, info.duration) if info.duration else seg.end
        if end - seg.start > 0.01:
            segs.append((seg.start, end))
    if not segs:
        raise OpError("剪辑区间超出视频时长")
    total = sum(e - s for s, e in segs)

    if len(segs) == 1:
        start, end = segs[0]
        args = ["-ss", f"{start:.3f}", "-i", str(path), "-t", f"{end - start:.3f}", *maps(info)]
        if p.mode == "fast":
            ext = copy_ext(info)
            args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
            if ext in {"mp4", "mov", "m4v"}:
                args += MP4_FLAGS
            out = out.with_suffix("." + ext)
            return OpPlan([args + [str(out)]], total, ext=ext)
        args += [*x264(p.crf), *aac(), *MP4_FLAGS, str(out)]
        return OpPlan([args], total)

    v, a = f"0:{info.video_index}", f"0:{info.audio_index}"
    has_audio = info.audio_index is not None
    chains, labels = [], []
    for i, (s, e) in enumerate(segs):
        chains.append(f"[{v}]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            chains.append(f"[{a}]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
    n = len(segs)
    graph = ";".join(chains) + ";" + "".join(labels)
    graph += f"concat=n={n}:v=1:a={1 if has_audio else 0}[v]" + ("[a]" if has_audio else "")
    args = ["-i", str(path), "-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        args += ["-map", "[a]", *aac()]
    args += [*x264(p.crf), *MP4_FLAGS, str(out)]
    return OpPlan([args], total)


def merge_lossless_ok(sources: list[Source]) -> bool:
    first = sources[0][1]
    if first.video_codec not in {"h264", "hevc", "av1", "mpeg4"}:
        return False
    if first.audio_codec is not None and first.audio_codec not in MP4_AUDIO_CODECS:
        return False
    keys = [
        (
            i.video_codec,
            i.coded_width,
            i.coded_height,
            i.rotation,
            i.pix_fmt,
            i.audio_codec,
            i.sample_rate,
            i.channels,
        )
        for _, i in sources
    ]
    return all(k == keys[0] for k in keys)


def plan_merge(p: MergeParams, sources: list[Source], out: Path, tmp: Path) -> OpPlan:
    total = sum(i.duration for _, i in sources)
    lossless = p.mode == "lossless" or (p.mode == "auto" and merge_lossless_ok(sources))
    if p.mode == "lossless" and not merge_lossless_ok(sources):
        raise OpError("视频编码/分辨率不一致，无法无损合并，请选择重新编码")

    if lossless:
        first = sources[0][1]
        listing = tmp / "concat.txt"
        listing.write_text(
            "".join("file '" + str(path).replace("'", "'\\''") + "'\n" for path, _ in sources)
        )
        args = ["-f", "concat", "-safe", "0", "-i", str(listing), *maps(first)]
        args += ["-c", "copy", *MP4_FLAGS, str(out)]
        return OpPlan([args], total, files=[listing])

    first = sources[0][1]
    w = even(p.width or first.width)
    h = even(p.height or first.height)
    fps = p.fps or (min(first.fps, 60) if first.fps else 30)
    any_audio = any(i.has_audio for _, i in sources)

    args = []
    for path, _ in sources:
        args += ["-i", str(path)]
    silent_inputs: dict[int, int] = {}
    if any_audio:
        for idx, (_, info) in enumerate(sources):
            if not info.has_audio:
                silent_inputs[idx] = len(sources) + len(silent_inputs)
                args += [
                    "-f", "lavfi", "-t", f"{info.duration:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo",
                ]  # fmt: skip

    chains, labels = [], []
    for idx, (_, info) in enumerate(sources):
        chains.append(
            f"[{idx}:{info.video_index}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{idx}]"
        )
        labels.append(f"[v{idx}]")
        if any_audio:
            a_in = (
                f"{silent_inputs[idx]}:0" if idx in silent_inputs else f"{idx}:{info.audio_index}"
            )
            chains.append(
                f"[{a_in}]aformat=sample_rates=48000:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS[a{idx}]"
            )
            labels.append(f"[a{idx}]")
    n = len(sources)
    graph = ";".join(chains) + ";" + "".join(labels)
    graph += f"concat=n={n}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else "")
    args += ["-filter_complex", graph, "-map", "[v]"]
    if any_audio:
        args += ["-map", "[a]", *aac()]
    args += [*x264(p.crf), *MP4_FLAGS, str(out)]
    return OpPlan([args], total)


CRF_TABLE = {
    "h264": {"high": 23, "medium": 27, "low": 31},
    "h265": {"high": 26, "medium": 29, "low": 33},
}


def plan_compress(p: CompressParams, src: Source, out: Path, tmp: Path) -> OpPlan:
    path, info = src
    w, h = scaled_dims(info, p.resolution)
    filters = []
    if (w, h) != (even(info.width), even(info.height)) or info.width % 2 or info.height % 2:
        filters.append(f"scale={w}:{h}")
    if p.max_fps and info.fps > p.max_fps:
        filters.append(f"fps={p.max_fps}")
    filters.append("format=yuv420p")
    vf = ["-vf", ",".join(filters)]
    encoder = "libx264" if p.codec == "h264" else "libx265"
    tag = ["-tag:v", "hvc1"] if p.codec == "h265" else []
    audio = aac(p.audio_bitrate) if info.has_audio else []

    if p.target_size_mb is None:
        args = ["-i", str(path), *maps(info), *vf, "-c:v", encoder, "-preset", p.preset]
        args += ["-crf", str(CRF_TABLE[p.codec][p.quality]), *tag, *audio, *MP4_FLAGS, str(out)]
        return OpPlan([args], info.duration)

    if info.duration <= 0:
        raise OpError("无法获取视频时长，不能按目标大小压缩")
    total_kbps = p.target_size_mb * 8 * 1024 / info.duration
    audio_kbps = p.audio_bitrate if info.has_audio else 0
    video_kbps = int(total_kbps * 0.97 - audio_kbps)
    if video_kbps < 50:
        raise OpError("目标文件过小，无法达到可用的画质")
    log = tmp / "2pass"

    def pass_args(n: int) -> list[str]:
        if p.codec == "h264":
            return ["-pass", str(n), "-passlogfile", str(log)]
        return ["-x265-params", f"pass={n}:stats={log}.log"]

    rate = ["-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.5)}k"]
    rate += ["-bufsize", f"{video_kbps * 2}k"]
    common = ["-i", str(path), *vf, "-c:v", encoder, "-preset", p.preset, *rate]
    pass1 = [*common, *pass_args(1), "-map", f"0:{info.video_index}", "-an", "-f", "null", "-"]
    pass2 = [*common, *pass_args(2), *maps(info), *tag, *audio, *MP4_FLAGS, str(out)]
    return OpPlan([pass1, pass2], info.duration)


def plan_crop(p: CropParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    if p.x + p.width > info.width or p.y + p.height > info.height:
        raise OpError("裁切区域超出画面范围")
    w, h = p.width // 2 * 2, p.height // 2 * 2
    args = ["-i", str(path), *maps(info), "-vf", f"crop={w}:{h}:{p.x}:{p.y}", *x264(p.crf)]
    args += [*audio_for_mp4(info), *MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration)


def plan_speed(p: SpeedParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    args = ["-i", str(path), "-filter:v", f"setpts=PTS/{p.factor}", *maps(info)]
    if info.has_audio:
        args += ["-filter:a", atempo_chain(p.factor), *aac()]
    args += [*x264(p.crf), *MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration / p.factor)


def plan_mute(src: Source, out: Path) -> OpPlan:
    path, info = src
    ext = copy_ext(info)
    out = out.with_suffix("." + ext)
    args = ["-i", str(path), *maps(info, audio=False), "-c", "copy", "-an", str(out)]
    return OpPlan([args], info.duration, ext=ext)


def plan_convert(p: ConvertParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    out = out.with_suffix("." + p.format)
    args = ["-i", str(path), *maps(info)]
    if p.format == "mkv":
        args += ["-c", "copy"]
    elif p.format == "mp4":
        if info.remuxable_to_mp4:
            args += ["-c", "copy"]
        else:
            args += [*x264(21), *aac()]
        args += MP4_FLAGS
    else:
        args += [
            "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
            "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p",
        ]  # fmt: skip
        if info.has_audio:
            args += ["-c:a", "libopus", "-b:a", "128k"]
    return OpPlan([args + [str(out)]], info.duration, ext=p.format)


def plan_extract_audio(p: ExtractAudioParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    if info.audio_index is None:
        raise OpError("该视频没有音轨")
    out = out.with_suffix("." + p.format)
    args = ["-i", str(path), "-map", f"0:{info.audio_index}", "-vn"]
    if p.format == "mp3":
        args += ["-c:a", "libmp3lame", "-q:a", "2"]
    elif info.audio_codec == "aac":
        args += ["-c:a", "copy"]
    else:
        args += aac(192)
    return OpPlan([args + [str(out)]], info.duration, ext=p.format)


def plan_embed_cover(src: Source, cover: Path, out: Path) -> OpPlan:
    path, info = src
    if info.container not in {"mp4", "mov", "m4v"}:
        raise OpError("仅 MP4/MOV 文件支持写入封面")
    ext = info.container
    out = out.with_suffix("." + ext)
    args = ["-i", str(path), "-i", str(cover), *maps(info), "-map", "1:0"]
    args += ["-c", "copy", "-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
    args += [*MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration, ext=ext)
