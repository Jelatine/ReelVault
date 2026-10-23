"""Edit operations. Each builds the ffmpeg command line(s) that produce one output file."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .color import processed_fps
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
    cwd: Path | None = None


# ---------------------------------------------------------------- parameters


class Segment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

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
    # auto: a pure rotation of an MP4/MOV only rewrites the display matrix (stream copy)
    # when FFmpeg supports -display_rotation; otherwise, and for reencode, re-encode.
    mode: Literal["auto", "reencode"] = "auto"

    @model_validator(mode="after")
    def _noop(self) -> RotateParams:
        if self.angle == 0 and self.flip == "none":
            raise ValueError("请选择旋转角度或翻转方向")
        return self


class TrimParams(BaseModel):
    op: Literal["trim"] = "trim"
    segments: list[Segment] = Field(min_length=1, max_length=50)
    # fast: stream copy, cuts land on keyframes. precise: re-encode, frame accurate.
    # smart: frame accurate, re-encoding only from each cut to the nearest keyframe.
    mode: Literal["fast", "precise", "smart"] = "precise"
    crf: int = Field(20, ge=0, le=51)


class MergeParams(BaseModel):
    op: Literal["merge"] = "merge"
    video_ids: list[str] = Field(min_length=2, max_length=50)
    mode: Literal["auto", "lossless", "reencode"] = "auto"
    delete_sources: bool = False
    # Target size for re-encoding; defaults to the first video.
    width: int | None = Field(None, ge=16, le=7680)
    height: int | None = Field(None, ge=16, le=4320)
    fps: float | None = Field(None, gt=0, le=120, allow_inf_nan=False)
    crf: int = Field(21, ge=0, le=51)
    transition: Literal[
        "none",
        "fade",
        "fadeblack",
        "fadewhite",
        "wipeleft",
        "wiperight",
        "slideleft",
        "slideright",
        "dissolve",
        "circleopen",
    ] = "none"
    transition_duration: float = Field(0.5, ge=0.05, le=10, allow_inf_nan=False)

    @model_validator(mode="after")
    def transition_needs_encoding(self) -> MergeParams:
        if self.transition != "none" and self.mode == "lossless":
            raise ValueError("合并转场需要重新编码，不能使用无损模式")
        return self


class CompressParams(BaseModel):
    op: Literal["compress"] = "compress"
    codec: Literal["h264", "h265", "av1"] = "h264"
    quality: Literal["high", "medium", "low"] = "medium"
    # Shorter side in pixels; None keeps the original resolution.
    resolution: Literal[2160, 1440, 1080, 720, 480, 360] | None = None
    max_fps: float | None = Field(None, gt=0, le=120, allow_inf_nan=False)
    # When set, two-pass encode aiming for this output size.
    target_size_mb: float | None = Field(None, gt=0, allow_inf_nan=False)
    audio_bitrate: Literal[64, 96, 128, 160, 192, 256] = 128
    preset: Literal["ultrafast", "veryfast", "faster", "fast", "medium", "slow"] = "medium"
    # Score the result against the source (VMAF, or SSIM without libvmaf).
    measure_quality: bool = False


class CropParams(BaseModel):
    op: Literal["crop"] = "crop"
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    crf: int = Field(20, ge=0, le=51)


class SpeedParams(BaseModel):
    op: Literal["speed"] = "speed"
    factor: float = Field(ge=0.25, le=4, allow_inf_nan=False)
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


class AudioParams(BaseModel):
    op: Literal["audio"] = "audio"
    mode: Literal["adjust", "replace", "mix"] = "adjust"
    gain_db: float = Field(0, ge=-60, le=24, allow_inf_nan=False)
    music_gain_db: float = Field(-12, ge=-60, le=24, allow_inf_nan=False)
    normalize: bool = False
    target_lufs: float = Field(-16, ge=-70, le=-5, allow_inf_nan=False)
    # FFT noise reduction (dB) applied to the original track; 0 disables it.
    denoise: float = Field(0, ge=0, le=40, allow_inf_nan=False)
    fade_in: float = Field(0, ge=0, le=3600, allow_inf_nan=False)
    fade_out: float = Field(0, ge=0, le=3600, allow_inf_nan=False)
    audio_asset_id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")
    loop: bool = False
    offset: float = Field(0, ge=0, le=86400, allow_inf_nan=False)

    @model_validator(mode="after")
    def asset_required(self) -> AudioParams:
        if self.mode != "adjust" and not self.audio_asset_id:
            raise ValueError("替换或混音需要选择上传的音频")
        if self.mode == "adjust" and self.audio_asset_id:
            raise ValueError("调整原音轨不使用外部音频")
        if self.mode == "replace" and self.denoise:
            raise ValueError("降噪仅作用于原音轨，替换音轨时不可用")
        return self


class SubtitleParams(BaseModel):
    op: Literal["subtitle"] = "subtitle"
    subtitle_asset_id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")
    embedded_index: int | None = Field(None, ge=0, le=1000)
    crf: int = Field(20, ge=0, le=51)

    @model_validator(mode="after")
    def one_source(self) -> SubtitleParams:
        if (self.subtitle_asset_id is None) == (self.embedded_index is None):
            raise ValueError("请选择一份外挂字幕或一条内封字幕")
        return self


class AnimationParams(BaseModel):
    op: Literal["animation"] = "animation"
    format: Literal["gif", "webp"] = "gif"
    start: float = Field(0, ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    fps: int = Field(12, ge=1, le=60)
    width: int = Field(480, ge=16, le=1920)
    loop: bool = True
    colors: int = Field(256, ge=16, le=256)
    dither: Literal["sierra2_4a", "bayer", "none"] = "sierra2_4a"
    quality: int = Field(80, ge=1, le=100)
    lossless: bool = False

    @model_validator(mode="after")
    def valid_range(self) -> AnimationParams:
        if self.end <= self.start:
            raise ValueError("动图片段结束时间必须大于开始时间")
        return self


class WatermarkParams(BaseModel):
    op: Literal["watermark"] = "watermark"
    mode: Literal["image", "text"] = "text"
    image_asset_id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")
    text: str = Field("", max_length=2000)
    position: Literal[
        "top-left", "top-right", "bottom-left", "bottom-right", "center", "custom"
    ] = "bottom-right"
    x: float = Field(50, ge=0, le=100, allow_inf_nan=False)
    y: float = Field(50, ge=0, le=100, allow_inf_nan=False)
    margin_percent: float = Field(2, ge=0, le=25, allow_inf_nan=False)
    opacity: float = Field(0.65, ge=0, le=1, allow_inf_nan=False)
    width_percent: float = Field(20, ge=1, le=100, allow_inf_nan=False)
    font_size: int = Field(32, ge=8, le=512)
    color: str = Field("#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    border_width: int = Field(2, ge=0, le=20)
    box: bool = False
    crf: int = Field(20, ge=0, le=51)

    @model_validator(mode="after")
    def valid_content(self) -> WatermarkParams:
        if self.mode == "image":
            if not self.image_asset_id or self.text:
                raise ValueError("图片水印需要选择图片，不能同时指定文字")
        elif self.image_asset_id or not self.text.strip():
            raise ValueError("文字水印需要非空文字，不能同时指定图片")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in self.text):
            raise ValueError("文字不能包含控制字符")
        self.text = self.text.replace("\r\n", "\n").replace("\r", "\n")
        return self


class AdjustParams(BaseModel):
    op: Literal["adjust"] = "adjust"
    brightness: float = Field(0, ge=-1, le=1, allow_inf_nan=False)
    contrast: float = Field(1, ge=0, le=3, allow_inf_nan=False)
    saturation: float = Field(1, ge=0, le=3, allow_inf_nan=False)
    lut_asset_id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")
    denoise: float = Field(0, ge=0, le=20, allow_inf_nan=False)
    stabilize: bool = False
    shakiness: int = Field(5, ge=1, le=10)
    accuracy: int = Field(15, ge=1, le=15)
    smoothing: int = Field(15, ge=1, le=100)
    zoom: float = Field(0, ge=0, le=100, allow_inf_nan=False)
    autozoom: bool = True
    crf: int = Field(20, ge=0, le=51)

    @model_validator(mode="after")
    def valid_adjustment(self) -> AdjustParams:
        if self.stabilize and self.accuracy < self.shakiness:
            raise ValueError("防抖精度不能小于抖动强度")
        if not (
            self.brightness
            or self.contrast != 1
            or self.saturation != 1
            or self.lut_asset_id
            or self.denoise
            or self.stabilize
        ):
            raise ValueError("请至少启用一项画面调整")
        return self


class EffectParams(BaseModel):
    op: Literal["effect"] = "effect"
    mode: Literal["reverse", "freeze", "slow"] = "reverse"
    start: float = Field(0, ge=0, allow_inf_nan=False)
    end: float | None = Field(None, gt=0, allow_inf_nan=False)
    duration: float = Field(2, ge=0.04, le=3600, allow_inf_nan=False)
    factor: float = Field(0.5, ge=0.1, lt=1, allow_inf_nan=False)
    crf: int = Field(20, ge=0, le=51)

    @model_validator(mode="after")
    def valid_range(self) -> EffectParams:
        if self.mode != "freeze" and (self.end is None or self.end <= self.start):
            raise ValueError("效果片段结束时间必须大于开始时间")
        return self


class CompositeParams(BaseModel):
    op: Literal["composite"] = "composite"
    video_ids: list[str] = Field(min_length=2, max_length=9)
    layout: Literal["pip", "horizontal", "vertical", "grid"] = "pip"
    width: int = Field(1280, ge=32, le=7680)
    height: int = Field(720, ge=32, le=4320)
    fps: float = Field(30, ge=1, le=120, allow_inf_nan=False)
    columns: int = Field(2, ge=1, le=3)
    fit: Literal["contain", "cover"] = "contain"
    background: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    duration_mode: Literal["first", "longest", "shortest"] = "first"
    audio_mode: Literal["source", "mix", "none"] = "source"
    audio_source: int = Field(0, ge=0, le=8)
    pip_scale: float = Field(30, ge=5, le=80, allow_inf_nan=False)
    pip_x: float = Field(98, ge=0, le=100, allow_inf_nan=False)
    pip_y: float = Field(98, ge=0, le=100, allow_inf_nan=False)
    pip_opacity: float = Field(1, ge=0, le=1, allow_inf_nan=False)
    crf: int = Field(20, ge=0, le=51)

    @model_validator(mode="after")
    def valid_layout(self) -> CompositeParams:
        if len(set(self.video_ids)) != len(self.video_ids):
            raise ValueError("拼接输入不能重复")
        if self.layout == "pip" and len(self.video_ids) != 2:
            raise ValueError("画中画需要一个主视频和一个叠加视频")
        if self.width % 2 or self.height % 2:
            raise ValueError("拼接画布宽高必须为偶数")
        if self.audio_mode == "source" and self.audio_source >= len(self.video_ids):
            raise ValueError("音轨来源超出输入范围")
        return self


class MixedParams(BaseModel):
    """One source, fixed filter order, one final encode (or two-pass compression)."""

    op: Literal["mixed"] = "mixed"
    trim: Segment | None = None
    crop: CropParams | None = None
    rotate: RotateParams | None = None
    speed: SpeedParams | None = None
    mute: bool = False
    compress: CompressParams = Field(default_factory=CompressParams)

    @model_validator(mode="after")
    def has_transform(self) -> MixedParams:
        if not (self.trim or self.crop or self.rotate or self.speed or self.mute):
            raise ValueError("混合编辑需要至少选择一项操作与压缩组合")
        return self


class RepairParams(BaseModel):
    """Remux while discarding corrupt packets; nothing is re-encoded."""

    op: Literal["repair"] = "repair"


EditParams = Annotated[
    RepairParams
    | RotateParams
    | TrimParams
    | MergeParams
    | CompressParams
    | CropParams
    | SpeedParams
    | MuteParams
    | ConvertParams
    | ExtractAudioParams
    | EmbedCoverParams
    | AudioParams
    | SubtitleParams
    | WatermarkParams
    | AnimationParams
    | AdjustParams
    | EffectParams
    | CompositeParams
    | MixedParams,
    Field(discriminator="op"),
]

OP_LABELS = {
    "repair": "修复（重新封装）",
    "mixed": "混合编辑",
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
    "audio": "音频处理",
    "subtitle": "烧录字幕",
    "watermark": "水印与文字",
    "animation": "导出动图",
    "adjust": "画面调整",
    "effect": "片段效果",
    "composite": "画中画与分屏",
}

# ---------------------------------------------------------------- helpers

COPY_CONTAINERS = {"mp4", "mov", "m4v", "mkv", "webm"}
MATROSKA_SEEK_BACKOFF = 3 / 23


def copy_ext(info: MediaInfo) -> str:
    return info.container if info.container in COPY_CONTAINERS else "mkv"


def x264(crf: int, preset: str = "medium") -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


# CRF is chosen on the x264 scale; x265 reaches similar quality a few steps higher.
X265_CRF_OFFSET = 3


@dataclass(frozen=True)
class VideoTarget:
    """Re-encode output that follows the sources instead of always using 8-bit H.264."""

    codec: Literal["h264", "h265"] = "h264"
    ten_bit: bool = False

    @property
    def pix_fmt(self) -> str:
        return "yuv420p10le" if self.ten_bit else "yuv420p"

    def args(self, crf: int, preset: str = "medium") -> list[str]:
        if self.codec == "h264":
            return x264(crf, preset)
        crf = min(51, crf + X265_CRF_OFFSET)
        return [
            "-c:v", "libx265", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", self.pix_fmt, "-tag:v", "hvc1",
        ]  # fmt: skip


def video_target(*infos: MediaInfo) -> VideoTarget:
    """HEVC sources stay HEVC; 10-bit SDR stays 10-bit (HDR is tone mapped to 8-bit)."""
    if not infos or any(info.video_codec != "hevc" for info in infos):
        return VideoTarget()
    return VideoTarget("h265", all(i.high_bit_depth and not i.is_hdr for i in infos))


def aac(bitrate: int = 160) -> list[str]:
    return ["-c:a", "aac", "-b:a", f"{bitrate}k"]


def audio_codecs(info: MediaInfo) -> list[str]:
    streams = info.extra.get("audio_streams") or []
    return [str(s["codec"]) for s in streams] or ([info.audio_codec] if info.audio_codec else [])


def audio_for_mp4(info: MediaInfo) -> list[str]:
    if all(codec in {"aac", "mp3"} for codec in audio_codecs(info)):
        return ["-c:a", "copy"]
    return aac()


MP4_FLAGS = ["-movflags", "+faststart"]
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}


def audio_indices(info: MediaInfo) -> list[int]:
    """The primary audio track first, then every other audio track."""
    if info.audio_index is None:
        return []
    others = [
        int(s["index"])
        for s in info.extra.get("audio_streams") or []
        if int(s["index"]) != info.audio_index
    ]
    return [info.audio_index, *others]


def subtitle_indices(info: MediaInfo, container: str = "mp4") -> list[int]:
    """Subtitle tracks the output container can hold; MP4/WebM take text tracks only."""
    streams = info.extra.get("subtitle_streams") or []
    return [
        int(s["index"]) for s in streams if container == "mkv" or s["codec"] in TEXT_SUBTITLE_CODECS
    ]


def subtitle_codec(info: MediaInfo, container: str = "mp4") -> list[str]:
    if not subtitle_indices(info, container):
        return []
    return {"mkv": ["-c:s", "copy"], "webm": ["-c:s", "webvtt"]}.get(
        container, ["-c:s", "mov_text"]
    )


def maps(
    info: MediaInfo, audio: bool = True, *, subtitles: bool = False, container: str = "mp4"
) -> list[str]:
    """Video first (VAAPI relies on it), then all audio tracks and optional subtitles."""
    out = ["-map", f"0:{info.video_index}"]
    for index in audio_indices(info) if audio else []:
        out += ["-map", f"0:{index}"]
    for index in subtitle_indices(info, container) if subtitles else []:
        out += ["-map", f"0:{index}"]
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


# Matroska muxers before FFmpeg 7 drop the display matrix on stream copy.
DISPLAY_MATRIX_CONTAINERS = {"mp4", "mov", "m4v"}


def lossless_rotation_blocker(p: RotateParams, info: MediaInfo, supported: bool) -> str | None:
    """Why a rotation must be re-encoded, or None when the display matrix suffices."""
    if p.mode == "reencode":
        return "已选择重新编码"
    if p.flip != "none":
        return "翻转需要重新编码"
    if info.container not in DISPLAY_MATRIX_CONTAINERS:
        return "仅 MP4／MOV 支持无损旋转"
    if not supported:
        return "当前 FFmpeg 不支持 -display_rotation（需要 6.1 或更高版本）"
    return None


def plan_rotate(
    p: RotateParams, src: Source, out: Path, *, display_rotation: bool = False
) -> OpPlan:
    path, info = src
    if lossless_rotation_blocker(p, info, display_rotation) is None:
        # The display matrix is counter-clockwise and replaces the source's rotation.
        rotation = (info.rotation - p.angle) % 360
        args = [f"-display_rotation:{info.video_index}", str(rotation), "-i", str(path)]
        args += [*maps(info, subtitles=True, container=info.container), "-c", "copy"]
        out = out.with_suffix("." + info.container)
        return OpPlan([[*args, *MP4_FLAGS, str(out)]], info.duration, ext=info.container)
    filters = {90: ["transpose=clock"], 180: ["hflip", "vflip"], 270: ["transpose=cclock"]}.get(
        p.angle, []
    )
    if p.flip == "horizontal":
        filters.append("hflip")
    elif p.flip == "vertical":
        filters.append("vflip")
    args = ["-i", str(path), *maps(info, subtitles=True), "-vf", ",".join(filters)]
    args += [*video_target(info).args(p.crf), *audio_for_mp4(info), *subtitle_codec(info)]
    args += [*MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration)


def _fast_cut(path: Path, info: MediaInfo, start: float, end: float, out: Path) -> list[str]:
    """Stream-copy one segment; cuts land on keyframes the caller already snapped to."""
    seek = start
    if info.container in {"mkv", "webm"} and info.video_has_b_frames:
        # FFmpeg backs a seek off by 3/23 s for demuxers that seek by DTS when
        # B-frames delay output; Matroska would then land on the previous keyframe.
        seek = start + MATROSKA_SEEK_BACKOFF + 0.001
        if end - seek <= 0.01:
            seek = start
    # -t counts from the requested seek point, not from the copied keyframe.
    args = ["-ss", f"{seek:.6f}", "-i", str(path), "-t", f"{end - seek:.6f}"]
    # Copied subtitle packets keep cues from before the cut and overrun the end.
    args += maps(info)
    args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    if out.suffix in {".mp4", ".mov", ".m4v"}:
        args += MP4_FLAGS
    return [*args, str(out)]


def concat_listing(paths: list[Path], outpoints: list[float] | None = None) -> str:
    """Concat demuxer script; an outpoint stops each file at its intended length."""
    lines = []
    for i, path in enumerate(paths):
        lines.append("file '" + str(path).replace("'", "'\\''") + "'\n")
        if outpoints:
            lines.append(f"outpoint {outpoints[i]:.6f}\n")
    return "".join(lines)


def plan_trim(p: TrimParams, src: Source, out: Path, tmp: Path | None = None) -> OpPlan:
    path, info = src
    segs: list[tuple[float, float]] = []
    for seg in p.segments:
        end = min(seg.end, info.duration) if info.duration else seg.end
        if end - seg.start > 0.01:
            segs.append((seg.start, end))
    if not segs:
        raise OpError("剪辑区间超出视频时长")
    total = sum(e - s for s, e in segs)

    if p.mode == "fast":
        ext = copy_ext(info)
        out = out.with_suffix("." + ext)
        if len(segs) == 1:
            return OpPlan([_fast_cut(path, info, *segs[0], out)], total, ext=ext)
        if tmp is None:
            raise OpError("多段快速剪辑需要临时目录")
        # Cut each segment by stream copy, then join them with the concat demuxer.
        parts = [tmp / f"part{i}.{ext}" for i in range(len(segs))]
        commands = [
            _fast_cut(path, info, s, e, part) for (s, e), part in zip(segs, parts, strict=True)
        ]
        listing = tmp / "trim-concat.txt"
        # Copied parts overrun by up to a frame and an AAC packet; without outpoints the
        # demuxer offsets each following part by that overrun.
        listing.write_text(concat_listing(parts, [e - s for s, e in segs]))
        args = ["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0", "-c", "copy"]
        if ext in {"mp4", "mov", "m4v"}:
            args += MP4_FLAGS
        commands.append([*args, str(out)])
        return OpPlan(commands, total, ext=ext, files=[listing, *parts])

    if len(segs) == 1:
        start, end = segs[0]
        args = ["-ss", f"{start:.3f}", "-i", str(path), "-t", f"{end - start:.3f}"]
        args += [*maps(info, subtitles=True), *video_target(info).args(p.crf), *aac()]
        args += [*subtitle_codec(info), *MP4_FLAGS, str(out)]
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
    args += [*video_target(info).args(p.crf), *MP4_FLAGS, str(out)]
    return OpPlan([args], total)


# H.264 profiles libx264 can match; the concat demuxer converts H.264 parts to Annex B
# with their own in-band parameter sets, so re-encoded and copied parts can be joined.
SMART_PROFILES = {
    "Constrained Baseline": "baseline",
    "Baseline": "baseline",
    "Main": "main",
    "High": "high",
}


def smart_cut_blocker(info: MediaInfo) -> str | None:
    """Why a smart cut falls back to a full re-encode, or None when it can run."""
    if info.video_codec != "h264":
        return "智能剪切仅支持 H.264 视频"
    if info.video_profile not in SMART_PROFILES or info.pix_fmt not in {"yuv420p", "yuvj420p"}:
        return "智能剪切仅支持 8-bit 4:2:0 的 Baseline／Main／High 档次"
    if info.interlaced or info.is_hdr:
        return "隔行扫描或 HDR 视频无法智能剪切"
    if info.rotation:
        return "带旋转标记的视频无法智能剪切"
    if info.fps <= 0:
        return "无法获取帧率"
    return None


@dataclass
class SmartPart:
    start: int  # first frame index
    end: int  # frame index after the last frame
    copy: bool


def smart_parts(
    frames: list[float], keyframes: list[float], start: float, end: float
) -> list[SmartPart]:
    """Copy whole GOPs inside [start, end) and re-encode the partial GOPs at both cuts.

    Assumes closed GOPs: frames shown before a keyframe are decoded before it.
    """
    first = bisect.bisect_left(frames, start - 1e-6)
    last = bisect.bisect_left(frames, end - 1e-6)
    if first >= last:
        return []
    keys = {round(k, 6) for k in keyframes}
    inside = [i for i in range(first, last) if round(frames[i], 6) in keys]
    if not inside:
        return [SmartPart(first, last, False)]
    copy_start = inside[0]
    # The copy may run to the cut when the cut is a keyframe or the end of the file.
    copy_end = last if last == len(frames) or round(frames[last], 6) in keys else inside[-1]
    parts = []
    if first < copy_start:
        parts.append(SmartPart(first, copy_start, False))
    if copy_start < copy_end:
        parts.append(SmartPart(copy_start, copy_end, True))
    if copy_end < last:
        parts.append(SmartPart(copy_end, last, False))
    return parts


def plan_smart_trim(
    p: TrimParams,
    src: Source,
    out: Path,
    tmp: Path,
    frames: list[float],
    keyframes: list[float],
) -> OpPlan:
    path, info = src
    profile = SMART_PROFILES[info.video_profile]
    encode = ["-c:v", "libx264", "-preset", "medium", "-crf", str(p.crf), "-profile:v", profile]
    if info.video_level:
        encode += ["-level:v", f"{info.video_level / 10:.1f}"]
    encode += ["-pix_fmt", info.pix_fmt, "-fps_mode", "passthrough"]
    video_only = ["-map", f"0:{info.video_index}", "-an", "-sn", "-dn"]
    commands: list[list[str]] = []
    parts: list[Path] = []
    spans: list[tuple[float, float]] = []
    for segment in p.segments:
        segment_parts = smart_parts(frames, keyframes, segment.start, segment.end)
        if not segment_parts:
            continue
        first, last = segment_parts[0].start, segment_parts[-1].end
        spans.append((frames[first], frames[last] if last < len(frames) else info.duration))
        for part in segment_parts:
            target = tmp / f"smart{len(parts)}.mp4"
            count = ["-frames:v", str(part.end - part.start)]
            if part.copy:
                seek = frames[part.start] + 0.001
                if info.container in {"mkv", "webm"} and info.video_has_b_frames:
                    seek += MATROSKA_SEEK_BACKOFF
                args = ["-ss", f"{seek:.6f}", "-i", str(path), *count, *video_only]
                args += ["-c", "copy", "-avoid_negative_ts", "make_zero", str(target)]
            else:
                # Accurate seek drops frames before -ss; a quarter frame early keeps the
                # first frame despite timestamp rounding but not the one before it.
                seek = max(0.0, frames[part.start] - 0.25 / info.fps)
                args = ["-ss", f"{seek:.6f}", "-i", str(path), *count, *video_only, *encode]
                args.append(str(target))
            commands.append(args)
            parts.append(target)
    if not parts:
        raise OpError("剪辑区间超出视频时长")
    total = sum(e - s for s, e in spans)
    listing = tmp / "smart-concat.txt"
    listing.write_text(concat_listing(parts))
    files = [listing, *parts]
    final = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    if info.has_audio:
        # One continuous audio encode avoids AAC priming gaps at every join.
        audio = tmp / "smart-audio.m4a"
        chains, labels = [], []
        for track, index in enumerate(audio_indices(info)):
            pieces = [f"[s{track}_{i}]" for i in range(len(spans))]
            split = "".join(pieces)
            chains.append(f"[0:{index}]asplit={len(spans)}{split}" if len(spans) > 1 else "")
            trimmed = []
            for i, (s, e) in enumerate(spans):
                source = pieces[i] if len(spans) > 1 else f"[0:{index}]"
                chains.append(
                    f"{source}atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS[t{track}_{i}]"
                )
                trimmed.append(f"[t{track}_{i}]")
            chains.append(f"{''.join(trimmed)}concat=n={len(spans)}:v=0:a=1[a{track}]")
            labels.append(f"[a{track}]")
        graph = ";".join(chain for chain in chains if chain)
        args = ["-i", str(path), "-filter_complex", graph]
        for label in labels:
            args += ["-map", label]
        commands.append([*args, *aac(), str(audio)])
        files.append(audio)
        final += ["-i", str(audio), "-map", "0:v", "-map", "1:a"]
    else:
        final += ["-map", "0:v"]
    commands.append([*final, "-c", "copy", *MP4_FLAGS, str(out.with_suffix(".mp4"))])
    return OpPlan(commands, total, files=files)


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
            i.color_transfer,
            i.color_primaries,
            i.color_space,
            i.color_range,
            i.audio_codec,
            i.sample_rate,
            i.channels,
        )
        for _, i in sources
    ]
    return all(k == keys[0] for k in keys)


def plan_merge(p: MergeParams, sources: list[Source], out: Path, tmp: Path) -> OpPlan:
    if p.transition != "none":
        from .transitions import plan_transitions

        return plan_transitions(p, sources, out)
    total = sum(i.duration for _, i in sources)
    lossless = p.mode == "lossless" or (p.mode == "auto" and merge_lossless_ok(sources))
    if p.mode == "lossless" and not merge_lossless_ok(sources):
        raise OpError("视频编码/分辨率不一致，无法无损合并，请选择重新编码")

    if lossless:
        first = sources[0][1]
        listing = tmp / "concat.txt"
        listing.write_text(concat_listing([path for path, _ in sources]))
        # Only the primary tracks: later files need not carry the same extra tracks.
        primary = ["-map", f"0:{first.video_index}"]
        if first.audio_index is not None:
            primary += ["-map", f"0:{first.audio_index}"]
        args = ["-f", "concat", "-safe", "0", "-i", str(listing), *primary]
        args += ["-c", "copy", *MP4_FLAGS, str(out)]
        return OpPlan([args], total, files=[listing])

    first = sources[0][1]
    w = even(p.width or first.width)
    h = even(p.height or first.height)
    fps = p.fps or (min(processed_fps(first), 60) if first.output_fps else 30)
    any_audio = any(i.has_audio for _, i in sources)
    target = video_target(*(info for _, info in sources))

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
        # Every source contributes exactly its duration to both tracks. A video track
        # shorter than its audio (common with variable frame rate) would otherwise shift
        # every later source against its sound.
        length = info.duration
        chains.append(
            f"[{idx}:{info.video_index}]setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format={target.pix_fmt},"
            f"tpad=start_mode=clone:start_duration={max(0, info.video_delay):.6f}:"
            f"stop_mode=clone:stop_duration={length:.6f},trim=duration={length:.6f},"
            f"setpts=PTS-STARTPTS[v{idx}]"
        )
        labels.append(f"[v{idx}]")
        if any_audio:
            a_in = (
                f"{silent_inputs[idx]}:0" if idx in silent_inputs else f"{idx}:{info.audio_index}"
            )
            delay = max(0, round(info.audio_delay * 48000)) if idx not in silent_inputs else 0
            chains.append(
                f"[{a_in}]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS,adelay={delay}S:all=1,apad,atrim=duration={length:.6f},"
                f"asetpts=PTS-STARTPTS[a{idx}]"
            )
            labels.append(f"[a{idx}]")
    n = len(sources)
    graph = ";".join(chains) + ";" + "".join(labels)
    graph += f"concat=n={n}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else "")
    args += ["-filter_complex", graph, "-map", "[v]"]
    if any_audio:
        args += ["-map", "[a]", *aac()]
    args += [*target.args(p.crf), "-fps_mode", "cfr", *MP4_FLAGS, str(out)]
    return OpPlan([args], total)


CRF_TABLE = {
    "h264": {"high": 23, "medium": 27, "low": 31},
    "h265": {"high": 26, "medium": 29, "low": 33},
    # SVT-AV1 uses a 0-63 scale.
    "av1": {"high": 30, "medium": 36, "low": 42},
}
ENCODERS = {"h264": "libx264", "h265": "libx265", "av1": "libsvtav1"}
# SVT-AV1 presets (0 slowest .. 13 fastest) for the x264-style preset names; 11+ is
# documented as unsuitable for viewing.
SVT_PRESETS = {"ultrafast": 10, "veryfast": 9, "faster": 8, "fast": 7, "medium": 6, "slow": 4}


def plan_compress(
    p: CompressParams,
    src: Source,
    out: Path,
    tmp: Path,
    *,
    video_filters: list[str] | None = None,
    audio_filters: list[str] | None = None,
    input_options: list[str] | None = None,
    subtitles: bool = True,
) -> OpPlan:
    path, info = src
    w, h = scaled_dims(info, p.resolution)
    filters = list(video_filters or [])
    if (w, h) != (even(info.width), even(info.height)) or info.width % 2 or info.height % 2:
        filters.append(f"scale={w}:{h}")
    if p.max_fps and processed_fps(info) > p.max_fps:
        filters.append(f"fps={p.max_fps}")
    # H.265/AV1 keep a 10-bit SDR source at 10 bits; H.264 stays 8-bit for browsers.
    ten_bit = p.codec != "h264" and info.high_bit_depth and not info.is_hdr
    pix_fmt = "yuv420p10le" if ten_bit else "yuv420p"
    filters.append(f"format={pix_fmt}")
    vf = ["-vf", ",".join(filters), "-pix_fmt", pix_fmt]
    encoder = ENCODERS[p.codec]
    preset = str(SVT_PRESETS[p.preset]) if p.codec == "av1" else p.preset
    tag = ["-tag:v", "hvc1"] if p.codec == "h265" else []
    audio = aac(p.audio_bitrate) if info.has_audio else []
    if info.has_audio and audio_filters:
        audio += ["-af", ",".join(audio_filters)]
    input_args = [*(input_options or []), "-i", str(path)]
    stream_maps = maps(info, subtitles=subtitles)
    if subtitles:
        audio += subtitle_codec(info)

    if p.target_size_mb is None:
        args = [*input_args, *stream_maps, *vf, "-c:v", encoder, "-preset", preset]
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
        if p.codec == "h265":
            return ["-x265-params", f"pass={n}:stats={log}.log"]
        return ["-pass", str(n), "-passlogfile", str(log)]

    rate = ["-b:v", f"{video_kbps}k"]
    if p.codec != "av1":
        # SVT-AV1 only accepts a maximum bitrate in capped-CRF mode.
        rate += ["-maxrate", f"{int(video_kbps * 1.5)}k", "-bufsize", f"{video_kbps * 2}k"]
    common = [*input_args, *vf, "-c:v", encoder, "-preset", preset, *rate]
    pass1 = [*common, *pass_args(1), "-map", f"0:{info.video_index}", "-an", "-f", "null", "-"]
    pass2 = [*common, *pass_args(2), *stream_maps, *tag, *audio, *MP4_FLAGS, str(out)]
    return OpPlan([pass1, pass2], info.duration)


def plan_mixed(p: MixedParams, src: Source, out: Path, tmp: Path) -> OpPlan:
    path, source_info = src
    info = replace(source_info)
    filters: list[str] = []
    audio_filters: list[str] = []
    input_options: list[str] = []
    if p.trim:
        end = min(p.trim.end, info.duration)
        if end - p.trim.start <= 0.01:
            raise OpError("剪辑区间超出视频时长")
        info.duration = end - p.trim.start
        input_options = ["-ss", str(p.trim.start), "-t", str(info.duration)]
    if p.crop:
        c = p.crop
        if c.x + c.width > info.width or c.y + c.height > info.height:
            raise OpError("裁切区域超出画面范围")
        info.width, info.height = c.width // 2 * 2, c.height // 2 * 2
        filters.append(f"crop={info.width}:{info.height}:{c.x}:{c.y}")
    if p.rotate:
        r = p.rotate
        rotations = {90: ["transpose=clock"], 180: ["hflip", "vflip"], 270: ["transpose=cclock"]}
        filters += rotations.get(r.angle, [])
        if r.angle in (90, 270):
            info.width, info.height = info.height, info.width
        if r.flip != "none":
            filters.append("hflip" if r.flip == "horizontal" else "vflip")
    if p.speed:
        filters.append(f"setpts=PTS/{p.speed.factor}")
        audio_filters.append(atempo_chain(p.speed.factor))
        info.duration /= p.speed.factor
    if p.mute:
        info.audio_codec = None
        info.audio_index = None
    return plan_compress(
        p.compress,
        (path, info),
        out,
        tmp,
        video_filters=filters,
        audio_filters=audio_filters,
        input_options=input_options,
        # Subtitle timestamps cannot follow a speed change.
        subtitles=not p.speed,
    )


def plan_crop(p: CropParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    if p.x + p.width > info.width or p.y + p.height > info.height:
        raise OpError("裁切区域超出画面范围")
    w, h = p.width // 2 * 2, p.height // 2 * 2
    args = ["-i", str(path), *maps(info, subtitles=True), "-vf", f"crop={w}:{h}:{p.x}:{p.y}"]
    args += [*video_target(info).args(p.crf), *audio_for_mp4(info), *subtitle_codec(info)]
    args += [*MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration)


def plan_speed(p: SpeedParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    args = ["-i", str(path), "-filter:v", f"setpts=PTS/{p.factor}", *maps(info)]
    if info.has_audio:
        args += ["-filter:a", atempo_chain(p.factor), *aac()]
    args += [*video_target(info).args(p.crf), *MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration / p.factor)


def plan_repair(src: Source, out: Path) -> OpPlan:
    path, info = src
    ext = copy_ext(info)
    out = out.with_suffix("." + ext)
    args = ["-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt", "-i", str(path)]
    args += [*maps(info, subtitles=True, container=ext), "-c", "copy"]
    args += ["-avoid_negative_ts", "make_zero"]
    if ext in {"mp4", "mov", "m4v"}:
        args += MP4_FLAGS
    return OpPlan([[*args, str(out)]], info.duration, ext=ext)


def plan_mute(src: Source, out: Path) -> OpPlan:
    path, info = src
    ext = copy_ext(info)
    out = out.with_suffix("." + ext)
    args = ["-i", str(path), *maps(info, audio=False, subtitles=True, container=ext)]
    args += ["-c", "copy", "-an", str(out)]
    return OpPlan([args], info.duration, ext=ext)


def plan_convert(p: ConvertParams, src: Source, out: Path) -> OpPlan:
    path, info = src
    out = out.with_suffix("." + p.format)
    args = ["-i", str(path), *maps(info, subtitles=True, container=p.format)]
    if p.format == "mkv":
        args += ["-c", "copy"]
    elif p.format == "mp4":
        # Copy whatever MP4 can hold; only incompatible tracks are re-encoded.
        args += ["-c:v", "copy"] if info.video_codec in {"h264", "av1"} else x264(21)
        if info.has_audio:
            copy = all(codec in {"aac", "mp3", "opus"} for codec in audio_codecs(info))
            args += ["-c:a", "copy"] if copy else aac()
        args += [*subtitle_codec(info), *MP4_FLAGS]
    else:
        args += [
            "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
            "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p",
        ]  # fmt: skip
        if info.has_audio:
            args += ["-c:a", "libopus", "-b:a", "128k"]
        args += subtitle_codec(info, "webm")
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
    args = ["-i", str(path), "-i", str(cover), *maps(info, subtitles=True), "-map", "1:0"]
    args += ["-c", "copy", "-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
    args += [*MP4_FLAGS, str(out)]
    return OpPlan([args], info.duration, ext=ext)


LOUDNESS_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


AUDIO_COMMON = "aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS"
NOISE_WINDOW = 4800  # 100 ms at 48 kHz


def _original_alignment(info: MediaInfo) -> str:
    alignment = f"atrim=start={max(0, -info.audio_delay)},{AUDIO_COMMON}"
    if info.audio_delay > 0:
        alignment += f",adelay={round(info.audio_delay * 1000)}:all=1"
    return alignment


def noise_floor_command(src: Source, output: str) -> list[str]:
    """Write the RMS level of each 100 ms window of the original track to `output`."""
    path, info = src
    graph = (
        f"[0:{info.audio_index}]{_original_alignment(info)},asetnsamples=n={NOISE_WINDOW},"
        "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level,"
        f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file={output}[m]"
    )
    return ["-i", str(path), "-filter_complex", graph, "-map", "[m]", "-f", "null", "-"]


def _audio_graph(
    p: AudioParams, src: Source, asset: tuple[Path, int] | None, noise_floor: float | None = None
) -> tuple[list[str], list[str], str]:
    """Inputs and filter chains up to the selected (unnormalised) audio label."""
    path, info = src
    duration = info.duration
    if duration <= 0:
        raise OpError("无法获取视频时长")
    if p.fade_in + p.fade_out > duration:
        raise OpError("淡入与淡出总时长不能超过视频时长")
    if p.mode != "adjust" and p.offset >= duration:
        raise OpError("音频开始时间必须小于视频时长")
    if p.mode == "adjust" and not info.has_audio:
        raise OpError("该视频没有音轨，请选择替换音轨或背景音乐")
    args = ["-i", str(path)]
    chains = []
    common = AUDIO_COMMON
    original = p.mode in {"adjust", "mix"} and info.has_audio
    if original:
        alignment = _original_alignment(info)
        if p.denoise and noise_floor is not None:
            # afftdn only removes noise near its floor; noise tracking alone barely acts.
            alignment += f",afftdn=nr={p.denoise}:nf={noise_floor:.1f}"
        chains.append(
            f"[0:{info.audio_index}]{alignment},volume={p.gain_db}dB,apad,atrim=duration={duration}[original]"
        )
    if p.mode != "adjust":
        if asset is None:
            raise OpError("音频素材不存在")
        audio_path, stream_index = asset
        if p.loop:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(audio_path)]
        delay = round(p.offset * 1000)
        chains.append(
            f"[1:{stream_index}]{common},volume={p.music_gain_db}dB,adelay={delay}:all=1,apad,atrim=duration={duration}[music]"
        )
    if p.mode == "mix" and original:
        chains.append(
            "[original][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mixed]"
        )
        selected = "mixed"
    else:
        selected = "original" if p.mode == "adjust" else "music"
    return args, chains, selected


def loudness_command(
    p: AudioParams,
    src: Source,
    asset: tuple[Path, int] | None = None,
    noise_floor: float | None = None,
) -> list[str]:
    """First loudnorm pass: measure exactly the audio the final pass will normalise."""
    args, chains, selected = _audio_graph(p, src, asset, noise_floor)
    chains.append(f"[{selected}]loudnorm=I={p.target_lufs}:TP=-1.5:LRA=11:print_format=json[m]")
    args += ["-filter_complex", ";".join(chains), "-map", "[m]", "-t", str(src[1].duration)]
    return [*args, "-f", "null", "-"]


def plan_audio(
    p: AudioParams,
    src: Source,
    out: Path,
    asset: tuple[Path, int] | None = None,
    measured: dict[str, float] | None = None,
    noise_floor: float | None = None,
) -> OpPlan:
    path, info = src
    duration = info.duration
    args, chains, selected = _audio_graph(p, src, asset, noise_floor)
    ext = copy_ext(info)
    if ext == "webm":
        ext = "mkv"  # WebM cannot hold the AAC output.
    filters = []
    if p.normalize:
        loudnorm = f"loudnorm=I={p.target_lufs}:TP=-1.5:LRA=11"
        if measured:
            # Second pass: a single gain keeps the dynamics when the targets allow it.
            i, tp, lra, thresh, offset = (measured[key] for key in LOUDNESS_KEYS)
            loudnorm += (
                f":measured_I={i}:measured_TP={tp}:measured_LRA={lra}"
                f":measured_thresh={thresh}:offset={offset}:linear=true"
            )
        else:
            loudnorm += ":linear=false"
        filters.append(loudnorm)
    if p.fade_in:
        filters.append(f"afade=t=in:st=0:d={p.fade_in}")
    if p.fade_out:
        filters.append(f"afade=t=out:st={duration - p.fade_out}:d={p.fade_out}")
    filters += ["alimiter=limit=0.95:level=false:latency=true", "aresample=48000"]
    chains.append(f"[{selected}]" + ",".join(filters) + "[audio_out]")
    target = out.with_suffix("." + ext)
    args += [
        "-filter_complex",
        ";".join(chains),
        "-map",
        f"0:{info.video_index}",
        "-map",
        "[audio_out]",
        "-c:v",
        "copy",
        *aac(192),
        "-ar",
        "48000",
        "-t",
        str(duration),
    ]
    if ext in {"mp4", "mov", "m4v"}:
        args += MP4_FLAGS
    return OpPlan([args + [str(target)]], duration, ext=ext)
