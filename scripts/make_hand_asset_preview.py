#!/usr/bin/env python3
"""Create the static light/dark placement preview for the V3 hand assets."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def load_cropped(path: Path, target_height: int) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"素材没有有效 alpha：{path}")
    image = image.crop(bbox)
    ratio = target_height / image.height
    width = max(1, round(image.width * ratio))
    return image.resize((width, target_height), Image.Resampling.LANCZOS)


def place(canvas: Image.Image, image: Image.Image, x: int, y: int, anchor: tuple[float, float]) -> None:
    px = round(x - image.width * anchor[0])
    py = round(y - image.height * anchor[1])
    canvas.alpha_composite(image, (px, py))


def marker(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    draw.ellipse((x - 7, y - 7, x + 7, y + 7), outline=color, width=3)
    draw.line((x - 18, y, x + 18, y), fill=color, width=2)
    draw.line((x, y - 18, x, y + 18), fill=color, width=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drawing", required=True, type=Path)
    parser.add_argument("--eraser", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    width, height = 1920, 1080
    target_height = round(height * 0.14)
    canvas = Image.new("RGBA", (width, height), (245, 235, 215, 255))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((width // 2, 0, width, height), fill=(27, 43, 58, 255))
    draw.line((width // 2, 0, width // 2, height), fill=(255, 255, 255, 120), width=2)

    drawing = load_cropped(args.drawing, target_height)
    eraser = load_cropped(args.eraser, target_height)
    drawing_anchor = (300, 720)
    eraser_anchor = (1320, 720)
    place(canvas, drawing, *drawing_anchor, (0.0, 0.0))
    place(canvas, eraser, *eraser_anchor, (0.18, 0.53))

    light_ink = (77, 58, 43, 255)
    dark_ink = (235, 244, 247, 255)
    draw.text((60, 55), "small-hand / 14% short-edge target", fill=light_ink)
    draw.text((width // 2 + 60, 55), "eraser contact-center / 14% target", fill=dark_ink)
    draw.text((60, 95), f"1920×1080 · overlay height {target_height}px", fill=light_ink)
    draw.text((width // 2 + 60, 95), f"1920×1080 · overlay height {target_height}px", fill=dark_ink)
    marker(draw, *drawing_anchor, light_ink)
    marker(draw, *eraser_anchor, dark_ink)
    draw.text((60, height - 72), "pen-tip anchor", fill=light_ink)
    draw.text((width // 2 + 60, height - 72), "eraser contact center", fill=dark_ink)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(args.output, format="PNG", optimize=True)
    print(f"PREVIEW={args.output}")
    print(f"TARGET_HAND_HEIGHT={target_height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
