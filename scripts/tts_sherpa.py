#!/usr/bin/env python3
"""Generate offline Chinese narration with sherpa-onnx and MeloTTS."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


class SherpaTtsError(RuntimeError):
    """Concise error raised for model or synthesis failures."""


def model_files(model_dir: Path) -> dict[str, Path]:
    files = {
        "model": model_dir / "model.onnx",
        "lexicon": model_dir / "lexicon.txt",
        "tokens": model_dir / "tokens.txt",
        "date_fst": model_dir / "date.fst",
        "number_fst": model_dir / "number.fst",
    }
    missing = [path.name for path in files.values() if not path.is_file()]
    if missing:
        raise SherpaTtsError("MeloTTS 模型文件不完整：" + ", ".join(missing))
    return files


def write_metadata(
    path: Path,
    model_dir: Path,
    elapsed_seconds: float,
    audio_duration_seconds: float,
    sample_rate: int,
    speed: float,
    threads: int,
) -> Path:
    rtf = elapsed_seconds / audio_duration_seconds if audio_duration_seconds else 0.0
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "provider": "sherpa",
                "engine": "sherpa-onnx",
                "model": "vits-melo-tts-zh_en",
                "model_dir": str(model_dir),
                "sample_rate": sample_rate,
                "speed": speed,
                "threads": threads,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "audio_duration_seconds": round(audio_duration_seconds, 3),
                "rtf": round(rtf, 3),
                "alignment_required": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def synthesize(args: argparse.Namespace) -> tuple[Path, Path]:
    script = Path(args.script).resolve()
    if not script.is_file():
        raise SherpaTtsError(f"口播稿不存在：{script}")
    text = script.read_text(encoding="utf-8").strip()
    if not text:
        raise SherpaTtsError("口播稿为空")

    model_dir = Path(args.model_dir).resolve()
    files = model_files(model_dir)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "narration.wav"

    try:
        import sherpa_onnx
        import soundfile as sf
    except ImportError as error:
        raise SherpaTtsError(
            "缺少离线配音依赖；请先运行 Skill 的 setup --provider sherpa"
        ) from error

    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(files["model"]),
                lexicon=str(files["lexicon"]),
                tokens=str(files["tokens"]),
            ),
            provider="cpu",
            debug=False,
            num_threads=args.threads,
        ),
        rule_fsts=",".join((str(files["date_fst"]), str(files["number_fst"]))),
        max_num_sentences=1,
    )
    if not config.validate():
        raise SherpaTtsError("sherpa-onnx 模型配置无效")

    engine = sherpa_onnx.OfflineTts(config)
    generation = sherpa_onnx.GenerationConfig()
    generation.sid = 0
    generation.speed = args.speed
    generation.silence_scale = 0.2
    started = time.perf_counter()
    audio = engine.generate(text, generation)
    elapsed_seconds = time.perf_counter() - started
    if len(audio.samples) == 0:
        raise SherpaTtsError("sherpa-onnx 没有生成音频")

    sf.write(audio_path, audio.samples, samplerate=audio.sample_rate, subtype="PCM_16")
    audio_duration_seconds = len(audio.samples) / audio.sample_rate
    metadata_path = write_metadata(
        out_dir / "sherpa.json",
        model_dir,
        elapsed_seconds,
        audio_duration_seconds,
        audio.sample_rate,
        args.speed,
        args.threads,
    )
    return audio_path, metadata_path


def run(args: argparse.Namespace) -> int:
    try:
        audio_path, metadata_path = synthesize(args)
        print("PROVIDER=sherpa")
        print(f"AUDIO={audio_path}")
        print(f"METADATA={metadata_path}")
        print("ALIGNMENT_REQUIRED=true")
        return 0
    except (OSError, SherpaTtsError) as error:
        print(f"[err] {error}", file=sys.stderr)
        return 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="用 sherpa-onnx MeloTTS 离线生成中文配音")
    root.add_argument("--script", required=True)
    root.add_argument("--out-dir", required=True)
    root.add_argument("--model-dir", required=True)
    root.add_argument("--speed", type=float, default=1.0)
    root.add_argument("--threads", type=int, default=4)
    return root


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
