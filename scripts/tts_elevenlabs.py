#!/usr/bin/env python3
"""Generate narration and character timestamps with the ElevenLabs SDK."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from network_safety import safe_diagnostic

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


def alignment_to_words(alignment: object) -> list[dict]:
    characters = list(getattr(alignment, "characters"))
    starts = list(getattr(alignment, "character_start_times_seconds"))
    ends = list(getattr(alignment, "character_end_times_seconds"))
    if not (len(characters) == len(starts) == len(ends)):
        raise ValueError("ElevenLabs 对齐数组长度不一致")
    words: list[dict] = []
    for text, start, end in zip(characters, starts, ends):
        if not str(text).strip() or str(text) in PUNCTUATION:
            continue
        words.append({"text": str(text), "start_ms": round(float(start) * 1000), "end_ms": round(float(end) * 1000)})
    if not words:
        raise ValueError("ElevenLabs 没有返回可用的字符时间")
    return words


def run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = args.voice_id or os.environ.get("ELEVENLABS_VOICE_ID")
    missing = []
    if not api_key:
        missing.append("ELEVENLABS_API_KEY")
    if not voice_id:
        missing.append("ELEVENLABS_VOICE_ID")
    if missing:
        print("[needs-input] " + ", ".join(missing), file=sys.stderr)
        return 3
    try:
        from elevenlabs import ElevenLabs
    except ImportError:
        print("[err] 缺少 ElevenLabs SDK；请先运行 Skill 的 setup --provider elevenlabs。", file=sys.stderr)
        return 2

    script = Path(args.script).resolve()
    text = script.read_text(encoding="utf-8").strip()
    if not text:
        print("[err] 口播稿为空。", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        client = ElevenLabs(api_key=api_key)
        response = client.text_to_speech.convert_with_timestamps(
            voice_id=voice_id,
            text=text,
            output_format=args.output_format,
            model_id=args.model,
            seed=args.seed,
        )
    except Exception as error:
        print(f"[err] ElevenLabs 请求失败：{safe_diagnostic(error)}", file=sys.stderr)
        return 1
    alignment = response.normalized_alignment or response.alignment
    if alignment is None:
        print("[err] TTS 返回了音频，但没有时间对齐。", file=sys.stderr)
        return 1
    audio_path = out_dir / "narration.mp3"
    audio_path.write_bytes(base64.b64decode(response.audio_base_64))
    words = alignment_to_words(alignment)
    display_text = text
    if args.display_script:
        display_text = Path(args.display_script).resolve().read_text(encoding="utf-8").strip()
        if not display_text:
            print("[err] 展示文案为空。", file=sys.stderr)
            return 2
        words = align_script_words(display_text, words)
    measured_duration_ms = decoded_audio_duration_ms(audio_path)
    words_path = out_dir / "words.json"
    words_path.write_text(json.dumps({
        "version": 1,
        "source": "elevenlabs-character-alignment",
        "provider": "elevenlabs",
        "voice_id": voice_id,
        "model": args.model,
        "output_format": args.output_format,
        "language": "zh",
        "duration_ms": max(measured_duration_ms or 0, max(item["end_ms"] for item in words)),
        "words": words,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    captions_path = out_dir / "captions.srt"
    write_srt(
        build_cues(words, args.max_chars, args.max_caption_ms, display_text, args.max_line_chars),
        captions_path,
    )
    print(f"AUDIO={audio_path}")
    print(f"WORDS={words_path}")
    print(f"CAPTIONS={captions_path}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="用 ElevenLabs 生成中文配音和真实字符时间")
    p.add_argument("--script", required=True)
    p.add_argument("--display-script", help="正确书面语原稿；当 --script 是发音送读稿时使用")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--voice-id")
    p.add_argument("--model", default="eleven_multilingual_v2")
    p.add_argument("--output-format", default="mp3_44100_128")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CAPTION_CHARS)
    p.add_argument("--max-caption-ms", type=int, default=DEFAULT_MAX_CAPTION_MS)
    p.add_argument("--max-line-chars", type=int, default=DEFAULT_MAX_LINE_CHARS)
    return p


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
