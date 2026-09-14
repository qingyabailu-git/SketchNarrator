#!/usr/bin/env python3
"""Generate Chinese narration with free, no-key TTS providers.

Edge-TTS is the default because it returns word-boundary metadata. Piper is an
offline fallback; its audio must be aligned separately before the first gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from align_words import (
    DEFAULT_MAX_CAPTION_CHARS,
    DEFAULT_MAX_CAPTION_MS,
    DEFAULT_MAX_LINE_CHARS,
    PUNCTUATION,
    align_script_words,
    build_cues,
    write_srt,
)
from audio_probe import decoded_audio_duration_ms
from script_text import read_narration_text


DEFAULT_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"


def ticks_to_ms(value: int | float) -> int:
    """Convert Edge's 100-nanosecond ticks to milliseconds."""

    return round(float(value) / 10_000)


def edge_chunk_to_word(chunk: dict) -> dict | None:
    """Convert one Edge WordBoundary event to the shared words.json shape."""

    if chunk.get("type") != "WordBoundary":
        return None
    text = str(chunk.get("text", ""))
    if not text.strip() or all(char in PUNCTUATION for char in text.strip()):
        return None
    start_ms = ticks_to_ms(chunk["offset"])
    end_ms = ticks_to_ms(chunk["offset"] + chunk["duration"])
    return {"text": text, "start_ms": start_ms, "end_ms": max(start_ms + 1, end_ms)}


def write_edge_outputs(
    words: list[dict],
    out_dir: Path,
    voice: str,
    max_chars: int,
    max_caption_ms: int,
    script_text: str | None = None,
    max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
    audio_duration_ms: int | None = None,
) -> tuple[Path, Path]:
    if not words:
        raise ValueError("Edge-TTS 没有返回可用的逐词时间")
    if script_text:
        words = align_script_words(script_text, words)
    words_path = out_dir / "words.json"
    words_path.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "edge-tts-word-boundary",
                "provider": "edge",
                "voice": voice,
                "language": "zh-CN",
                "duration_ms": max(audio_duration_ms or 0, max(item["end_ms"] for item in words)),
                "words": words,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    captions_path = out_dir / "captions.srt"
    write_srt(
        build_cues(words, max_chars, max_caption_ms, script_text, max_line_chars),
        captions_path,
    )
    return words_path, captions_path


async def synthesize_edge(args: argparse.Namespace, text: str, out_dir: Path) -> tuple[Path, Path, Path]:
    try:
        import edge_tts
    except ImportError as error:
        raise RuntimeError("缺少 edge-tts；请在隔离环境中安装 edge-tts==7.2.8") from error

    audio_path = out_dir / "narration.mp3"
    words: list[dict] = []
    communicate = edge_tts.Communicate(
        text,
        voice=args.voice,
        rate=args.rate,
        volume=args.volume,
        pitch=args.pitch,
        boundary="WordBoundary",
    )
    with audio_path.open("wb") as audio:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            else:
                word = edge_chunk_to_word(chunk)
                if word is not None:
                    words.append(word)
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise RuntimeError("Edge-TTS 没有生成音频")
    display_text = text
    if args.display_script:
        display_text = read_narration_text(Path(args.display_script).resolve())
        if not display_text:
            raise RuntimeError("展示文案为空")
    words_path, captions_path = write_edge_outputs(
        words,
        out_dir,
        args.voice,
        args.max_chars,
        args.max_caption_ms,
        display_text,
        args.max_line_chars,
        decoded_audio_duration_ms(audio_path),
    )
    return audio_path, words_path, captions_path


def synthesize_piper(args: argparse.Namespace, text: str, out_dir: Path) -> Path:
    model_value = args.piper_model or os.environ.get("PIPER_MODEL_PATH")
    if not model_value:
        raise ValueError("PIPER_MODEL_PATH")
    model = Path(model_value).resolve()
    if not model.is_file():
        raise RuntimeError(f"Piper 模型不存在：{model}")
    try:
        import piper  # noqa: F401
    except ImportError as error:
        raise RuntimeError("缺少 piper-tts；请先运行 Skill 的 setup --provider piper") from error

    audio_path = out_dir / "narration.wav"
    tts_input = out_dir / "tts-input.txt"
    tts_input.write_text(text + "\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "piper",
        "-m",
        str(model),
        "-f",
        str(audio_path),
        "--input-file",
        str(tts_input),
    ]
    if args.piper_data_dir:
        command[3:3] = ["--data-dir", str(Path(args.piper_data_dir).resolve())]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Piper 生成失败：{detail}")
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise RuntimeError("Piper 没有生成音频")
    return audio_path


def run(args: argparse.Namespace) -> int:
    script = Path(args.script).resolve()
    if not script.is_file():
        print(f"[err] 口播稿不存在：{script}", file=sys.stderr)
        return 2
    text = read_narration_text(script)
    if not text:
        print("[err] 口播稿为空。", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.provider == "edge":
            audio_path, words_path, captions_path = asyncio.run(synthesize_edge(args, text, out_dir))
            print("PROVIDER=edge")
            print(f"AUDIO={audio_path}")
            print(f"WORDS={words_path}")
            print(f"CAPTIONS={captions_path}")
            return 0

        audio_path = synthesize_piper(args, text, out_dir)
        print("PROVIDER=piper")
        print(f"AUDIO={audio_path}")
        print("ALIGNMENT_REQUIRED=true")
        return 0
    except ValueError as error:
        if str(error) == "PIPER_MODEL_PATH":
            print("[needs-input] PIPER_MODEL_PATH", file=sys.stderr)
            return 3
        print(f"[err] {error}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as error:
        print(f"[err] {error}", file=sys.stderr)
        return 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="用免费 TTS 生成中文配音")
    p.add_argument("--script", required=True)
    p.add_argument("--display-script", help="正确书面语原稿；当 --script 是发音送读稿时使用")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--provider", choices=("edge", "piper"), default="edge")
    p.add_argument("--voice", default=DEFAULT_EDGE_VOICE)
    p.add_argument("--rate", default="+12%")
    p.add_argument("--volume", default="+0%")
    p.add_argument("--pitch", default="+0Hz")
    p.add_argument("--piper-model")
    p.add_argument("--piper-data-dir")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CAPTION_CHARS)
    p.add_argument("--max-caption-ms", type=int, default=DEFAULT_MAX_CAPTION_MS)
    p.add_argument("--max-line-chars", type=int, default=DEFAULT_MAX_LINE_CHARS)
    return p


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
