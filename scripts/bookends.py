#!/usr/bin/env python3
"""Compose optional external intro/outro clips around a finished main video."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


class BookendError(ValueError):
    """Raised when external bookend media cannot be composed safely."""


_HEX_COLOR = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _pyav_stream_seconds(stream: Any) -> float:
    duration = getattr(stream, "duration", None)
    time_base = getattr(stream, "time_base", None)
    if duration is None or time_base is None:
        return 0.0
    return float(duration * time_base)


def _metadata_seconds(value: Any) -> float:
    """Return a usable metadata duration without trusting values such as ``N/A``."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    return seconds if seconds > 0 else 0.0


def _probe_with_pyav(path: Path) -> dict[str, Any]:
    """Read stream metadata only; never scan all frames to estimate duration."""
    try:
        import av
        with av.open(str(path)) as container:
            videos = list(container.streams.video)
            audios = list(container.streams.audio)
            if not videos or not audios:
                raise BookendError(f"固定片段必须同时包含视频轨和音轨：{path}")
            video = videos[0]
            audio = audios[0]
            format_seconds = float(container.duration or 0) / 1_000_000
            video_seconds = _pyav_stream_seconds(video) or format_seconds
            audio_seconds = _pyav_stream_seconds(audio) or format_seconds
            if video_seconds <= 0:
                raise BookendError(f"媒体缺少可用时长元数据：{path}；可设置 WHITEBOARD_FFPROBE 后重新探测，不逐帧扫描")
            fps = float(video.average_rate or 0)
            width, height = int(video.codec_context.width), int(video.codec_context.height)
            if fps <= 0 or width <= 0 or height <= 0:
                raise BookendError(f"媒体缺少有效帧率或尺寸：{path}")
            return {
                "duration_ms": round(video_seconds * 1000),
                "video_duration_ms": round(video_seconds * 1000),
                "audio_duration_ms": round(audio_seconds * 1000),
                "format_duration_ms": round(format_seconds * 1000),
                "width": width,
                "height": height,
                "fps": fps,
            }
    except BookendError:
        raise
    except (ImportError, OSError, ValueError, TypeError) as exc:
        raise BookendError(f"PyAV 无法读取媒体信息：{path}：{exc}") from exc


def probe_media(ffprobe: str | Path | None, path: Path) -> dict[str, Any]:
    if not ffprobe:
        return _probe_with_pyav(path)
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise BookendError(f"无法读取固定片段媒体信息：{path}：{result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BookendError(f"固定片段媒体信息不是有效 JSON：{path}") from exc
    streams = payload.get("streams") if isinstance(payload, dict) else None
    fmt = payload.get("format") if isinstance(payload, dict) else None
    if not isinstance(streams, list) or not isinstance(fmt, dict):
        raise BookendError(f"固定片段媒体信息格式无效：{path}")
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not isinstance(video, dict) or not isinstance(audio, dict):
        raise BookendError(f"固定片段必须同时包含视频轨和音轨：{path}")
    format_seconds = _metadata_seconds(fmt.get("duration"))
    video_seconds = _metadata_seconds(video.get("duration")) or format_seconds
    audio_seconds = _metadata_seconds(audio.get("duration")) or format_seconds
    duration_ms = round(video_seconds * 1000)
    if duration_ms <= 0:
        raise BookendError(f"固定片段时长无效：{path}")
    rate = str(video.get("r_frame_rate") or "30/1")
    try:
        numerator, denominator = rate.split("/", 1)
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "duration_ms": duration_ms,
        "video_duration_ms": duration_ms,
        "audio_duration_ms": round(audio_seconds * 1000),
        "format_duration_ms": round(format_seconds * 1000),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
    }


