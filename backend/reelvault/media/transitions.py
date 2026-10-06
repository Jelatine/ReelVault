from __future__ import annotations

from pathlib import Path

from .ops import MP4_FLAGS, MergeParams, OpError, OpPlan, Source, aac, even, x264


def plan_transitions(p: MergeParams, sources: list[Source], out: Path) -> OpPlan:
    if len(sources) < 2:
        raise OpError("转场合并至少需要两个视频")
    t = p.transition_duration
    for index, (_, info) in enumerate(sources):
        required = t * (2 if 0 < index < len(sources) - 1 else 1)
        if info.duration <= t or info.duration + 1e-6 < required:
            raise OpError("转场时长必须小于每段时长，中间片段至少为转场时长的两倍")
    first = sources[0][1]
    w, h = even(p.width or first.width), even(p.height or first.height)
    fps = p.fps or (min(first.fps, 60) if first.fps else 30)
    any_audio = any(info.has_audio for _, info in sources)
    args: list[str] = []
    chains = []
    for path, _ in sources:
        args += ["-i", str(path)]
    for index, (_, info) in enumerate(sources):
        duration = info.duration
        chains.append(
            f"[{index}:{info.video_index}]setpts=PTS-STARTPTS,"
            f"scale=w='if(gt(dar,{w}/{h}),{w},max(2,trunc({h}*dar/2)*2))':"
            f"h='if(gt(dar,{w}/{h}),max(2,trunc({w}/dar/2)*2),{h})',setsar=1,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,"
            f"tpad=start_mode=clone:start_duration={max(0, info.video_delay):.6f}:"
            f"stop_mode=clone:stop_duration={duration:.6f},trim=duration={duration:.6f},"
            f"settb=AVTB,setpts=PTS-STARTPTS[v{index}]"
        )
        if any_audio:
            if info.has_audio:
                delay = max(0, round(info.audio_delay * 48000))
                chains.append(
                    f"[{index}:{info.audio_index}]aresample=48000,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS,"
                    f"adelay={delay}S:all=1,apad,atrim=duration={duration:.6f},"
                    f"asettb=1/48000,asetpts=PTS-STARTPTS[a{index}]"
                )
            else:
                chains.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f},"
                    f"asettb=1/48000,asetpts=PTS-STARTPTS[a{index}]"
                )
    total = first.duration
    previous_v, previous_a = "v0", "a0"
    for index in range(1, len(sources)):
        v, a = f"joined_v{index}", f"joined_a{index}"
        offset = total - t
        chains.append(
            f"[{previous_v}][v{index}]xfade=transition={p.transition}:"
            f"duration={t:.6f}:offset={offset:.6f}[{v}]"
        )
        if any_audio:
            chains.append(f"[{previous_a}][a{index}]acrossfade=d={t:.6f}:c1=tri:c2=tri[{a}]")
        total += sources[index][1].duration - t
        previous_v, previous_a = v, a
    args += ["-filter_complex", ";".join(chains), "-map", f"[{previous_v}]"]
    if any_audio:
        args += ["-map", f"[{previous_a}]", *aac()]
    args += [*x264(p.crf), "-t", f"{total:.6f}", *MP4_FLAGS, str(out)]
    return OpPlan([args], total)
