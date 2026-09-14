"""Source-resolution object masks shared by planning, QA and rendering.

Selections compile deterministic, reviewable masks. Quality doubts are warnings,
not rendering vetoes. Explicit masks retain the approved pixel assignment.
"""
from __future__ import annotations

import hashlib

import numpy as np

PIXEL_POLICY = "semantic-masks-v1"


def foreground_from_rgb(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = rgb.shape[:2]
    edge = max(1, min(h, w) // 35)
    corners = np.concatenate([
        rgb[:edge, :edge].reshape(-1, 3), rgb[:edge, -edge:].reshape(-1, 3),
        rgb[-edge:, :edge].reshape(-1, 3), rgb[-edge:, -edge:].reshape(-1, 3),
    ])
    background = np.median(corners.astype(np.float32), axis=0)
    return np.linalg.norm(rgb.astype(np.float32) - background, axis=2) > 42, background


def region_mask(shape: tuple[int, int], region: dict) -> np.ndarray:
    h, w = shape
    if not all(isinstance(region.get(k), (int, float)) and np.isfinite(region[k]) for k in ("x", "y", "width", "height")):
        raise ValueError("region 必须使用整数像素坐标")
    x, y, rw, rh = (round(region[k]) for k in ("x", "y", "width", "height"))
    if rw <= 0 or rh <= 0 or x < 0 or y < 0 or x + rw > w or y + rh > h:
        raise ValueError("region 必须完整位于源图画布内且面积大于零")
    mask = np.zeros(shape, dtype=bool)
    mask[y:y + rh, x:x + rw] = True
    return mask


def encode_mask(mask):
    runs = []
    for y, row in enumerate(mask):
        edges = np.flatnonzero(np.diff(np.pad(row.astype(np.int8), (1, 1))))
        runs.extend([y, int(a), int(b)] for a, b in zip(edges[::2], edges[1::2]))
    return {"size": [mask.shape[1], mask.shape[0]], "runs": runs}


def decode_mask(value, shape):
    h, w = shape
    if value.get("size") != [w, h] or not isinstance(value.get("runs"), list):
        raise ValueError("对象掩码与源图尺寸不一致")
    mask = np.zeros(shape, dtype=bool)
    for run in value["runs"]:
        if not isinstance(run, list) or len(run) != 3 or any(type(v) is not int for v in run):
            raise ValueError("对象掩码行段无效")
        y, a, b = run
        if not (0 <= y < h and 0 <= a < b <= w):
            raise ValueError("对象掩码行段越界")
        mask[y, a:b] = True
    return mask


def resolve_ownership(rgb: np.ndarray, annotation: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    foreground, _ = foreground_from_rgb(rgb)
    h, w = foreground.shape
    background_mask = np.zeros((h, w), dtype=bool)
    if annotation.get("backgroundMask") is not None:
        background_mask = decode_mask(annotation["backgroundMask"], (h, w))
        foreground &= ~background_mask
    if annotation.get("canvas") != {"width": w, "height": h}:
        canvas = annotation.get("canvas", {})
        if (canvas.get("width"), canvas.get("height")) != (w, h):
            raise ValueError("像素归属必须在 annotation.canvas 对应的源图尺寸计算")
    elements = annotation.get("elements", [])
    ids = [str(e.get("id") or "") for e in elements]
    if not elements or any(not key for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("每个语义元素必须有非空且唯一的 id")
    candidates = []
    explicit_flags = [element.get("pixelMask") is not None for element in elements]
    if any(explicit_flags) and not all(explicit_flags):
        raise ValueError("对象掩码必须全部存在或全部缺省，不能混用")
    for element in elements:
        mask = region_mask((h, w), element.get("region", {}))
        for protected in element.get("reveal", {}).get("protectedRegions", []):
            mask &= ~region_mask((h, w), protected)
        explicit = element.get("pixelMask")
        if explicit is not None:
            declared = decode_mask(explicit, (h, w))
            outside = declared & ~mask
            if outside.any():
                raise ValueError("对象掩码超出当前区域或进入保护区；请显式重新准备")
            candidates.append(declared & foreground)
        else:
            candidates.append(mask & foreground)
    coverage = np.stack(candidates).sum(axis=0)
    owner = np.zeros((h, w), dtype=np.uint16)
    errors = []
    warnings = []
    overlap = int(np.count_nonzero(coverage > 1))
    uncovered = int(np.count_nonzero(foreground & (coverage == 0)))
    raw_uncovered = uncovered

    # Preserve unique selections. Resolve shared selections with the smaller region
    # first (more specific selection), then stable ID. Never use reveal order or
    # proximity, so sorting objects cannot silently change their content.
    priority = sorted(range(len(elements)), key=lambda i: (
        float(elements[i]["region"]["width"]) * float(elements[i]["region"]["height"]), ids[i]))
    for i in priority:
        owner[candidates[i] & (owner == 0)] = i + 1
    metrics = []
    for index, (element, mask) in enumerate(zip(elements, candidates), 1):
        unique = owner == index
        count = int(np.count_nonzero(unique))
        metrics.append({"id": ids[index - 1], "sequence": element.get("sequence"),
                        "assigned_foreground_pixels": count,
                        "shared_candidate_foreground_pixels": int(np.count_nonzero(mask & (coverage > 1)))})
        if not count:
            warnings.append(f"对象 {ids[index - 1]} 当前没有选中的前景，将保持空绘制；可调整，也可按现状确认")
    ambiguous = foreground & (coverage > 1)
    auto_gap = np.zeros((h, w), dtype=bool)
    if overlap:
        warnings.append(f"{overlap} 个重叠前景像素已按小选区优先、同面积按对象 ID 分配；请查看顺序预览，确认后照此执行")
    if uncovered:
        warnings.append(f"{uncovered} 个前景像素在选区外，不会绘制；可扩框，也可按现状确认，不自动重画")
    for index, metric in enumerate(metrics, 1):
        metric["assigned_foreground_pixels"] = int(np.count_nonzero(owner == index))
    split = []
    report = {"mode": PIXEL_POLICY, "map_sha256": hashlib.sha256(owner.astype('<u2').tobytes()).hexdigest(),
              "selection_rule": "smaller-region-then-stable-id-v1",
              "source_size": [w, h], "overlap_foreground_pixels": overlap,
              "uncovered_foreground_pixels": uncovered,
              "raw_uncovered_foreground_pixels": raw_uncovered,
              "auto_assigned_edge_gap_pixels": int(np.count_nonzero(auto_gap)),
              "split_components": split,
              "resolved_overlap_foreground_pixels": int(np.count_nonzero(ambiguous & (owner > 0))),
              "elements": metrics, "errors": errors, "warnings": warnings}
    return owner, foreground, report


def require_ownership(rgb: np.ndarray, annotation: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    result = resolve_ownership(rgb, annotation)
    if result[2]["errors"]:
        raise ValueError("；".join(result[2]["errors"]))
    return result


def compile_masks(rgb, annotation):
    owner, _, _ = require_ownership(rgb, annotation)
    return {str(element["id"]): encode_mask(owner == index)
            for index, element in enumerate(annotation["elements"], 1)}
