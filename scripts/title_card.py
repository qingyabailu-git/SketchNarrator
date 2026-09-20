#!/usr/bin/env python3
"""Reusable concept title card generator for SketchNarrator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

SCRIPTS = Path(__file__).resolve().parent
RENDERER_SCRIPTS = SCRIPTS.parent / "renderer" / "scripts"
for path in (SCRIPTS, RENDERER_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

try:
    import font_runtime
except ImportError:
    font_runtime = None  # type: ignore


TITLE_CARD_REGION = (0.025, 0.028, 0.95, 0.112)
TITLE_CARD_CONTENT_TOP = 0.16

DEFAULT_PALETTES = {
    "coral": (242, 95, 92, 255),
    "purple": (142, 68, 173, 255),
    "cobalt": (58, 110, 165, 255),
    "sky": (41, 128, 185, 255),
    "emerald": (39, 174, 96, 255),
    "gold": (243, 156, 18, 255),
    "dark": (38, 50, 56, 255),
}


def _resolve_color(color_spec: str | tuple) -> tuple[int, int, int, int]:
    if isinstance(color_spec, (tuple, list)):
        if len(color_spec) == 3:
            return (int(color_spec[0]), int(color_spec[1]), int(color_spec[2]), 255)
        return (int(color_spec[0]), int(color_spec[1]), int(color_spec[2]), int(color_spec[3]))
    key = str(color_spec).strip().lower()
    if key in DEFAULT_PALETTES:
        return DEFAULT_PALETTES[key]
    if key.startswith("#"):
        hex_code = key.lstrip("#")
        if len(hex_code) == 6:
            r = int(hex_code[0:2], 16)
            g = int(hex_code[2:4], 16)
            b = int(hex_code[4:6], 16)
            return (r, g, b, 255)
        elif len(hex_code) == 8:
            r = int(hex_code[0:2], 16)
            g = int(hex_code[2:4], 16)
            b = int(hex_code[4:6], 16)
            a = int(hex_code[6:8], 16)
            return (r, g, b, a)
    return (58, 110, 165, 255)


def get_default_font(size: int = 56) -> ImageFont.FreeTypeFont:
    if font_runtime is not None:
        try:
            return font_runtime.load_cjk_font(size)
        except Exception:
            pass
    # Fallback to standard system fonts
    for candidate in [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_concept_card(
    text: str,
    font_size: int = 56,
    font: ImageFont.FreeTypeFont | None = None,
    bg_color: tuple = (255, 255, 255, 245),
    border_color: tuple = (26, 26, 26, 255),
    text_color: tuple = (26, 26, 26, 255),
    border_width: int = 5,
    corner_radius: int = 18,
    padding_x: int = 32,
    padding_y: int = 16,
    accent_bar_color: str | tuple = "cobalt",
) -> Image.Image:
    """Render a doodle-styled concept card with a left accent bar and drop shadow."""
    resolved_font = font or get_default_font(font_size)
    dummy = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=resolved_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    bar_width = max(6, int(font_size * 0.14))
    bar_gap = max(12, int(font_size * 0.28))
    content_w = bar_width + bar_gap + tw
    total_w = content_w + padding_x * 2
    total_h = th + padding_y * 2

    margin = border_width + 4
    img = Image.new("RGBA", (total_w + margin * 2, total_h + margin * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    rect = [margin, margin, margin + total_w, margin + total_h]

    # Subtle offset doodle shadow
    shadow_rect = [rect[0] + 3, rect[1] + 3, rect[2] + 3, rect[3] + 3]
    draw.rounded_rectangle(shadow_rect, radius=corner_radius, fill=(0, 0, 0, 30))

    # Main card body
    draw.rounded_rectangle(rect, radius=corner_radius, fill=bg_color, outline=border_color, width=border_width)

    # Accent bar on left
    resolved_accent = _resolve_color(accent_bar_color)
    bar_x0 = rect[0] + padding_x
    bar_y0 = rect[1] + padding_y + 4
    bar_x1 = bar_x0 + bar_width
    bar_y1 = rect[3] - padding_y - 4
    draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, bar_y1], radius=max(2, bar_width // 2), fill=resolved_accent)

    # Text content
    tx = bar_x1 + bar_gap - bbox[0]
    ty = rect[1] + padding_y - bbox[1]
    draw.text((tx, ty), text, font=resolved_font, fill=text_color)

    return img


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render concept title card image.")
    parser.add_argument("--text", required=True, help="Title text on card")
    parser.add_argument("--output", required=True, help="Output PNG path")
    parser.add_argument("--size", type=int, default=56, help="Font size (default: 56)")
    parser.add_argument("--color", default="cobalt", help="Accent color name or hex (#RRGGBB)")
    args = parser.parse_args(argv)

    card = render_concept_card(args.text, font_size=args.size, accent_bar_color=args.color)
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(out_path)
    print(f"TITLE_CARD={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
