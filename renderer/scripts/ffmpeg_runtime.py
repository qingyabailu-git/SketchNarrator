#!/usr/bin/env python3
"""Resolve and inspect the isolated FFmpeg runtime used by the renderer."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


def _executable_name(name: str) -> str:
    return f"{name}.exe" if sys.platform.startswith("win") else name


def _normalise_candidate(value: str | Path | None, executable: str) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        candidate = candidate / _executable_name(executable)
    try:
        return candidate.resolve()
    except OSError:
        return candidate.absolute()


def _run(command: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _valid_executable(path: Path | None, version_flag: str = "-version") -> bool:
    if path is None or not path.is_file():
        return False
    result = _run([str(path), version_flag])
    return bool(result and result.returncode == 0)


def _imageio_ffmpeg_candidate() -> Path | None:
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        return _normalise_candidate(imageio_ffmpeg.get_ffmpeg_exe(), "ffmpeg")
    except Exception:
        return None


def _candidate_paths(explicit: str | Path | None = None) -> Iterable[tuple[str, Path | None]]:
    yield "explicit", _normalise_candidate(explicit, "ffmpeg")
    yield "WHITEBOARD_FFMPEG", _normalise_candidate(os.environ.get("WHITEBOARD_FFMPEG"), "ffmpeg")
    yield "IMAGEIO_FFMPEG_EXE", _normalise_candidate(os.environ.get("IMAGEIO_FFMPEG_EXE"), "ffmpeg")
    yield "PATH", _normalise_candidate(shutil.which("ffmpeg"), "ffmpeg")
    yield "imageio-ffmpeg", _imageio_ffmpeg_candidate()


def resolve_ffmpeg(explicit: str | Path | None = None) -> tuple[Path | None, str | None, list[str]]:
    errors: list[str] = []
    seen: set[str] = set()
    for source, candidate in _candidate_paths(explicit):
        if candidate is None:
            continue
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if _valid_executable(candidate):
            return candidate, source, errors
        errors.append(f"{source} 指向不可用的 FFmpeg：{candidate}")
    return None, None, errors


def resolve_ffprobe(ffmpeg: Path | None = None) -> tuple[Path | None, str | None]:
    candidates: list[tuple[str, Path | None]] = [
        ("WHITEBOARD_FFPROBE", _normalise_candidate(os.environ.get("WHITEBOARD_FFPROBE"), "ffprobe")),
    ]
    if ffmpeg is not None:
        candidates.append(("FFmpeg sibling", ffmpeg.with_name(_executable_name("ffprobe"))))
    candidates.append(("PATH", _normalise_candidate(shutil.which("ffprobe"), "ffprobe")))
    seen: set[str] = set()
    for source, candidate in candidates:
        if candidate is None:
            continue
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if _valid_executable(candidate):
            return candidate, source
    return None, None


def _first_line(value: str) -> str:
    return value.splitlines()[0].strip() if value.strip() else ""


def probe_capabilities(ffmpeg: Path) -> dict[str, Any]:
    version_result = _run([str(ffmpeg), "-version"])
    encoders_result = _run([str(ffmpeg), "-hide_banner", "-encoders"])
    filters_result = _run([str(ffmpeg), "-hide_banner", "-filters"])
    demuxers_result = _run([str(ffmpeg), "-hide_banner", "-demuxers"])
    version = _first_line(version_result.stdout if version_result else "")
    encoders = (encoders_result.stdout + encoders_result.stderr) if encoders_result else ""
    filters = (filters_result.stdout + filters_result.stderr) if filters_result else ""
    demuxers = (demuxers_result.stdout + demuxers_result.stderr) if demuxers_result else ""
    hardware = [name for name in ("h264_nvenc", "h264_qsv", "h264_amf") if name in encoders]
    return {
        "version": version,
        "libx264": bool(re.search(r"\blibx264\b", encoders)),
        "subtitles": bool(re.search(r"\b(?:subtitles|ass)\b", filters)),
        "ass": bool(re.search(r"\bass\b", filters)),
        "concat_demuxer": bool(re.search(r"\bconcat\b", demuxers)),
        "hardware_h264_encoders": hardware,
    }


_RUNTIME_INFO_CACHE: dict[str, dict[str, Any]] = {}


def clear_runtime_cache() -> None:
    _RUNTIME_INFO_CACHE.clear()


def runtime_info(explicit: str | Path | None = None, use_cache: bool = True) -> dict[str, Any]:
    cache_key = str(explicit) if explicit is not None else "__default__"
    if use_cache and cache_key in _RUNTIME_INFO_CACHE:
        return _RUNTIME_INFO_CACHE[cache_key]

    ffmpeg, source, errors = resolve_ffmpeg(explicit)
    ffprobe, ffprobe_source = resolve_ffprobe(ffmpeg)
    capabilities = probe_capabilities(ffmpeg) if ffmpeg else {
        "version": "",
        "libx264": False,
        "subtitles": False,
        "ass": False,
        "concat_demuxer": False,
        "hardware_h264_encoders": [],
    }
    result = {
        "available": ffmpeg is not None,
        "path": str(ffmpeg) if ffmpeg else None,
        "source": source,
        "capabilities": capabilities,
        "ffprobe": {
            "available": ffprobe is not None,
            "path": str(ffprobe) if ffprobe else None,
            "source": ffprobe_source,
        },
        "errors": errors,
    }
    if use_cache:
        _RUNTIME_INFO_CACHE[cache_key] = result
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="发现并检查白板渲染器使用的 FFmpeg")
    parser.add_argument("--ffmpeg", help="显式 FFmpeg 文件或 bin 目录")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--require", action="store_true", help="FFmpeg 或核心能力缺失时返回非零")
    parser.add_argument("--encode-check", action="store_true", help="实际编码一帧到空输出，不保存视频")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    info = runtime_info(args.ffmpeg)
    if args.encode_check:
        result = _run([info["path"], "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-f", "lavfi", "-i", "color=c=white:s=16x16:r=1",
                       "-frames:v", "1", "-c:v", "libx264", "-f", "null", "-"]) if info["available"] else None
        info["encode_check"] = "passed" if result and result.returncode == 0 else "failed"
        if info["encode_check"] != "passed":
            print("[err] FFmpeg 实际编码检查失败", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    elif info["available"]:
        caps = info["capabilities"]
        print(f"[ok] FFmpeg: {info['path']} ({info['source']})")
        print(
            "[ok] 能力: "
            f"libx264={'yes' if caps['libx264'] else 'no'}, "
            f"subtitles={'yes' if caps['subtitles'] else 'no'}, "
            f"concat={'yes' if caps['concat_demuxer'] else 'no'}"
        )
        if info["ffprobe"]["available"]:
            print(f"[ok] FFprobe: {info['ffprobe']['path']}")
        else:
            print("[warn] 未找到 FFprobe；媒体时长检查会使用 PyAV")
    else:
        print("[warn] 未找到可用 FFmpeg；编码与合成会使用较慢的 PyAV 回退")
        for error in info["errors"]:
            print(f"[warn] {error}")
    if args.require and (
        not info["available"]
        or not info["capabilities"]["libx264"]
        or not info["capabilities"]["concat_demuxer"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
