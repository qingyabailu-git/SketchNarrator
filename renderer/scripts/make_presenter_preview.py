#!/usr/bin/env python3
"""Create generic QA previews for a user-owned Presenter asset pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from font_runtime import load_cjk_font
from presenter_runtime import resolve_presenter_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets" / "presenter.json"
PAPER = (246, 239, 222, 255)
INK = (42, 48, 58, 255)
ACCENT = (204, 91, 49, 255)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_cjk_font(size)


def load_manifest() -> dict:
    global MANIFEST
    MANIFEST = resolve_presenter_manifest()
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def asset_path(record: dict) -> Path:
    return MANIFEST.parent / record["runtime_file"]


def paste_anchored(
    canvas: Image.Image,
    sprite: Image.Image,
    target_height: int,
    anchor: tuple[float, float],
    target: tuple[int, int],
) -> tuple[int, int, int, int]:
    scale = target_height / sprite.height
    size = (max(1, round(sprite.width * scale)), target_height)
    sprite = sprite.resize(size, Image.Resampling.LANCZOS)
    left = round(target[0] - anchor[0] * (size[0] - 1))
    top = round(target[1] - anchor[1] * (size[1] - 1))
    canvas.alpha_composite(sprite, (left, top))
    return left, top, left + size[0], top + size[1]


def anchor_preview(manifest: dict, target: Path) -> None:
    canvas = Image.new("RGBA", (1920, 1080), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.text((72, 55), "Presenter 绘制 / 板擦锚点预览", fill=INK, font=font(42))
    draw.text((74, 112), "红色十字为真实落墨点与板擦接触面中心", fill=(85, 91, 98, 255), font=font(24))

    draw.line([(180, 660), (390, 520), (640, 650)], fill=INK, width=8, joint="curve")
    draw.line([(1080, 520), (1500, 520)], fill=(145, 131, 114, 255), width=16)
    draw.line([(1080, 590), (1450, 590)], fill=(145, 131, 114, 255), width=16)
    draw.line([(1080, 660), (1520, 660)], fill=(145, 131, 114, 255), width=16)

    target_height = round(1080 * manifest["height_policy"]["ratio_of_output_short_edge"])
    drawing = manifest["assets"]["drawing"]
    drawing_anchor = (drawing["anchor"]["x"], drawing["anchor"]["y"])
    paste_anchored(
        canvas,
        Image.open(asset_path(drawing)).convert("RGBA"),
        target_height,
        drawing_anchor,
        (640, 650),
    )
    eraser = manifest["assets"]["eraser"]
    eraser_anchor = (eraser["anchor"]["x"], eraser["anchor"]["y"])
    paste_anchored(
        canvas,
        Image.open(asset_path(eraser)).convert("RGBA"),
        target_height,
        eraser_anchor,
        (1300, 660),
    )
    for x, y in ((640, 650), (1300, 660)):
        draw.line((x - 20, y, x + 20, y), fill=ACCENT, width=4)
        draw.line((x, y - 20, x, y + 20), fill=ACCENT, width=4)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=ACCENT)
    draw.text((230, 905), "笔尖跟随真实笔迹", fill=INK, font=font(30))
    draw.text((1190, 905), "接触面跟随擦除路径", fill=INK, font=font(30))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(target, quality=95)


def bookend_preview(manifest: dict, target: Path) -> None:
    canvas = Image.new("RGBA", (1920, 1080), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((55, 55, 925, 1025), radius=35, outline=(222, 203, 172, 255), width=4)
    draw.rounded_rectangle((995, 55, 1865, 1025), radius=35, outline=(222, 203, 172, 255), width=4)
    draw.text((105, 105), "固定开场", fill=ACCENT, font=font(36))
    draw.text((1045, 105), "固定结尾", fill=ACCENT, font=font(36))
    draw.text((435, 245), "今天的问题", fill=INK, font=font(28), anchor="mm")
    draw.text((435, 300), "为什么会这样？", fill=INK, font=font(46), anchor="mm")
    draw.text((1430, 265), "讲完啦，下次见！", fill=INK, font=font(40), anchor="mm")

    intro = manifest["assets"]["intro"]
    intro_sprite = Image.open(asset_path(intro)).convert("RGBA")
    paste_anchored(canvas, intro_sprite, 620, (0.5, 0.962), (435, 1000))
    outro = manifest["assets"]["outro"]
    outro_sprite = Image.open(asset_path(outro)).convert("RGBA")
    paste_anchored(canvas, outro_sprite, 650, (0.5, 0.99), (1430, 1020))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(target, quality=95)


def create_previews(manifest: dict, out_dir: Path, *, name_prefix: str = "presenter") -> tuple[Path, Path]:
    """Create both previews with stable generic names (or a legacy prefix)."""

    anchor_target = out_dir / f"{name_prefix}-anchor-preview.png"
    bookend_target = out_dir / f"{name_prefix}-bookend-preview.png"
    anchor_preview(manifest, anchor_target)
    bookend_preview(manifest, bookend_target)
    return anchor_target, bookend_target


def main(*, default_name_prefix: str = "presenter") -> None:
    parser = argparse.ArgumentParser(description="生成通用 Presenter 锚点与片头片尾检查图")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    create_previews(load_manifest(), args.out_dir, name_prefix=default_name_prefix)
    print(f"OUTPUT={args.out_dir}")


if __name__ == "__main__":
    main()
