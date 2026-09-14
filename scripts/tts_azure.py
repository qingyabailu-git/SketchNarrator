#!/usr/bin/env python3
"""Generate Azure Speech narration with native word-boundary timing."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from network_safety import safe_diagnostic
from xml.sax.saxutils import escape, quoteattr

from align_words import (
    DEFAULT_MAX_CAPTION_CHARS,
    DEFAULT_MAX_CAPTION_MS,
    DEFAULT_MAX_LINE_CHARS,
    PUNCTUATION,
    align_script_words,
    build_cues,
    write_srt,
)


DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
KEY_ENV = "AZURE_SPEECH_KEY"
REGION_ENV = "AZURE_SPEECH_REGION"


class AzureSpeechError(RuntimeError):
    """Concise error raised for Azure configuration or synthesis failures."""


def ticks_to_ms(value: int | float) -> int:
    """Convert Azure Speech 100-nanosecond ticks to milliseconds."""

    return round(float(value) / 10_000)


def duration_to_ms(value: object) -> int:
    """Convert a Speech SDK duration value to milliseconds."""

    if value is None:
        return 0
    if isinstance(value, timedelta) or hasattr(value, "total_seconds"):
        return round(float(value.total_seconds()) * 1000)
    return ticks_to_ms(float(value))


def boundary_type_name(value: object) -> str:
    if value is None:
        return "Word"
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return str(value).rsplit(".", 1)[-1]


def azure_event_to_word(event: object) -> dict | None:
    """Convert one SDK WordBoundary event to the shared words.json shape."""

    if boundary_type_name(getattr(event, "boundary_type", None)).lower() != "word":
        return None
    text = str(getattr(event, "text", ""))
    stripped = text.strip()
    if not stripped or all(char in PUNCTUATION for char in stripped):
        return None
    start_ms = ticks_to_ms(getattr(event, "audio_offset", 0))
    end_ms = start_ms + duration_to_ms(getattr(event, "duration", 0))
    return {"text": text, "start_ms": start_ms, "end_ms": max(start_ms + 1, end_ms)}


def finish_word_durations(words: list[dict], audio_duration_ms: int = 0) -> list[dict]:
    """Fill zero-length SDK boundaries using the next boundary or audio end."""

    finished = [dict(item) for item in words]
    for index, item in enumerate(finished):
        next_start = finished[index + 1]["start_ms"] if index + 1 < len(finished) else audio_duration_ms
        if item["end_ms"] <= item["start_ms"] + 1 and next_start > item["start_ms"]:
            item["end_ms"] = next_start
        item["end_ms"] = max(item["start_ms"] + 1, item["end_ms"])
    return finished


def build_ssml(text: str, voice: str, rate: str, volume: str, pitch: str) -> str:
    """Build minimal Azure SSML while escaping user-provided text and attributes."""

    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xml:lang="zh-CN">'
        f"<voice name={quoteattr(voice)}>"
        f"<prosody rate={quoteattr(rate)} volume={quoteattr(volume)} pitch={quoteattr(pitch)}>"
        f"{escape(text)}"
        "</prosody></voice></speak>"
    )


def required_config(environment: dict[str, str] | None = None) -> tuple[str, str]:
    source = os.environ if environment is None else environment
    missing = [name for name in (KEY_ENV, REGION_ENV) if not source.get(name, "").strip()]
    if missing:
        raise ValueError(",".join(missing))
    return source[KEY_ENV].strip(), source[REGION_ENV].strip()


def write_outputs(
    words: list[dict],
    out_dir: Path,
    voice: str,
    region: str,
    audio_duration_ms: int,
    max_chars: int,
    max_caption_ms: int,
    script_text: str | None = None,
    max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
) -> tuple[Path, Path]:
    if not words:
        raise AzureSpeechError("Azure Speech 没有返回可用的逐词时间")
    words = finish_word_durations(words, audio_duration_ms)
    if script_text:
        words = align_script_words(script_text, words)
    duration_ms = max(audio_duration_ms, max(item["end_ms"] for item in words))
    words_path = out_dir / "words.json"
    words_path.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "azure-speech-word-boundary",
                "provider": "azure",
                "voice": voice,
                "language": "zh-CN",
                "region": region,
                "duration_ms": duration_ms,
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


def synthesize(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    key, region = required_config()
    script = Path(args.script).resolve()
    if not script.is_file():
        raise AzureSpeechError(f"口播稿不存在：{script}")
    text = script.read_text(encoding="utf-8").strip()
    if not text:
        raise AzureSpeechError("口播稿为空")
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "narration.mp3"

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as error:
        raise AzureSpeechError(
            "缺少 Azure Speech SDK；请先运行 Skill 的 setup --provider azure"
        ) from error

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary, "true"
    )
    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(audio_path))
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    words: list[dict] = []

    def collect_word(event: object) -> None:
        word = azure_event_to_word(event)
        if word is not None:
            words.append(word)

    synthesizer.synthesis_word_boundary.connect(collect_word)
    result = synthesizer.speak_ssml_async(
        build_ssml(text, args.voice, args.rate, args.volume, args.pitch)
    ).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = speechsdk.SpeechSynthesisCancellationDetails.from_result(result)
        reason = getattr(details, "reason", "unknown")
        error_details = getattr(details, "error_details", "")
        raise AzureSpeechError(f"Azure Speech 生成失败：{reason} {error_details}".strip())
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise AzureSpeechError("Azure Speech 没有生成音频")

    audio_duration_ms = duration_to_ms(getattr(result, "audio_duration", None))
    display_text = text
    if args.display_script:
        display_text = Path(args.display_script).resolve().read_text(encoding="utf-8").strip()
        if not display_text:
            raise AzureSpeechError("展示文案为空")
    words_path, captions_path = write_outputs(
        words,
        out_dir,
        args.voice,
        region,
        audio_duration_ms,
        args.max_chars,
        args.max_caption_ms,
        display_text,
        args.max_line_chars,
    )
    return audio_path, words_path, captions_path


def run(args: argparse.Namespace) -> int:
    try:
        audio_path, words_path, captions_path = synthesize(args)
        print("PROVIDER=azure")
        print(f"AUDIO={audio_path}")
        print(f"WORDS={words_path}")
        print(f"CAPTIONS={captions_path}")
        return 0
    except ValueError as error:
        print(f"[needs-input] {safe_diagnostic(error)}", file=sys.stderr)
        return 3
    except (OSError, AzureSpeechError) as error:
        print(f"[err] {safe_diagnostic(error)}", file=sys.stderr)
        return 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="用 Azure Speech 生成中文配音和逐词时间")
    root.add_argument("--script", required=True)
    root.add_argument("--display-script", help="正确书面语原稿；当 --script 是发音送读稿时使用")
    root.add_argument("--out-dir", required=True)
    root.add_argument("--voice", default=DEFAULT_VOICE)
    root.add_argument("--rate", default="+8%")
    root.add_argument("--volume", default="+0%")
    root.add_argument("--pitch", default="+0Hz")
    root.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CAPTION_CHARS)
    root.add_argument("--max-caption-ms", type=int, default=DEFAULT_MAX_CAPTION_MS)
    root.add_argument("--max-line-chars", type=int, default=DEFAULT_MAX_LINE_CHARS)
    return root


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