def prepare_bookend_spec(project: dict[str, Any], ffprobe: str | Path | None) -> dict[str, Any] | None:
    """Validate and measure project bookends before the immutable render snapshot."""

    raw = project.get("bookends")
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    intro = Path(str(raw.get("intro") or "")).expanduser()
    outro = Path(str(raw.get("outro") or "")).expanduser()
    if not intro.is_absolute() or not intro.is_file():
        raise BookendError(f"开场固定片段不存在：{intro}")
    if not outro.is_absolute() or not outro.is_file():
        raise BookendError(f"结尾固定片段不存在：{outro}")
    intro_info = probe_media(ffprobe, intro)
    outro_info = probe_media(ffprobe, outro)
    fit_mode = str(raw.get("fit_mode") or "contain").strip().casefold()
    if fit_mode not in {"contain", "cover"}:
        raise BookendError(f"不支持的固定片段 fit_mode：{fit_mode}")
    color = str(raw.get("background_color") or "#10141c").strip()
    if not _HEX_COLOR.fullmatch(color):
        raise BookendError(f"固定片段 background_color 必须是六位十六进制颜色：{color}")
    return {
        "enabled": True,
        "intro": str(intro.resolve()),
        "outro": str(outro.resolve()),
        "fit_mode": fit_mode,
        "background_color": color,
        "source": str(raw.get("source") or "external-local"),
        "prefix_duration_ms": int(intro_info["duration_ms"]),
        "suffix_duration_ms": int(outro_info["duration_ms"]),
        "intro_media": intro_info,
        "outro_media": outro_info,
    }


def _background_color(value: str) -> str:
    return "0x" + value.lstrip("#")


def compose_bookends(
    ffmpeg: str | Path,
    main: Path,
    output: Path,
    spec: dict[str, Any],
    main_info: dict[str, Any],
    cwd: Path,
) -> int:
    """Scale/pad or crop three A/V clips and concatenate them with FFmpeg."""

    width = int(main_info.get("width") or 0)
    height = int(main_info.get("height") or 0)
    fps = float(main_info.get("fps") or 30.0)
    if width <= 0 or height <= 0:
        raise BookendError("主片分辨率无效，无法接入固定片段")
    color = _background_color(str(spec.get("background_color") or "#10141c"))
    fit_mode = str(spec.get("fit_mode") or "contain")
    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:"
        f"(ow-iw)/2:(oh-ih)/2:color={color}"
        if fit_mode == "contain"
        else f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    )
    clip_durations_ms = [
        int(spec["prefix_duration_ms"]),
        int(main_info["duration_ms"]),
        int(spec["suffix_duration_ms"]),
    ]
    if any(value <= 0 for value in clip_durations_ms):
        raise BookendError("固定片段或主片时长无效，无法建立统一音画时间线")
    total_duration_ms = sum(clip_durations_ms)
    video_filters = []
    audio_filters = []
    frame_seconds = 1.0 / fps
    for index, duration_ms in enumerate(clip_durations_ms):
        duration_seconds = duration_ms / 1000.0
        video_filters.append(
            f"[{index}:v:0]{scale},setsar=1,fps={fps:.6f},format=yuv420p,"
            f"trim=duration={duration_seconds:.6f},"
            f"tpad=stop_mode=clone:stop_duration={frame_seconds:.9f},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
        audio_filters.append(
            f"[{index}:a:0]aresample=48000,apad,atrim=duration={duration_seconds:.6f},"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(3))
    filter_complex = ";".join(
        [
            *video_filters,
            *audio_filters,
            f"{concat_inputs}concat=n=3:v=1:a=1[vcat][acat]",
            f"[vcat]trim=duration={total_duration_ms / 1000.0:.6f},setpts=PTS-STARTPTS[vout]",
            f"[acat]atrim=duration={total_duration_ms / 1000.0:.6f},asetpts=PTS-STARTPTS[aout]",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(spec["intro"]), "-i", str(main), "-i", str(spec["outro"]),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-r", f"{fps:.6f}", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            "-t", f"{total_duration_ms / 1000.0:.6f}",
            str(output),
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not output.is_file():
        raise BookendError(f"固定片段合成失败：{result.stderr.strip()}")
    return total_duration_ms
