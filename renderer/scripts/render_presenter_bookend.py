#!/usr/bin/env python3
"""Render generic Presenter intro/outro clips from a user-owned asset pack."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from font_runtime import load_cjk_font
from presenter_runtime import resolve_presenter_manifest
import stream_render as sr


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "assets" / "presenter.json"
PAPER = (246, 239, 222, 255)
INK = (42, 48, 58, 255)
ACCENT = (204, 91, 49, 255)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_cjk_font(size)


def _ease_out_back(value: float) -> float:
    value = max(0.0, min(1.0, value))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (value - 1.0) ** 3 + c1 * (value - 1.0) ** 2


def _fade(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _wrap_title(text: str, limit: int = 11) -> str:
    text = text.strip()
    if "\n" in text or len(text) <= limit:
        return text
    split = min(range(4, len(text) - 3), key=lambda index: abs(index - len(text) / 2))
    return text[:split] + "\n" + text[split:]


def _alpha_scaled(sprite: Image.Image, scale: float, opacity: float) -> Image.Image:
    size = (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale)))
    resized = sprite.resize(size, Image.Resampling.LANCZOS)
    if opacity < 0.999:
        alpha = resized.getchannel("A").point(lambda value: round(value * opacity))
        resized.putalpha(alpha)
    return resized


def _draw_centered_text(
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    opacity: float,
) -> None:
    color = (*fill[:3], round(fill[3] * max(0.0, min(1.0, opacity))))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer, "RGBA")
    layer_draw.multiline_text(xy, text, fill=color, font=text_font, anchor="mm", align="center", spacing=10)
    canvas.alpha_composite(layer)


def render(kind: str, text: str, output: Path, duration_ms: int, fps: int, width: int, height: int) -> Path:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    record = manifest["assets"][kind]
    sprite = Image.open(MANIFEST_PATH.parent / record["runtime_file"]).convert("RGBA")
    raw = output.with_name(output.stem + "-raw.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"无法打开视频编码器：{raw}")

    frames = max(1, round(duration_ms * fps / 1000))
    title = _wrap_title(text)
    try:
        for index in range(frames):
            time_ms = index * 1000 / fps
            canvas = Image.new("RGBA", (width, height), PAPER)
            draw = ImageDraw.Draw(canvas, "RGBA")
            margin = round(height * 0.055)
            draw.rounded_rectangle(
                (margin, margin, width - margin, height - margin),
                radius=round(height * 0.035),
                outline=(222, 203, 172, 255),
                width=max(2, round(height * 0.004)),
            )

            enter = _ease_out_back(time_ms / 500.0)
            opacity = _fade(time_ms / 260.0)
            bob = math.sin(max(0.0, time_ms - 400) / 360.0) * height * 0.006
            if kind == "intro":
                target_height = height * 0.72
                center = (width * 0.30, height * 0.58 + bob)
                title_center = (round(width * 0.69), round(height * 0.48))
                title_opacity = _fade((time_ms - 520.0) / 480.0)
                _draw_centered_text(
                    canvas,
                    (round(width * 0.69), round(height * 0.25)),
                    "今天的问题",
                    _font(round(height * 0.055)),
                    ACCENT,
                    _fade((time_ms - 380.0) / 300.0),
                )
            else:
                target_height = height * 0.78
                center = (width * 0.50, height * 0.62 + bob)
                title_center = (round(width * 0.50), round(height * 0.20))
                title_opacity = _fade((time_ms - 250.0) / 400.0)

            scale = target_height / sprite.height * (0.88 + 0.12 * enter)
            current = _alpha_scaled(sprite, scale, opacity)
            left = round(center[0] - current.width / 2)
            top = round(center[1] - current.height / 2)
            canvas.alpha_composite(current, (left, top))
            _draw_centered_text(
                canvas,
                title_center,
                title,
                _font(round(height * (0.085 if kind == "intro" else 0.07))),
                INK,
                title_opacity,
            )
            writer.write(cv2.cvtColor(np.asarray(canvas.convert("RGB")), cv2.COLOR_RGB2BGR))
    finally:
        writer.release()
    return sr.transcode_h264(raw, output)


def main() -> None:
    global MANIFEST_PATH
    parser = argparse.ArgumentParser(description="渲染通用 Presenter 固定开场或结尾无声片段")
    parser.add_argument("--kind", choices=["intro", "outro"], required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-ms", type=int)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    MANIFEST_PATH = resolve_presenter_manifest(args.manifest)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    duration_ms = args.duration_ms or int(manifest["assets"][args.kind]["default_duration_ms"])
    result = render(args.kind, args.text, args.output, duration_ms, args.fps, args.width, args.height)
    print(f"OUTPUT={result}")


if __name__ == "__main__":
    main()
