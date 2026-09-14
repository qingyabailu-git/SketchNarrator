#!/usr/bin/env python3
"""Uniformly fit a generated board into the visual-safe frame without cropping."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError


SAFE_FRAME = {"left": 0.04, "top": 0.04, "right": 0.96, "bottom": 0.82}
FOREGROUND_THRESHOLD = 42.0

STANDARD_ASPECT_PROFILES: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),  # 横版高清
    "9:16": (1080, 1920),  # 竖版短视频
    "4:3":  (1440, 1080),  # 传统横版
    "3:4":  (1080, 1440),  # 竖版图文
    "1:1":  (1080, 1080),  # 方形
}


def resolve_standard_canvas(width: int, height: int, project_aspect: str | None = None) -> tuple[int, int]:
    """Snap width/height to a standard profile if close (within 10%), or return (width, height)."""
    if project_aspect and project_aspect in STANDARD_ASPECT_PROFILES:
        return STANDARD_ASPECT_PROFILES[project_aspect]

    input_ratio = width / max(1, height)
    best_profile = None
    min_diff = float("inf")
    for profile_name, (pw, ph) in STANDARD_ASPECT_PROFILES.items():
        pratio = pw / ph
        diff = abs(input_ratio - pratio) / pratio
        if diff < min_diff:
            min_diff = diff
            best_profile = (pw, ph)

    # If within 10% ratio difference and size >= 400px (avoid small test fixtures), snap to standard profile
    if best_profile is not None and min_diff <= 0.10 and max(width, height) >= 400:
        return best_profile

    return (width, height)


def median_background(array: np.ndarray) -> np.ndarray:
    height, width = array.shape[:2]
    edge = max(8, min(height, width) // 35)
    samples = np.concatenate([
        array[:edge, :edge].reshape(-1, 3),
        array[:edge, -edge:].reshape(-1, 3),
        array[-edge:, :edge].reshape(-1, 3),
        array[-edge:, -edge:].reshape(-1, 3),
    ])
    return np.median(samples.astype(np.float32), axis=0)


def foreground_bbox(array: np.ndarray, background: np.ndarray) -> tuple[int, int, int, int] | None:
    distance = np.linalg.norm(array.astype(np.float32) - background, axis=2)
    ys, xs = np.where(distance > FOREGROUND_THRESHOLD)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _transform_region(region: dict[str, Any], scale: float, dx: int, dy: int, width: int, height: int) -> None:
    x0 = max(0, min(width - 1, round(float(region["x"]) * scale + dx)))
    y0 = max(0, min(height - 1, round(float(region["y"]) * scale + dy)))
    x1 = max(x0 + 1, min(width, round((float(region["x"]) + float(region["width"])) * scale + dx)))
    y1 = max(y0 + 1, min(height, round((float(region["y"]) + float(region["height"])) * scale + dy)))
    region.update({"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})


def fit_board_safe_area(
    source: Path,
    target: Path,
    annotation: dict[str, Any],
    project_aspect: str | None = None,
    preserve_layout: bool = False,
) -> dict[str, Any]:
    """Fit visible content into the standard safe frame and transform annotation regions.

    Invalid/non-image fixtures are copied unchanged so the workflow contract can
    still be tested without decoding media.
    """
    source = source.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        image = Image.open(source).convert("RGB")
    except (UnidentifiedImageError, OSError):
        if source != target.resolve():
            shutil.copy2(source, target)
        return {"applied": False, "reason": "image-not-decodable"}

    array = np.asarray(image)
    height, width = array.shape[:2]
    background = median_background(array)
    bbox = foreground_bbox(array, background)
    if bbox is None:
        if source != target.resolve():
            shutil.copy2(source, target)
        return {"applied": False, "reason": "no-foreground"}

    target_w, target_h = resolve_standard_canvas(width, height, project_aspect=project_aspect)

    frame = (
        round(target_w * SAFE_FRAME["left"]),
        round(target_h * SAFE_FRAME["top"]),
        round(target_w * SAFE_FRAME["right"]),
        round(target_h * SAFE_FRAME["bottom"]),
    )
    bx0, by0, bx1, by1 = bbox
    fw, fh = frame[2] - frame[0], frame[3] - frame[1]
    bw, bh = max(1, bx1 - bx0), max(1, by1 - by0)

    base_scale = min(target_w / width, target_h / height)
    scale = min(base_scale, (fw * 0.94) / bw, (fh * 0.94) / bh)
    already_safe = (
        (target_w, target_h) == (width, height)
        and bx0 >= frame[0]
        and by0 >= frame[1]
        and bx1 <= frame[2]
        and by1 <= frame[3]
    )
    if already_safe and scale >= 0.985:
        if source != target.resolve():
            shutil.copy2(source, target)
        return {"applied": False, "reason": "already-safe", "foreground_bbox": list(bbox)}

    if preserve_layout:
        # Layout-aware boards were composed in normalized canvas coordinates.
        # Keep the whole-canvas center fixed; only shrink uniformly until the
        # actual foreground fits the outer safe frame. Never recenter content.
        scale = base_scale
        for _ in range(240):
            dx = round((target_w - width * scale) / 2)
            dy = round((target_h - height * scale) / 2)
            transformed = (
                round(bx0 * scale + dx),
                round(by0 * scale + dy),
                round(bx1 * scale + dx),
                round(by1 * scale + dy),
            )
            if (
                transformed[0] >= frame[0]
                and transformed[1] >= frame[1]
                and transformed[2] <= frame[2]
                and transformed[3] <= frame[3]
            ):
                break
            scale *= 0.995
    else:
        target_cx = (frame[0] + frame[2]) / 2
        target_cy = (frame[1] + frame[3]) / 2
        source_cx = (bx0 + bx1) / 2
        source_cy = (by0 + by1) / 2
        dx = round(target_cx - source_cx * scale)
        dy = round(target_cy - source_cy * scale)
    resized = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
    fill = tuple(int(round(value)) for value in background)
    canvas = Image.new("RGB", (target_w, target_h), fill)
    canvas.paste(resized, (dx, dy))
    save_kwargs = {"quality": 95} if target.suffix.lower() in {".jpg", ".jpeg"} else {}
    canvas.save(target, **save_kwargs)

    if annotation.get("backgroundMask") is not None:
        from pixel_contract import decode_mask, encode_mask
        background_mask = Image.fromarray(decode_mask(annotation["backgroundMask"], (height, width)).astype(np.uint8) * 255)
        transformed_mask = Image.new("L", (target_w, target_h), 0)
        transformed_mask.paste(background_mask.resize(resized.size, Image.Resampling.NEAREST), (dx, dy))
        annotation["backgroundMask"] = encode_mask(np.asarray(transformed_mask) > 0)
    for element in annotation.get("elements", []):
        if element.get("pixelMask") is not None:
            from pixel_contract import decode_mask, encode_mask
            source_mask = Image.fromarray(decode_mask(element["pixelMask"], (height, width)).astype(np.uint8) * 255)
            resized_mask = source_mask.resize(resized.size, Image.Resampling.NEAREST)
            target_mask = Image.new("L", (target_w, target_h), 0)
            target_mask.paste(resized_mask, (dx, dy))
            element["pixelMask"] = encode_mask(np.asarray(target_mask) > 0)
        for protected in element.get("reveal", {}).get("protectedRegions", []):
            _transform_region(protected, scale, dx, dy, target_w, target_h)
        region = element.get("region")
        if isinstance(region, dict) and all(key in region for key in ("x", "y", "width", "height")):
            _transform_region(region, scale, dx, dy, target_w, target_h)

    annotation["canvas"] = {"width": target_w, "height": target_h}
    annotation["visualSafeLayout"] = {
        "version": 1,
        "mode": "uniform-fit-preserve-layout" if preserve_layout else "uniform-fit-no-crop",
        "targetCanvas": {"width": target_w, "height": target_h},
        "safeFrame": {"x": frame[0], "y": frame[1], "width": fw, "height": fh},
        "scale": round(scale, 6),
        "offset": {"x": dx, "y": dy},
        "sourceForegroundBbox": list(bbox),
    }
    return annotation["visualSafeLayout"] | {"applied": True}
