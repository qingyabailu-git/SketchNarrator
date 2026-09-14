#!/usr/bin/env python3
"""Small shared helper for measuring the actual decoded audio duration."""

from __future__ import annotations

import wave
import json
import shutil
import subprocess
from pathlib import Path


def decoded_audio_duration_ms(path: Path) -> int | None:
    if path.suffix.casefold() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                return round(handle.getnframes() / handle.getframerate() * 1000)
        except (wave.Error, OSError, ZeroDivisionError):
            pass
    try:
        import av

        with av.open(str(path)) as container:
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            if not audio_streams:
                return None
            stream = audio_streams[0]
            if stream.duration is not None and bool(stream.time_base):
                return round(float(stream.duration * stream.time_base) * 1000)
            if container.duration is not None:
                return round(float(container.duration) / 1000)
    except (ImportError, OSError, ValueError):
        pass
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            duration = float(json.loads(result.stdout)["format"]["duration"])
            return round(duration * 1000)
        except (OSError, subprocess.SubprocessError, KeyError, ValueError, json.JSONDecodeError):
            pass
    return None
