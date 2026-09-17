#!/usr/bin/env python3
"""Structural and pixel-level checks for generated whiteboard source images."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import cv2
from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "renderer" / "scripts"))
from pixel_contract import resolve_ownership, foreground_from_rgb, PIXEL_POLICY
from phase_budget import effective_annotation

STYLE_REGISTRY_PATH = Path(__file__).parents[1] / "references" / "style-registry.json"
OPENING_ANCHOR_RECOMMENDED_MS = 200
OPENING_ANCHOR_MAX_MS = 500


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _hex_rgb(value: str) -> np.ndarray:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"无效颜色：{value}")
    return np.asarray([int(text[index:index + 2], 16) for index in (0, 2, 4)], dtype=np.float32)


def _background_style_check(background: np.ndarray, style: dict[str, Any] | None) -> dict[str, Any]:
    contract = (style or {}).get("visual_contract") or {}
    if not contract:
        return {"checked": False, "errors": []}
    rgb = background.astype(np.float32)
    luma = float(rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722)
    luma_range = contract.get("background_luma_range", [0, 255])
    expected = _hex_rgb(str(contract.get("background_hex", "#FFFFFF")))
    distance = float(np.linalg.norm(rgb - expected))
    tolerance = float(contract.get("background_rgb_tolerance", 64))
    errors: list[str] = []
    if not float(luma_range[0]) <= luma <= float(luma_range[1]):
        errors.append(
            f"背景亮度 {luma:.1f} 不符合 {style.get('id')} 约束 "
            f"[{luma_range[0]}, {luma_range[1]}]"
        )
    if distance > tolerance:
        errors.append(
            f"背景主色与 {style.get('id')} 基准色距离 {distance:.1f}，超过 {tolerance:.1f}"
        )
    return {
        "checked": True,
        "style_id": style.get("id"),
        "render_mode": style.get("render_mode", "light"),
        "background_luma": round(luma, 3),
        "background_luma_range": luma_range,
        "background_rgb_distance": round(distance, 3),
        "background_rgb_tolerance": tolerance,
        "errors": errors,
    }


def region_overlap(first: dict[str, int], second: dict[str, int]) -> int:
    if not all(key in first and key in second for key in ("x", "y", "width", "height")):
        return 0
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(first["x"] + first["width"], second["x"] + second["width"])
    bottom = min(first["y"] + first["height"], second["y"] + second["height"])
    return max(0, right - left) * max(0, bottom - top)


def foreground_mask(array: np.ndarray, background: np.ndarray) -> np.ndarray:
    distance = np.linalg.norm(array.astype(np.float32) - background, axis=2)
    return distance > 42


def remove_decorative_frame_components(mask: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Exclude sparse rectangular borders that are layout chrome, not revealable objects."""
    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    cleaned = mask.copy()
    ignored: list[dict[str, Any]] = []
    for component_id in range(1, count):
        x = int(stats[component_id, cv2.CC_STAT_LEFT])
        y = int(stats[component_id, cv2.CC_STAT_TOP])
        w = int(stats[component_id, cv2.CC_STAT_WIDTH])
        h = int(stats[component_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[component_id, cv2.CC_STAT_AREA])
        if w < width * 0.65 or h < height * 0.55 or area / max(1, w * h) > 0.025:
            continue
        component = labels == component_id
        edge = max(2, min(8, round(min(w, h) * 0.015)))
        top = int(np.count_nonzero(component[y:y + edge, x:x + w])) / max(1, w)
        bottom = int(np.count_nonzero(component[y + h - edge:y + h, x:x + w])) / max(1, w)
        left = int(np.count_nonzero(component[y:y + h, x:x + edge])) / max(1, h)
        right = int(np.count_nonzero(component[y:y + h, x + w - edge:x + w])) / max(1, h)
        if min(top, bottom, left, right) < 0.45:
            continue
        cleaned[component] = False
        ignored.append({
            "component_id": component_id,
            "bbox": [x, y, x + w, y + h],
            "area": area,
            "edge_coverage": {
                "top": round(top, 4),
                "bottom": round(bottom, 4),
                "left": round(left, 4),
                "right": round(right, 4),
            },
        })
    return cleaned, ignored


def covered_mask(width: int, height: int, regions: list[dict[str, int]]) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    for region in regions:
        x0 = max(0, int(region["x"]))
        y0 = max(0, int(region["y"]))
        x1 = min(width, x0 + int(region["width"]))
        y1 = min(height, y0 + int(region["height"]))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return mask


def single_region_mask(width: int, height: int, region: dict[str, int]) -> np.ndarray:
    return covered_mask(width, height, [region])


def mask_bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def semantic_crop_risks(
    owner: np.ndarray,
    foreground: np.ndarray,
    annotation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find likely object cuts at region edges; these are visual advisories only."""

    height, width = foreground.shape
    unresolved = foreground & (owner == 0)
    band = max(4, round(min(width, height) * 0.008))
    minimum = max(12, band * 2)
    risks: list[dict[str, Any]] = []
    for index, element in enumerate(annotation.get("elements", []), 1):
        if not isinstance(element, dict) or not isinstance(element.get("region"), dict):
            continue
        region = element["region"]
        try:
            x0 = max(0, int(region["x"]))
            y0 = max(0, int(region["y"]))
            x1 = min(width, x0 + int(region["width"]))
            y1 = min(height, y0 + int(region["height"]))
        except (KeyError, TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        sides = {
            "top": (
                foreground[y0:min(y1, y0 + band), x0:x1] & (owner[y0:min(y1, y0 + band), x0:x1] > 0),
                unresolved[max(0, y0 - band):y0, x0:x1],
            ),
            "bottom": (
                foreground[max(y0, y1 - band):y1, x0:x1] & (owner[max(y0, y1 - band):y1, x0:x1] > 0),
                unresolved[y1:min(height, y1 + band), x0:x1],
            ),
            "left": (
                foreground[y0:y1, x0:min(x1, x0 + band)] & (owner[y0:y1, x0:min(x1, x0 + band)] > 0),
                unresolved[y0:y1, max(0, x0 - band):x0],
            ),
            "right": (
                foreground[y0:y1, max(x0, x1 - band):x1] & (owner[y0:y1, max(x0, x1 - band):x1] > 0),
                unresolved[y0:y1, x1:min(width, x1 + band)],
            ),
        }
        for side, (inside, outside) in sides.items():
            inside_pixels = int(np.count_nonzero(inside))
            outside_pixels = int(np.count_nonzero(outside))
            if inside_pixels < minimum or outside_pixels < minimum:
                continue
            risks.append({
                "severity": "high",
                "kind": "possible-object-cut",
                "element_id": str(element.get("id") or index),
                "label": str(element.get("label") or ""),
                "side": side,
                "inside_pixels": inside_pixels,
                "outside_pixels": outside_pixels,
                "message": (
                    f"元素 {element.get('label') or element.get('id') or index} 的 {side} 边界内外都有连续前景，"
                    "可能裁断完整对象；请在第三次确认预览中查看"
                ),
            })
    if int(np.count_nonzero(unresolved)) and not risks:
        risks.append({
            "severity": "low",
            "kind": "unassigned-foreground",
            "pixels": int(np.count_nonzero(unresolved)),
            "bbox": mask_bbox(unresolved),
            "message": "存在未归属前景，请结合诊断图判断；确认后不会阻止渲染",
        })
    return risks


def check_scene(
    root: Path,
    scene: dict[str, Any],
    record: dict[str, Any],
    transition: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    animation_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    image_path = root / record["image"]
    annotation_path = root / record["annotation"]
    annotation = effective_annotation(read_json(annotation_path), str(scene["id"]), animation_plan)
    array = np.asarray(Image.open(image_path).convert("RGB"))
    height, width = array.shape[:2]
    background = median_background(array)
    errors: list[str] = []
    warnings: list[str] = []
    style_check = _background_style_check(background, style)
    errors.extend(style_check["errors"])
    canvas = annotation.get("canvas", {})
    if [canvas.get("width"), canvas.get("height")] != [width, height]:
        errors.append("annotation.canvas 与源图尺寸不一致")
    elements = annotation.get("elements", [])
    if not 1 <= len(elements) <= 6:
        errors.append("每幕应有 1–6 个按语义自适应的可独立绘制元素")
    if elements and isinstance(elements[0], dict):
        first_reveal = elements[0].get("reveal", {})
        first_start = first_reveal.get("startMs") if isinstance(first_reveal, dict) else None
        if isinstance(first_start, int):
            if first_start > OPENING_ANCHOR_MAX_MS:
                errors.append(
                    f"首个可见元素在幕开始后 {first_start}ms 起笔，超过 "
                    f"{OPENING_ANCHOR_MAX_MS}ms 上限"
                )
            elif first_start > OPENING_ANCHOR_RECOMMENDED_MS:
                warnings.append(
                    f"首个可见元素在幕开始后 {first_start}ms 起笔，建议控制在 "
                    f"{OPENING_ANCHOR_RECOMMENDED_MS}ms 内"
                )

    regions: list[dict[str, int]] = []
    region_elements: list[dict[str, Any]] = []
    previous_end = 0
    for index, element in enumerate(elements):
        region = element.get("region", {})
        if not all(isinstance(region.get(key), int) for key in ("x", "y", "width", "height")):
            errors.append(f"元素 {element.get('sequence')} 的 region 无效")
            continue
        if region["x"] < 0 or region["y"] < 0 or region["x"] + region["width"] > width or region["y"] + region["height"] > height:
            errors.append(f"元素 {element.get('sequence')} 超出画布")
        reveal = element.get("reveal", {})
        start = reveal.get("startMs")
        duration = reveal.get("durationMs")
        if not isinstance(start, int) or not isinstance(duration, int) or start < previous_end:
            errors.append(f"元素 {element.get('sequence')} 的绘制时间重叠或无效")
        else:
            previous_end = start + duration
            scene_dur = int(annotation.get("sceneDurationMs", 0) or 0)
            if index < len(elements) - 1:
                next_start = elements[index + 1].get("reveal", {}).get("startMs")
                available = (next_start - start) if isinstance(next_start, int) else 0
            else:
                available = max(0, scene_dur - start)
            if available >= 3000 and duration < available * 0.55:
                warnings.append(
                    f"元素 {element.get('sequence')} 绘制时长 {duration}ms 明显偏短（可用窗口 {available}ms，闲置率过高），建议使用 pace-annotations 自适应延长"
                )
        regions.append(region)
        region_elements.append(element)

    # The same source-resolution map is consumed by the planner and renderer.
    unprotected_overlap = sum(
        region_overlap(first, second)
        for index, first in enumerate(regions) for second in regions[index + 1:]
    )
    ignored_decorative_frames = []
    try:
        owner, foreground, ownership = resolve_ownership(array, annotation)
        errors.extend(ownership["errors"])
        warnings.extend(ownership.get("warnings", []))
    except ValueError as exc:
        foreground, _ = foreground_from_rgb(array)
        owner = np.zeros(foreground.shape, dtype=np.uint16)
        ownership = {"mode": PIXEL_POLICY, "errors": [str(exc)], "elements": []}
        errors.append(str(exc))
    overlap_foreground_pixels = int(ownership.get("overlap_foreground_pixels", 0))
    element_ownership = ownership.get("elements", [])
    split_components = ownership.get("split_components", [])
    foreground_pixels = int(np.count_nonzero(foreground))
    uncovered = foreground & (owner == 0)
    uncovered_pixels = int(ownership.get("uncovered_foreground_pixels", np.count_nonzero(uncovered)))
    uncovered_ratio = uncovered_pixels / max(1, foreground_pixels)
    visual_risks = semantic_crop_risks(owner, foreground, annotation)
    warnings.extend(risk["message"] for risk in visual_risks)

    safe_top = round(height * 0.86)
    safe_foreground = int(np.count_nonzero(foreground[safe_top:, :]))
    safe_ratio = safe_foreground / max(1, (height - safe_top) * width)
    if safe_ratio > 0.006:
        errors.append(f"底部字幕安全区前景占比 {safe_ratio:.3%} 过高")

    top_margin = round(height * 0.02)
    side_margin = round(width * 0.02)
    foreground_bbox_value = mask_bbox(foreground)
    if foreground_bbox_value:
        x0, y0, x1, _ = foreground_bbox_value
        if x0 < side_margin or x1 > width - side_margin or y0 < top_margin:
            errors.append("主体过于贴近画布顶部或左右边缘，存在截断风险；请等比缩小后重新居中")

    scene_duration = int(annotation.get("sceneDurationMs", 0) or 0)
    final_hold = max(0, scene_duration - previous_end)
    clean_canvas_ms = None
    if transition:
        erase_start = int(transition.get("eraseStartMs", scene_duration)) - int(scene.get("start_ms", 0))
        erase_end = int(transition.get("eraseEndMs", erase_start)) - int(scene.get("start_ms", 0))
        clean_end = int(transition.get("cleanCanvasEndMs", erase_end)) - int(scene.get("start_ms", 0))
        stable_hold = max(0, erase_start - previous_end)
        clean_canvas_ms = max(0, clean_end - erase_end)
        final_hold = stable_hold
        if stable_hold < 250:
            errors.append(f"板擦前完整画面停留只有 {stable_hold}ms，少于 250ms")
        if clean_canvas_ms < 60:
            errors.append(f"板擦后干净画布只有 {clean_canvas_ms}ms，少于 60ms")
    elif final_hold < 450:
        errors.append(f"完整画面停留只有 {final_hold}ms，少于 450ms")

    return {
        "scene": scene["id"],
        "image": str(image_path),
        "annotation": str(annotation_path),
        "image_sha256": sha256(image_path),
        "source_size": [width, height],
        "element_count": len(elements),
        "ownership_contract": ownership,
        "background_rgb": [round(float(value)) for value in background],
        "style_contract": style_check,
        "bottom_safe_foreground_ratio": round(safe_ratio, 6),
        "foreground_bbox": foreground_bbox_value,
        "visual_safe_layout": annotation.get("visualSafeLayout"),
        "final_hold_ms": final_hold,
        "clean_canvas_ms": clean_canvas_ms,
        "transition": transition,
        "unprotected_overlap_pixels": unprotected_overlap,
        "overlap_foreground_pixels": overlap_foreground_pixels,
        "pixel_ownership": PIXEL_POLICY,
        "ignored_decorative_frames": ignored_decorative_frames,
        "element_ownership": element_ownership,
        "split_foreground_components": split_components,
        "foreground_pixels": foreground_pixels,
        "uncovered_foreground_pixels": uncovered_pixels,
        "uncovered_foreground_ratio": round(uncovered_ratio, 6),
        "uncovered_foreground_bbox": mask_bbox(uncovered),
        "visual_risks": visual_risks,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def run(root: Path) -> dict[str, Any]:
    project = read_json(root / "project.json")
    state = read_json(root / "state.json")
    errors: list[str] = []
    warnings: list[str] = []
    scenes: list[dict[str, Any]] = []
    transition_by_scene: dict[str, dict[str, Any]] = {}
    animation_plan = root / "animation-plan.json"
    visual_plan_path = root / "visual-plan.json"
    layout_by_scene: dict[str, dict[str, Any]] = {}
    if visual_plan_path.is_file():
        visual_plan = read_json(visual_plan_path)
        layout_by_scene = {
            str(shot.get("section_id")): shot.get("layout_plan", {})
            for shot in visual_plan.get("shots", [])
            if isinstance(shot, dict)
        }
    registry = read_json(STYLE_REGISTRY_PATH)
    style_id = str(project.get("style_id", "custom"))
    style = next((item for item in registry.get("styles", []) if item.get("id") == style_id), None)
    plan = None
    if animation_plan.is_file():
        plan = read_json(animation_plan)
        transition_by_scene = {
            str(item.get("fromSceneId")): item
            for item in plan.get("transitions", [])
            if item.get("fromSceneId")
        }
    for scene in project.get("scenes", []):
        record = state.get("boards", {}).get(scene["id"])
        if not record:
            errors.append(f"{scene['id']} 尚未登记整板图")
            continue
        result = check_scene(
            root,
            scene,
            record,
            transition_by_scene.get(scene["id"]),
            style,
            layout_by_scene.get(scene["id"]),
            plan,
        )
        scenes.append(result)
        errors.extend(f"{scene['id']}：{message}" for message in result["errors"])
        warnings.extend(f"{scene['id']}：{message}" for message in result.get("warnings", []))

    backgrounds = [scene["background_rgb"] for scene in scenes]
    background_delta = 0.0
    for index, first in enumerate(backgrounds):
        for second in backgrounds[index + 1:]:
            background_delta = max(background_delta, math.dist(first, second))
    if background_delta > 35:
        errors.append(f"多幕背景色差 {background_delta:.1f} 过大")

    compositions = [str(scene.get("composition", "")).strip() for scene in project.get("scenes", [])]
    repeated = [
        index + 1 for index in range(1, len(compositions))
        if compositions[index] and compositions[index] == compositions[index - 1]
    ]
    if repeated:
        errors.append("这些相邻场景重复使用同一构图：" + ", ".join(map(str, repeated)))

    report = {
        "version": 3,
        "ok": not errors,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "style_id": style_id,
        "background_max_rgb_distance": round(background_delta, 3),
        "errors": errors,
        "warnings": warnings,
        "scenes": scenes,
        "manual_review": {
            "required": True,
            "checklist": [
                "人物身份、衣着和画风跨幕一致",
                "相邻场景构图方式不同，主体关系与口播语义一致",
                "源图没有文字、数字、Logo、水印和跨区主体",
                "整板图只在 layout_plan.native_regions 内组织主体，并保持字幕安全区干净",
                "查看对象级裁断风险和诊断图；人物头顶、四肢或道具可能缺失时在第三次确认中明确展示",
                "横向查看参考图与整板图的线材、填色、纹理和构图区分；纹理差异不进入每次自动门禁"
            ]
        }
    }
    write_json(root / "previews" / "board-qa.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="检查整板图、标注、背景和字幕安全区")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    try:
        report = run(Path(args.project).resolve())
    except Exception as exc:
        print(f"[err] 整板图 QA 失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
