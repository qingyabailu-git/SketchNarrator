#!/usr/bin/env python3
"""Build semantic-safe post-reveal animation and eraser timing plans.

The V3.3 planner keeps automatic paths out of the user-facing contract. It may
emphasize only real foreground pixels belonging to an annotation element, and
only when the visual plan or narrow semantic evidence explains why the motion
exists. Missing, ambiguous, or short windows become stable holds. V3.2 camera-
only focus plans and V3.1 gesture paths remain readable through compatibility
validators and the bottom renderer.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "renderer" / "scripts"))
from pixel_contract import require_ownership
from phase_budget import compile_budget, effective_annotation, PHASE_POLICY


class AnimationPlanError(RuntimeError):
    """Raised when a V3 animation plan cannot be mapped without guessing time."""


MIN_ERASE_MS = 240
MAX_ERASE_MS = 420
STABLE_HOLD_MS = 0
CLEAN_CANVAS_MS = 60
MAX_ACTIVE_TRANSITION_MS = 500
MIN_TRANSITION_GAP_MS = MIN_ERASE_MS + CLEAN_CANVAS_MS

MIN_GESTURE_MS = 700
SIMPLIFIED_GESTURE_MS = 1100
FULL_GESTURE_MS = 1600
FOCUS_PUSH_MS = 1200
FOCUS_PUSH_CONFIDENCE_MIN = 0.75
SAFE_TARGET_CONFIDENCE_MIN = 0.65
CONTOUR_INDICATE_MS = 900
HIGHLIGHT_WASH_MS = 900
PASSING_FLASH_MS = 1000
PRE_ACTION_MS = 100
POST_ACTION_HOLD_MS = 250
LEAVE_MS = 80
SUBTITLE_SAFE_TOP_RATIO = 0.84
TARGET_SAFE_MARGIN_RATIO = 0.02

DEFAULT_PLAN_VERSION = "3.3"
V32_PLAN_VERSION = "3.2"
LEGACY_PLAN_VERSION = "3.1"
LEGACY_PIXEL_EFFECTS = {"contour-indicate", "highlight-wash", "passing-flash"}
DEFAULT_EFFECTS = {"focus-push"}  # Local contour/wash/flash effects are legacy playback only.
EFFECT_MIN_MS = {
    "focus-push": FOCUS_PUSH_MS,
    "contour-indicate": CONTOUR_INDICATE_MS,
    "highlight-wash": HIGHLIGHT_WASH_MS,
    "passing-flash": PASSING_FLASH_MS,
}

# These terms are deliberately narrower than the legacy gesture classifier.
# They identify a conclusion/identity/result that can plausibly receive a
# camera emphasis; ordinary objects, relations, arrows and paths stay stable.
CORE_CONCLUSION_TERMS = (
    "结论", "总结", "归纳", "判断", "重点", "关键", "核心", "结果", "身份",
    "区别", "差异", "规则", "边界", "所以", "因此", "说明", "代表", "不代表",
    "就是", "仍是", "只是", "没有自动", "未自动", "conclusion", "summary",
    "key point", "result",
)
RELATION_TERMS = (
    "箭头", "关系线", "连接线", "流程线", "路径", "流向", "因果线",
    "arrow", "flow", "path",
)
WARNING_TERMS = (
    "提醒", "注意", "警告", "风险", "危险", "避坑", "warning", "risk", "caution",
)
FOCUS_TERMS = ("聚焦", "放大", "推近", "focus", "push")


def normalize_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(value)).casefold()


def _scene_words(scene: dict[str, Any], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start_ms = int(scene["start_ms"])
    end_ms = int(scene["end_ms"])
    return [
        word
        for word in words
        if int(word["start_ms"]) < end_ms and int(word["end_ms"]) > start_ms
    ]


def _match_trigger(trigger_text: str, items: list[dict[str, Any]]) -> tuple[int, int] | None:
    needle = normalize_text(trigger_text)
    if not needle or not items:
        return None
    combined = ""
    owners: list[int] = []
    for index, item in enumerate(items):
        token = normalize_text(str(item.get("text", "")))
        combined += token
        owners.extend([index] * len(token))
    offset = combined.find(needle)
    if offset < 0 or combined.find(needle, offset + 1) >= 0:
        return None
    return owners[offset], owners[offset + len(needle) - 1]


def semantic_word_window(
    scene: dict[str, Any], element: dict[str, Any], words: list[dict[str, Any]]
) -> dict[str, Any]:
    items = _scene_words(scene, words)
    trigger = str(
        element.get("trigger_text")
        or element.get("triggerText")
        or element.get("subtitle")
        or element.get("label")
        or ""
    ).strip()
    match = _match_trigger(trigger, items)
    if match is not None:
        left, right = match
        return {
            "triggerText": trigger,
            "wordStartMs": int(items[left]["start_ms"]),
            "wordEndMs": int(items[right]["end_ms"]),
            "wordIndexStart": left,
            "wordIndexEnd": right,
            "mapping": "exact-trigger-text",
        }

    if not items:
        return {
            "triggerText": trigger,
            "wordStartMs": int(scene["start_ms"]),
            "wordEndMs": int(scene["end_ms"]),
            "wordIndexStart": None,
            "wordIndexEnd": None,
            "mapping": "scene-window-no-words",
        }

    # Missing semantic evidence must not fabricate an element-specific window.
    left, right = 0, len(items) - 1
    return {
        "triggerText": trigger,
        "wordStartMs": int(items[left]["start_ms"]),
        "wordEndMs": int(items[right]["end_ms"]),
        "wordIndexStart": left,
        "wordIndexEnd": right,
        "mapping": "unresolved-semantic-trigger",
    }


def _transition_for_pair(
    previous: dict[str, Any],
    following: dict[str, Any],
    words: list[dict[str, Any]],
) -> dict[str, Any]:
    previous_words = _scene_words(previous, words)
    following_words = _scene_words(following, words)
    if not previous_words or not following_words:
        raise AnimationPlanError(
            f"{previous['id']}→{following['id']} 缺少前后幕逐词时间，无法安排板擦；"
            "请在正式配音阶段补齐 words.json。"
        )
    previous_end = max(int(word["end_ms"]) for word in previous_words)
    next_start = min(int(word["start_ms"]) for word in following_words)
    gap_ms = next_start - previous_end
    if gap_ms < MIN_TRANSITION_GAP_MS:
        raise AnimationPlanError(
            f"{previous['id']}→{following['id']} 的真实停顿只有 {gap_ms}ms，"
            f"至少需要 {MIN_TRANSITION_GAP_MS}ms（擦除 {MIN_ERASE_MS}ms + "
            f"净板 {CLEAN_CANVAS_MS}ms）；请按真实配音缩短转场或调整句间气口。"
        )

    active_budget = min(gap_ms, MAX_ACTIVE_TRANSITION_MS)
    clean_ms = CLEAN_CANVAS_MS
    erase_ms = min(MAX_ERASE_MS, active_budget - clean_ms)
    stable_hold_ms = gap_ms - erase_ms - clean_ms
    erase_start = previous_end + stable_hold_ms
    erase_end = erase_start + erase_ms
    return {
        "fromSceneId": previous["id"],
        "toSceneId": following["id"],
        "previousWordEndMs": previous_end,
        "nextSceneFirstWordMs": next_start,
        "gapMs": gap_ms,
        "stableHoldMs": stable_hold_ms,
        "eraseStartMs": erase_start,
        "eraseEndMs": erase_end,
        "eraseDurationMs": erase_ms,
        "cleanCanvasStartMs": erase_end,
        "cleanCanvasEndMs": next_start,
        "cleanCanvasMs": clean_ms,
        "status": "ready",
    }


def transition_plan(scenes: list[dict[str, Any]], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _transition_for_pair(previous, following, words)
        for previous, following in zip(scenes, scenes[1:])
    ]


def validate_transition_pause_budgets(scenes: list[dict[str, Any]], words: list[dict[str, Any]]) -> None:
    """Fail early at script/voice staging instead of silently stretching a scene."""

    transition_plan(scenes, words)


def _read_board_rgb(image_path: str | Path | None):
    if not image_path:
        return None
    try:
        import numpy as np
        from PIL import Image

        return np.asarray(Image.open(Path(image_path)).convert("RGB"))
    except Exception:
        return None


def _background_rgb(array):
    import numpy as np

    height, width = array.shape[:2]
    edge = max(8, min(height, width) // 35)
    samples = np.concatenate([
        array[:edge, :edge].reshape(-1, 3),
        array[:edge, -edge:].reshape(-1, 3),
        array[-edge:, :edge].reshape(-1, 3),
        array[-edge:, -edge:].reshape(-1, 3),
    ])
    return np.median(samples.astype(np.float32), axis=0)


def _clip_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    x0 = max(0, min(width - 1, int(x0)))
    y0 = max(0, min(height - 1, int(y0)))
    x1 = max(x0 + 1, min(width, int(x1)))
    y1 = max(y0 + 1, min(height, int(y1)))
    return x0, y0, x1, y1


def focus_target_from_pixels(
    annotation: dict[str, Any],
    element: dict[str, Any],
    image_path: str | Path | None,
) -> dict[str, Any]:
    """Return a safe compact target in annotation-pixel coordinates."""

    canvas = annotation.get("canvas") or {}
    canvas_w = max(1, int(canvas.get("width", 1)))
    canvas_h = max(1, int(canvas.get("height", 1)))
    region = element.get("region") or {}
    rx0 = int(region.get("x", 0))
    ry0 = int(region.get("y", 0))
    rx1 = rx0 + int(region.get("width", 1))
    ry1 = ry0 + int(region.get("height", 1))
    safe_top = max(1, int(canvas_h * SUBTITLE_SAFE_TOP_RATIO))
    fallback = _clip_box((rx0, ry0, min(rx1, canvas_w), min(ry1, safe_top)), canvas_w, canvas_h)
    array = _read_board_rgb(image_path)
    if array is None or getattr(array, "ndim", 0) != 3:
        return {
            "x": fallback[0], "y": fallback[1],
            "width": fallback[2] - fallback[0], "height": fallback[3] - fallback[1],
            "center": [round((fallback[0] + fallback[2]) / 2), round((fallback[1] + fallback[3]) / 2)],
            "confidence": 0.0,
            "source": "annotation-region-fallback",
            "safeMarginPx": 0,
            "subtitleSafeTopPx": safe_top,
        }

    import numpy as np

    img_h, img_w = array.shape[:2]
    ix0 = round(max(0, rx0) * img_w / canvas_w)
    iy0 = round(max(0, ry0) * img_h / canvas_h)
    ix1 = round(min(canvas_w, rx1) * img_w / canvas_w)
    iy1 = round(min(canvas_h, min(ry1, safe_top)) * img_h / canvas_h)
    ix0, iy0, ix1, iy1 = _clip_box((ix0, iy0, ix1, iy1), img_w, img_h)
    try:
        owner, _, _ = require_ownership(array, annotation)
    except ValueError as exc:
        raise AnimationPlanError(str(exc)) from exc
    element_index = next((index for index, item in enumerate(annotation["elements"], 1)
                          if str(item.get("id")) == str(element.get("id"))), None)
    if element_index is None:
        raise AnimationPlanError("后动画目标不属于当前语义对象集合")
    foreground = owner[iy0:iy1, ix0:ix1] == element_index
    ys, xs = np.where(foreground)
    crop_area = max(1, foreground.size)
    if len(xs) < max(25, round(crop_area * 0.00035)):
        return {
            "x": fallback[0], "y": fallback[1],
            "width": fallback[2] - fallback[0], "height": fallback[3] - fallback[1],
            "center": [round((fallback[0] + fallback[2]) / 2), round((fallback[1] + fallback[3]) / 2)],
            "confidence": 0.12,
            "source": "annotation-region-low-confidence",
            "safeMarginPx": 0,
            "subtitleSafeTopPx": safe_top,
        }

    # Quantiles discard isolated paper marks at region edges while retaining
    # the main foreground silhouette and linework.
    low_x, high_x = np.percentile(xs, [2, 98])
    low_y, high_y = np.percentile(ys, [2, 98])
    bx0 = round((ix0 + low_x) * canvas_w / img_w)
    by0 = round((iy0 + low_y) * canvas_h / img_h)
    bx1 = round((ix0 + high_x + 1) * canvas_w / img_w)
    by1 = round((iy0 + high_y + 1) * canvas_h / img_h)
    margin = max(14, round(min(canvas_w, canvas_h) * TARGET_SAFE_MARGIN_RATIO))
    bx0 -= margin
    by0 -= margin
    bx1 += margin
    by1 += margin
    bx0 = max(rx0, margin, bx0)
    by0 = max(ry0, margin, by0)
    bx1 = min(rx1, canvas_w - margin, bx1)
    by1 = min(ry1, safe_top - 2, by1)
    if bx1 <= bx0 or by1 <= by0:
        return {
            "x": fallback[0], "y": fallback[1],
            "width": fallback[2] - fallback[0], "height": fallback[3] - fallback[1],
            "center": [round((fallback[0] + fallback[2]) / 2), round((fallback[1] + fallback[3]) / 2)],
            "confidence": 0.12,
            "source": "annotation-region-low-confidence",
            "safeMarginPx": 0,
            "subtitleSafeTopPx": safe_top,
        }
    density = len(xs) / crop_area
    confidence = min(0.99, 0.55 + min(0.28, density * 5.0) + min(0.16, (bx1 - bx0) * (by1 - by0) / max(1, canvas_w * canvas_h)))
    return {
        "x": int(bx0), "y": int(by0), "width": int(bx1 - bx0), "height": int(by1 - by0),
        "center": [round((bx0 + bx1) / 2), round((by0 + by1) / 2)],
        "confidence": round(float(confidence), 4),
        "source": "foreground-pixels",
        "foregroundPixels": int(len(xs)),
        "foregroundDensity": round(float(density), 6),
        "safeMarginPx": int(margin),
        "subtitleSafeTopPx": int(safe_top),
    }


def _target_box(target: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(target["x"]), int(target["y"]),
        int(target["x"] + target["width"]), int(target["y"] + target["height"]),
    )


def gesture_path_for(
    effect: str,
    target: dict[str, Any],
    annotation: dict[str, Any],
) -> list[list[int]]:
    """Derive a short, subtitle-safe path from a focus target."""

    canvas = annotation.get("canvas") or {}
    width = int(canvas.get("width", 1))
    height = int(canvas.get("height", 1))
    safe_top = min(height - 2, int(target.get("subtitleSafeTopPx", height * SUBTITLE_SAFE_TOP_RATIO)))
    x0, y0, x1, y1 = _target_box(target)
    margin = max(16, int(target.get("safeMarginPx", 18)))
    if effect == "focus-push":
        # A focus-push still gets a real, open pen gesture first.  The camera
        # phase begins only after this underline is complete; it is not a
        # substitute for a flash or a closed focus frame.
        uy = min(safe_top - 10, y1 + margin)
        if uy <= y0 + 6:
            uy = min(safe_top - 10, y0 + max(12, (y1 - y0) // 2))
        left = max(12, x0 + max(8, (x1 - x0) // 5))
        right = min(width - 12, x1 - max(8, (x1 - x0) // 5))
        if right - left < 36:
            left, right = max(12, x0), min(width - 12, x1)
        return [[left, int(uy)], [right, int(uy)]]
    if effect == "gesture-underline":
        uy = y1 + margin
        if uy >= safe_top:
            uy = max(y0 + 4, y1 - max(8, margin // 2))
        left = max(8, x0 + max(4, (x1 - x0) // 10))
        right = min(width - 8, x1 - max(4, (x1 - x0) // 10))
        return [[left, min(safe_top - 2, uy)], [right, min(safe_top - 2, uy)]]
    if effect == "gesture-bracket":
        use_left = x1 > width * 0.62
        bx = max(8, x0 - margin) if use_left else min(width - 8, x1 + margin)
        top = max(8, y0 + max(4, margin // 2))
        bottom = min(safe_top - 4, y1 - max(4, margin // 2))
        if bottom <= top:
            top, bottom = max(8, y0), min(safe_top - 4, y1)
        direction = -1 if use_left else 1
        hook = direction * max(10, margin // 2)
        return [
            [bx + hook, top], [bx, top], [bx, bottom], [bx + hook, bottom],
        ]
    if effect == "gesture-arrow":
        cy = min(safe_top - 8, max(y0 + 8, round((y0 + y1) / 2)))
        left = max(12, x0 - max(20, margin * 2))
        right = min(width - 12, x1 + max(20, margin * 2))
        if right - left < 36:
            left, right = max(12, x0), min(width - 12, x1)
        head = max(12, min(32, round((right - left) * 0.12)))
        return [
            [left, cy], [right, cy],
            [right - head, cy - head], [right, cy], [right - head, cy + head],
        ]

    # gesture-arc: a partial ellipse, never a closed frame/circle.
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    rx = max(24.0, (x1 - x0) / 2 + margin)
    ry = max(18.0, (y1 - y0) / 2 + margin)
    points: list[list[int]] = []
    for index in range(15):
        angle = math.radians(210 + (140 * index / 14))
        px = max(8, min(width - 8, round(cx + rx * math.cos(angle))))
        py = max(8, min(safe_top - 4, round(cy + ry * math.sin(angle))))
        points.append([px, py])
    return points


LEGACY_RELATION_TERMS = ("身份", "层级", "关系", "区别", "位置", "规则", "差异", "名分", "正妻", "妾", "通房", "门槛", "家庭", "时代")
IDENTITY_TERMS = ("算不算", "身份", "结论", "归纳", "判断", "只是", "就是", "仍是", "代表", "不代表", "没有自动", "未自动", "身份边界", "靠近主人")
PROCESS_TERMS = ("因为", "所以", "安排", "变化", "步骤", "流程", "去向", "嫁出", "留在", "前后", "收作", "导致", "向")
ACTION_TERMS = ("人物", "丫鬟", "主人", "家主", "房门", "宅", "例子", "衣服", "差事", "簿", "物件", "伺候")


def choose_effect(scene: dict[str, Any], element: dict[str, Any], index: int, total: int) -> str:
    """Return the V3.1 legacy recipe for explicit legacy callers.

    The V3.2 builder below does not call this classifier. Keeping it available
    lets old tooling inspect or regenerate an explicitly legacy plan without
    making those gesture choices part of the new default workflow.
    """
    text = " ".join(str(element.get(key, "")) for key in ("label", "role", "narrativeRole", "trigger_text", "subtitle"))
    composition = str(scene.get("composition", ""))
    if index == total - 1 and any(term in text for term in IDENTITY_TERMS):
        return "focus-push"
    if composition in {"causal-chain", "timeline", "vertical-layers"} and any(term in text for term in PROCESS_TERMS):
        return "gesture-arrow"
    if any(term in text for term in LEGACY_RELATION_TERMS):
        return "gesture-bracket"
    if any(term in text for term in IDENTITY_TERMS):
        return "gesture-underline"
    if any(term in text for term in ACTION_TERMS):
        return "gesture-arc"
    return "gesture-underline" if index == total - 1 else "gesture-arc"


def is_core_conclusion(
    scene: dict[str, Any],
    element: dict[str, Any],
    index: int,
    total: int,
    visual_beat: dict[str, Any] | None = None,
) -> bool:
    """Return whether an element is explicit enough for a camera emphasis.

    A final element alone is not sufficient: the text must contain a narrow
    conclusion/identity/result cue, or an upstream visual beat must explicitly
    describe a focus/summary conclusion. Ordinary objects, arrows, paths and
    relationships stay with the normal region stream renderer.
    """

    text = " ".join(
        str(element.get(key, ""))
        for key in ("label", "role", "narrativeRole", "trigger_text", "subtitle")
    ).casefold()
    if any(term.casefold() in text for term in CORE_CONCLUSION_TERMS):
        return True
    if visual_beat:
        beat_text = " ".join(
            str(visual_beat.get(key, ""))
            for key in ("action", "purpose", "target", "template", "label")
        ).casefold()
        if any(term in beat_text for term in ("focus", "summary", "conclusion", "结论", "总结", "归纳", "重点", "核心")):
            return True
    return False


def semantic_effect_for(
    scene: dict[str, Any],
    element: dict[str, Any],
    visual_beat: dict[str, Any] | None,
) -> tuple[str, str, str]:
    """Return (effect, semantic_intent, evidence) or a stable skip.

    A visual-plan preference is accepted only from the safe V3.3 vocabulary.
    Otherwise classification uses narrow words attached to the actual element;
    ordinary subjects never receive motion merely because they are last or
    visually prominent.
    """

    if visual_beat:
        preferred = str(visual_beat.get("preferred_effect") or "").strip()
        role = str(visual_beat.get("motion_role") or "none").strip()
        if bool(visual_beat.get("animation_candidate")) and preferred in DEFAULT_EFFECTS:
            return preferred, role, "visual-plan-explicit-semantic-intent"

    text = " ".join(
        str(element.get(key, ""))
        for key in ("label", "role", "narrativeRole", "type", "visual_action", "trigger_text", "subtitle")
    ).casefold()
    if any(term.casefold() in text for term in FOCUS_TERMS):
        return "focus-push", "focus", "annotation-explicit-focus-cue"
    return "skip", "none", "no-semantic-motion-evidence"


def _effect_confidence_min(effect: str) -> float:
    return FOCUS_PUSH_CONFIDENCE_MIN if effect == "focus-push" else SAFE_TARGET_CONFIDENCE_MIN


def _simplify_effect(effect: str, budget_ms: int) -> tuple[str, dict[str, Any] | None]:
    if effect == "focus-push" and budget_ms < FOCUS_PUSH_MS:
        if budget_ms >= MIN_GESTURE_MS:
            return "gesture-underline", {"from": "focus-push", "to": "gesture-underline", "reason": "focus-push 可用窗口低于 1200ms"}
        return "skip", {"from": effect, "to": "skip", "reason": "focus-push 与手绘下划线均没有 700ms 真实窗口"}
    if budget_ms < SIMPLIFIED_GESTURE_MS and effect in {"gesture-arc", "gesture-bracket"}:
        if budget_ms >= MIN_GESTURE_MS:
            return "gesture-underline", {"from": effect, "to": "gesture-underline", "reason": "真实窗口仅允许短下划线"}
        return "skip", {"from": effect, "to": "skip", "reason": "真实窗口低于 700ms"}
    if budget_ms < MIN_GESTURE_MS:
        return "skip", {"from": effect, "to": "skip", "reason": "真实窗口低于 700ms"}
    if budget_ms < FULL_GESTURE_MS:
        return effect, {"from": effect, "to": effect, "reason": "简化手绘阶段，保留至少 250ms 停留"}
    return effect, None


def _phase_timing(
    start_ms: int,
    budget_ms: int,
    effect: str,
    has_gesture_path: bool = True,
) -> dict[str, dict[str, int]]:
    start_ms = int(start_ms)
    end_ms = start_ms + int(budget_ms)
    if effect in LEGACY_PIXEL_EFFECTS:
        prepare_end = min(end_ms, start_ms + PRE_ACTION_MS)
        remaining = max(0, end_ms - prepare_end)
        accent_duration = min(650, max(400, remaining - POST_ACTION_HOLD_MS - LEAVE_MS))
        accent_end = min(end_ms, prepare_end + accent_duration)
        leave_start = min(end_ms, max(accent_end + POST_ACTION_HOLD_MS, end_ms - LEAVE_MS))
        return {
            "prepare": {"startMs": start_ms, "endMs": prepare_end},
            "accent": {"startMs": prepare_end, "endMs": accent_end},
            "hold": {"startMs": accent_end, "endMs": leave_start},
            "leave": {"startMs": leave_start, "endMs": end_ms},
        }
    if effect == "focus-push" and not has_gesture_path:
        # V3.2 focus-push is camera-only emphasis. There is deliberately no
        # draw phase: the normal region stream already drew any board arrows
        # or relationship lines that belong in the source image.
        prepare_end = min(end_ms, start_ms + PRE_ACTION_MS)
        remaining = max(0, end_ms - prepare_end)
        camera_duration = min(650, max(400, remaining - 400 - LEAVE_MS))
        camera_end = min(end_ms, prepare_end + camera_duration)
        leave_start = min(end_ms, max(camera_end + 400, end_ms - LEAVE_MS))
        return {
            "prepare": {"startMs": start_ms, "endMs": prepare_end},
            "camera": {"startMs": prepare_end, "endMs": camera_end},
            "hold": {"startMs": camera_end, "endMs": leave_start},
            "leave": {"startMs": leave_start, "endMs": end_ms},
        }
    if effect == "focus-push":
        prepare_end = min(end_ms, start_ms + PRE_ACTION_MS)
        # Leave a real draw phase before the camera.  At the minimum legal
        # 1200ms budget this is deliberately short but still visible; longer
        # windows use a natural 500-650ms pen movement.
        remaining = max(0, end_ms - prepare_end)
        draw_duration = min(650, max(200, remaining - 400 - 400 - LEAVE_MS))
        draw_end = min(end_ms, prepare_end + draw_duration)
        camera_start = draw_end
        camera_end = min(end_ms, camera_start + min(550, max(400, end_ms - camera_start - 400 - LEAVE_MS)))
        hold_end = min(end_ms, camera_end + 400)
        return {
            "prepare": {"startMs": start_ms, "endMs": prepare_end},
            "draw": {"startMs": prepare_end, "endMs": draw_end},
            "camera": {"startMs": camera_start, "endMs": camera_end},
            "hold": {"startMs": camera_end, "endMs": max(camera_end, hold_end)},
            "leave": {"startMs": hold_end, "endMs": end_ms},
        }
    draw_start = start_ms + PRE_ACTION_MS
    draw_end = max(draw_start + 1, end_ms - POST_ACTION_HOLD_MS - LEAVE_MS)
    hold_start = draw_end
    leave_start = max(hold_start, end_ms - LEAVE_MS)
    return {
        "prepare": {"startMs": start_ms, "endMs": draw_start},
        "draw": {"startMs": draw_start, "endMs": draw_end},
        "hold": {"startMs": hold_start, "endMs": leave_start},
        "leave": {"startMs": leave_start, "endMs": end_ms},
    }


def _style_payload(style_config: dict[str, Any] | None, effect: str) -> dict[str, Any]:
    style = style_config or {}
    style_id = str(style.get("id") or "warm-pencil")
    colors = style.get("gesture_colors") or {}
    color = str(colors.get(effect) or colors.get("default") or "#4E7598")
    return {
        "styleId": style_id,
        "colorSource": f"style-registry:{style_id}.gesture_colors",
        "colorHex": color,
        "lineWidthShortEdgeRatio": 0.0036,
    }


def _make_event(
    scene: dict[str, Any],
    element: dict[str, Any],
    semantic: dict[str, Any],
    effect: str,
    start_ms: int,
    end_ms: int,
    boundary_ms: int,
    focus_target: dict[str, Any],
    gesture_path: list[list[int]],
    auxiliary: bool,
    fallback: dict[str, Any] | None,
    style_config: dict[str, Any] | None,
    visual_beat: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase = _phase_timing(
        start_ms,
        max(1, end_ms - start_ms),
        effect,
        has_gesture_path=bool(gesture_path),
    )
    target_id = str(element.get("id") or element.get("label") or element.get("sequence"))
    intent = str((visual_beat or {}).get("motion_role") or "none")
    semantic_goal = str(
        (visual_beat or {}).get("semantic_goal")
        or element.get("narrativeRole")
        or element.get("role")
        or element.get("label")
        or ""
    ).strip()
    start_state = str((visual_beat or {}).get("start_state") or f"{target_id} 已完整绘制").strip()
    action = str((visual_beat or {}).get("action") or effect).strip()
    end_state = str((visual_beat or {}).get("end_state") or "强调结束后恢复稳定画面").strip()
    static_anchors = list((visual_beat or {}).get("static_anchors") or [])
    record: dict[str, Any] = {
        "id": f"{scene['id']}-{target_id}-{effect.replace('/', '-')}" + ("-aux" if auxiliary else ""),
        "targetElementId": target_id,
        "targetElement": target_id,
        "focusTarget": focus_target,
        "effect": effect,
        "startMs": int(start_ms),
        "endMs": int(end_ms),
        "persistUntilMs": int(boundary_ms),
        "timeBudgetMs": int(end_ms - start_ms),
        "phase": phase,
        "intensity": 0.05 if effect == "focus-push" else 0.03,
        "auxiliary": auxiliary,
        "fallback": fallback,
        "fallbackPolicy": "stable-hold",
        "semanticIntent": intent,
        "semanticGoal": semantic_goal,
        "startState": start_state,
        "action": action,
        "endState": end_state,
        "staticAnchors": static_anchors,
        "targetMaskSource": "foreground-pixels-within-focusTarget",
        "effectOptions": {
            "direction": "left-to-right",
            "preserveOriginalPixels": True,
            "inventGeometry": False,
        },
        "style": _style_payload(style_config, effect),
        "handBehavior": {
            "mode": "small-hand",
            "coordinateSpace": "annotation-pixels",
            "tipAnchorSource": "hand-assets.json:drawing.small-hand.anchor",
            "prepare": "stable-frame-before-semantic-effect" if not gesture_path else "lift-pen-move-to-start",
            "draw": "none" if not gesture_path else "drop-pen-follow-path",
            "hold": "keep-semantic-result-readable" if not gesture_path else "keep-ink-and-tip-at-end",
            "leave": "stable-frame-after-semantic-effect" if not gesture_path else "lift-pen-move-away",
            "hideDuringCamera": effect == "focus-push",
            "showHandForEffect": False if effect in LEGACY_PIXEL_EFFECTS else bool(gesture_path),
        },
        "triggerText": semantic["triggerText"],
        "wordStartMs": semantic["wordStartMs"],
        "wordEndMs": semantic["wordEndMs"],
        "wordIndexStart": semantic.get("wordIndexStart"),
        "wordIndexEnd": semantic.get("wordIndexEnd"),
        "mapping": semantic["mapping"],
    }
    # New V3.2 focus-push events intentionally omit gesturePath/path. The
    # bottom renderer still accepts these fields for explicitly legacy plans.
    if gesture_path:
        path_start = gesture_path[0]
        path_end = gesture_path[-1]
        record["gesturePath"] = gesture_path
        record["gesturePathSpace"] = "annotation-pixels"
        record["path"] = gesture_path
        record["handBehavior"]["handStart"] = [max(0, path_start[0] - 90), max(0, path_start[1] - 90)]
        record["handBehavior"]["handLeave"] = [path_end[0] + 90, max(0, path_end[1] - 90)]
    if visual_beat and visual_beat.get("beat_id"):
        record["upstreamVisualBeatId"] = visual_beat.get("beat_id")
        record["triggerPhraseId"] = visual_beat.get("trigger_phrase_id")
        record["visualBeatStartMs"] = int(visual_beat.get("start_ms", 0))
        record["visualBeatEndMs"] = int(visual_beat.get("end_ms", 0))
        record["visualMapping"] = "visual-plan-beat"
    elif visual_beat:
        record["visualMapping"] = "annotation-semantic-evidence"
    return record


def _available_window(
    scene: dict[str, Any],
    element: dict[str, Any],
    next_element: dict[str, Any] | None,
    scene_duration: int,
    transition_rel_erase: int | None,
    narration_rel_end: int | None = None,
    allow_concurrent_draw: bool = False,
) -> dict[str, Any]:
    reveal = element.get("reveal", {})
    element_end = int(reveal.get("startMs", 0)) + int(reveal.get("durationMs", 0))
    next_start = int(next_element.get("reveal", {}).get("startMs", scene_duration)) if next_element else scene_duration
    # Pixel-only post effects do not use the drawing hand and may run while a
    # later object starts drawing. Treating every next reveal as a hard
    # boundary made compact scenes lose all animation events even though the
    # audio still had a legal 900-1200ms emphasis window.
    boundary = scene_duration if allow_concurrent_draw else min(scene_duration, next_start)
    if transition_rel_erase is not None:
        boundary = min(boundary, transition_rel_erase)
    if narration_rel_end is not None:
        boundary = min(boundary, narration_rel_end)
    action_start = element_end + PRE_ACTION_MS
    action_end = boundary - POST_ACTION_HOLD_MS
    return {
        "elementEndMs": element_end,
        "nextElementStartMs": next_start,
        "boundaryMs": boundary,
        "actionStartMs": action_start,
        "actionEndMs": action_end,
        "budgetMs": max(0, action_end - action_start),
        "concurrentWithLaterDrawing": bool(
            allow_concurrent_draw and next_element is not None and action_end > next_start
        ),
    }


def reserve_animation_windows(
    project: dict[str, Any],
    annotations: dict[str, dict[str, Any]],
    words: list[dict[str, Any]],
    visual_plan: dict[str, Any] | None = None,
    board_images: dict[str, str | Path] | None = None,
) -> list[dict[str, Any]]:
    """Shorten only expendable reveal tail time to expose one legal effect window.

    The scene and audio boundaries never move. A changed reveal records which
    late phases were compressed and which recognition phases remain protected.
    """

    scenes = list(project.get("scenes", []))
    transitions = transition_plan(scenes, words) if project.get("version", 1) >= 3 else []
    transition_by_scene = {item["fromSceneId"]: item for item in transitions}
    changes: list[dict[str, Any]] = []
    for scene in scenes:
        annotation = annotations.get(str(scene.get("id")), {})
        elements = list(annotation.get("elements", []))
        if not elements:
            continue
        scene_start = int(scene.get("start_ms", 0))
        scene_end = int(scene.get("end_ms", scene_start))
        scene_duration = int(annotation.get("sceneDurationMs") or (scene_end - scene_start))
        local_word_ends = [
            int(word.get("end_ms", 0)) - scene_start
            for word in words
            if scene_start <= int(word.get("start_ms", -1)) < scene_end
        ]
        narration_rel_end = min(scene_duration, max(local_word_ends)) if local_word_ends else scene_duration
        transition = transition_by_scene.get(str(scene.get("id")))
        transition_rel_start = (
            int(transition["eraseStartMs"]) - scene_start if transition else None
        )
        visual_shot = next(
            (shot for shot in (visual_plan or {}).get("shots", []) if shot.get("section_id") == scene.get("id")),
            None,
        )
        visual_beats = list((visual_shot or {}).get("beats", []))
        image_path = (board_images or {}).get(str(scene.get("id")))
        candidates: list[dict[str, Any]] = []
        has_viable_window = False
        for index, element in enumerate(elements):
            target = focus_target_from_pixels(annotation, element, image_path)
            if image_path is None:
                target["confidence"] = 0.18
                target["source"] = "annotation-region-compat"
            element_keys = {
                str(element.get("id", "")),
                str(element.get("label", "")),
                str(element.get("sequence", "")),
            }
            visual_beat = next(
                (beat for beat in visual_beats if str(beat.get("target", "")) in element_keys),
                None,
            )
            if semantic_word_window(scene, element, words)["mapping"] != "exact-trigger-text" and visual_beat is None:
                continue
            requested, semantic_intent, semantic_evidence = semantic_effect_for(scene, element, visual_beat)
            if requested not in DEFAULT_EFFECTS:
                continue
            threshold = _effect_confidence_min(requested)
            if float(target.get("confidence", 0.0) or 0.0) < threshold:
                continue
            window = _available_window(
                scene,
                element,
                elements[index + 1] if index + 1 < len(elements) else None,
                scene_duration,
                transition_rel_start,
                narration_rel_end,
                allow_concurrent_draw=requested in LEGACY_PIXEL_EFFECTS,
            )
            if window["budgetMs"] >= EFFECT_MIN_MS[requested]:
                has_viable_window = True
                break
            candidates.append({
                "index": index,
                "element": element,
                "effect": requested,
                "intent": semantic_intent,
                "evidence": semantic_evidence,
                "target": target,
                "window": window,
            })
        if has_viable_window:
            continue
        candidates.sort(
            key=lambda item: (
                -float(item["target"].get("confidence", 0.0) or 0.0),
                -int(item["index"]),
            )
        )
        drawing_plan = annotation.get("drawingPlan", {}) if isinstance(annotation.get("drawingPlan"), dict) else {}
        profile = project.get("renderer_profile") or {}
        if (drawing_plan.get("mode", profile.get("draw_mode", "layered")) != "layered"
                or drawing_plan.get("strokePlanner", profile.get("stroke_planner", "semantic-v2")) != "semantic-v2"
                or drawing_plan.get("colorSchedule", profile.get("color_schedule", "object-progressive-v1")) != "object-progressive-v1"
                or profile.get("color_fill", "local-brush") != "local-brush"):
            continue
        for candidate in candidates:
            element = candidate["element"]
            reveal = element.get("reveal", {})
            start_ms = int(reveal.get("startMs", 0))
            original_duration = int(reveal.get("durationMs", 0))
            required_effect_ms = max(900, min(1200, EFFECT_MIN_MS[candidate["effect"]]))
            latest_draw_end = (
                int(candidate["window"]["boundaryMs"])
                - POST_ACTION_HOLD_MS
                - required_effect_ms
                - PRE_ACTION_MS
            )
            new_duration = latest_draw_end - start_ms
            if new_duration >= original_duration or new_duration <= 0:
                continue
            compressed_ms = original_duration - new_duration
            try:
                # Preview and production use integer frames. Admit a reservation
                # only when protected phases fit across the supported fps range.
                for fps in range(8, 61):
                    compile_budget(start_ms, new_duration, original_duration, fps)
            except ValueError:
                continue
            reveal["durationMs"] = new_duration
            reveal["animationTimingReserve"] = {
                "effect": candidate["effect"],
                "reservedMs": required_effect_ms,
                "originalDurationMs": original_duration,
                "compressedMs": compressed_ms,
                "compressedPhases": ["texture", "finalize"],
                "protectedPhases": ["recognition", "base_color"],
                "audioExtendedMs": 0,
                "phaseBudgetPolicy": PHASE_POLICY,
            }
            changes.append({
                "sceneId": scene.get("id"),
                "targetElementId": str(element.get("id") or element.get("sequence")),
                "effect": candidate["effect"],
                "originalDurationMs": original_duration,
                "durationMs": new_duration,
                "reservedMs": required_effect_ms,
                "narrationBoundaryMs": narration_rel_end,
                "reason": "compressed-expendable-frame-budget",
                "phaseBudgetPolicy": PHASE_POLICY,
            })
            break
    return changes


def build_animation_plan(
    project: dict[str, Any],
    annotations: dict[str, dict[str, Any]],
    words: list[dict[str, Any]],
    visual_plan: dict[str, Any] | None = None,
    board_images: dict[str, str | Path] | None = None,
    style_config: dict[str, Any] | None = None,
    hand_mode: str = "small-hand",
) -> dict[str, Any]:
    scenes = list(project.get("scenes", []))
    transitions = transition_plan(scenes, words) if project.get("version", 1) >= 3 else []
    transition_by_scene = {item["fromSceneId"]: item for item in transitions}
    plan_scenes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for scene_index, scene in enumerate(scenes):
        annotation = annotations.get(scene["id"], {})
        elements = list(annotation.get("elements", []))
        scene_duration = int(annotation.get("sceneDurationMs") or (int(scene["end_ms"]) - int(scene["start_ms"])))
        transition = transition_by_scene.get(scene["id"])
        transition_rel_start = None
        if transition:
            transition_rel_start = int(transition["eraseStartMs"]) - int(scene["start_ms"])
        visual_shot = next(
            (shot for shot in (visual_plan or {}).get("shots", []) if shot.get("section_id") == scene["id"]),
            None,
        )
        visual_beats = list((visual_shot or {}).get("beats", []))
        image_path = (board_images or {}).get(scene["id"])
        local_word_ends = [
            int(word.get("end_ms", 0)) - int(scene["start_ms"])
            for word in words
            if int(scene["start_ms"]) <= int(word.get("start_ms", -1)) < int(scene["end_ms"])
        ]
        narration_rel_end = min(scene_duration, max(local_word_ends)) if local_word_ends else scene_duration
        candidates: list[dict[str, Any]] = []
        for index, element in enumerate(elements):
            target = focus_target_from_pixels(annotation, element, image_path)
            if image_path is None:
                # Older callers may construct a plan before boards are
                # attached. Keep that read path compatible, but mark the
                # target at the exact low-confidence threshold so a formal
                # project render still requires board pixels.
                target["confidence"] = 0.18
                target["source"] = "annotation-region-compat"
            semantic = semantic_word_window(scene, element, words)
            element_keys = {
                str(element.get("id", "")),
                str(element.get("label", "")),
                str(element.get("sequence", "")),
            }
            visual_beat = next(
                (beat for beat in visual_beats if str(beat.get("target", "")) in element_keys),
                None,
            )
            requested, semantic_intent, semantic_evidence = semantic_effect_for(scene, element, visual_beat)
            if semantic["mapping"] != "exact-trigger-text" and visual_beat is None:
                requested = "skip"
            window = _available_window(
                scene,
                element,
                elements[index + 1] if index + 1 < len(elements) else None,
                scene_duration,
                transition_rel_start,
                narration_rel_end,
                allow_concurrent_draw=requested in LEGACY_PIXEL_EFFECTS,
            )
            effective = requested
            fallback: dict[str, Any] | None = None
            threshold = _effect_confidence_min(requested) if requested in DEFAULT_EFFECTS else 1.0
            minimum_ms = EFFECT_MIN_MS.get(requested, 0)
            if requested == "skip":
                fallback = {
                    "from": "automatic-emphasis",
                    "to": "skip",
                    "reason": "没有明确语义动作证据，保持稳定画面",
                }
            elif float(target.get("confidence", 0.0) or 0.0) < threshold:
                effective = "skip"
                fallback = {
                    "from": requested,
                    "to": "skip",
                    "reason": f"真实前景目标置信度低于 {threshold:.2f}",
                }
            elif window["budgetMs"] < minimum_ms:
                effective = "skip"
                fallback = {
                    "from": requested,
                    "to": "skip",
                    "reason": f"{requested} 可用窗口低于 {minimum_ms}ms，不拉长时间也不制造替代线条",
                }
            directive_beat = dict(visual_beat or {})
            directive_beat.setdefault("motion_role", semantic_intent)
            directive_beat.setdefault("semantic_goal", str(element.get("narrativeRole") or element.get("role") or element.get("label") or ""))
            directive_beat.setdefault("start_state", f"{element.get('label') or element.get('id') or '目标'} 已完整绘制")
            directive_beat.setdefault("action", requested if requested != "skip" else "stable-hold")
            directive_beat.setdefault("end_state", "强调结束后恢复稳定画面")
            directive_beat.setdefault("static_anchors", [])
            directive_beat.setdefault("fallback", "stable-hold")
            candidates.append({
                "element": element,
                "target": target,
                "semantic": semantic,
                "window": window,
                "effect": effective,
                "requestedEffect": requested,
                "fallback": fallback,
                "visualBeat": directive_beat,
                "semanticIntent": semantic_intent,
                "semanticEvidence": semantic_evidence,
                "priority": (
                    float(target.get("confidence", 0.0) or 0.0) * 3.0
                    + min(2.0, window["budgetMs"] / 1800)
                    + (1.0 if index == len(elements) - 1 else 0.0)
                    + {"focus-push": 0.8, "passing-flash": 0.7, "highlight-wash": 0.6, "contour-indicate": 0.5}.get(effective, 0.0)
                ),
            })

        # The first V3.3 round keeps one semantic post-reveal event per scene.
        # Effects may only color or transform real target pixels; no automatic
        # line, arrow, bracket, circle, or rectangle is introduced.
        viable = [
            item for item in candidates
            if item["effect"] in DEFAULT_EFFECTS
            and float(item["target"].get("confidence", 0.0) or 0.0) >= _effect_confidence_min(item["effect"])
            and item["window"]["budgetMs"] >= EFFECT_MIN_MS[item["effect"]]
        ]
        viable.sort(key=lambda item: (-item["priority"], int(item["window"]["actionStartMs"])))
        selected = viable[:1]
        events: list[dict[str, Any]] = []
        scene_skipped: list[dict[str, Any]] = []
        for item in selected:
            event = _make_event(
                scene,
                item["element"],
                item["semantic"],
                item["effect"],
                item["window"]["actionStartMs"],
                item["window"]["actionEndMs"],
                item["window"]["boundaryMs"],
                item["target"],
                [],
                False,
                item["fallback"],
                style_config,
                item["visualBeat"],
            )
            event["window"] = item["window"]
            event["requestedEffect"] = item["requestedEffect"]
            event["semanticEvidence"] = item["semanticEvidence"]
            event["handBehavior"]["mode"] = hand_mode
            event["handBehavior"]["tipAnchorSource"] = (
                "presenter.json:assets.drawing.anchor"
                if hand_mode == "presenter"
                else "hand-assets.json:assets.drawing.anchor"
            )
            events.append(event)

        selected_ids = {str(item["element"].get("id") or item["element"].get("sequence")) for item in selected}
        for item in candidates:
            if str(item["element"].get("id") or item["element"].get("sequence")) in selected_ids:
                continue
            fallback = item["fallback"] or {
                "from": item["requestedEffect"],
                "to": "skip",
                "reason": "第一轮每幕最多一个语义后动画，其他元素保持稳定画面",
            }
            skipped_item = {
                "sceneId": scene["id"],
                "targetElementId": str(item["element"].get("id") or item["element"].get("sequence")),
                "availableWindowMs": item["window"]["budgetMs"],
                "targetConfidence": round(float(item["target"].get("confidence", 0.0) or 0.0), 4),
                "targetSource": item["target"].get("source"),
                "semanticIntent": item["semanticIntent"],
                "semanticEvidence": item["semanticEvidence"],
                "fallback": fallback,
            }
            skipped.append(skipped_item)
            scene_skipped.append(skipped_item)

        plan_scenes.append({
            "sceneId": scene["id"],
            "sceneStartMs": int(scene["start_ms"]),
            "sceneEndMs": int(scene["end_ms"]),
            "narrationEndMs": int(scene["end_ms"]),
            "renderEndMs": int(project["scenes"][scene_index + 1]["start_ms"])
            if scene_index + 1 < len(project["scenes"]) else int(scene["end_ms"]),
            "sceneDurationMs": scene_duration,
            "composition": scene.get("composition", ""),
            "recipe": "semantic-safe-director-v3.3",
            "events": sorted(events, key=lambda item: (item["startMs"], item["auxiliary"], item["id"])),
            "transition": transition,
            "visualPlanShotId": (visual_shot or {}).get("shot_id"),
            "visualBeatCount": len(visual_beats),
            "skipped": scene_skipped,
        })

    return {
        "version": 3,
        "planVersion": DEFAULT_PLAN_VERSION,
        "timebase": "absolute-word-ms",
        "source": {
            "projectVersion": project.get("version", 1),
            "wordCount": len(words),
            "visualPlanVersion": (visual_plan or {}).get("version"),
            "geometry": "semantic-target-from-foreground-pixels-only",
            "note": "新计划不生成局部轮廓、染色高亮或流光效果；只在明确聚焦与足够时间时使用 focus-push，不补画线、框、圆或箭头；没有证据、目标或时间时保持稳定画面。上游改变后保留本文件并标为失效，显式重建或保存完整修订计划后重新确认。",
        },
        "rules": {
            "min_gesture_ms": MIN_GESTURE_MS,
            "simplified_gesture_min_ms": SIMPLIFIED_GESTURE_MS,
            "full_gesture_min_ms": FULL_GESTURE_MS,
            "focus_push_min_ms": FOCUS_PUSH_MS,
            "focus_push_confidence_min": FOCUS_PUSH_CONFIDENCE_MIN,
            "safe_target_confidence_min": SAFE_TARGET_CONFIDENCE_MIN,
            "effect_min_ms": dict(EFFECT_MIN_MS),
            "pre_action_reserve_ms": PRE_ACTION_MS,
            "post_action_stable_ms": POST_ACTION_HOLD_MS,
            "default_effects": ["focus-push"],
            "legacy_effects": [
                "gesture-arc", "gesture-underline", "gesture-bracket", "gesture-arrow",
                "pulse", "pulse/outline", "outline", "hand-drawn-circle", "circle", "focus-zoom"
            ],
            "max_semantic_effects_per_scene": 1,
            "default_overlay_paths": False,
            "invent_geometry": False,
            "target_mask_source": "foreground-pixels-within-focusTarget",
            "stable_scene_without_event": True,
            "max_primary_effects_per_semantic_sentence": 1,
            "max_simultaneous_moving_subjects": 1,
            "focus_push_max": 0.06,
            "focus_push_return": "disabled-by-default",
            "stop_other_motion_before_eraser": True,
            "subtitle_layer": "final-composite-only",
            "effect_runs_during_narration": True,
            "animation_window_reserve_ms": [900, 1200],
        },
        "scenes": plan_scenes,
        "transitions": transitions,
        "skipped": skipped,
    }


def _validate_v32_plan(
    plan: dict[str, Any],
    annotations: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Validate the V3.2 default: zero or one camera-only focus push."""

    errors: list[str] = []
    for scene in plan.get("scenes", []):
        scene_id = str(scene.get("sceneId", ""))
        duration = int(scene.get("sceneDurationMs", 0))
        annotation = (annotations or {}).get(scene_id, {})
        canvas = annotation.get("canvas") or {}
        width = int(canvas.get("width", 0) or 0)
        height = int(canvas.get("height", 0) or 0)
        target_ids = {
            str(element.get("id") or element.get("label") or element.get("sequence"))
            for element in annotation.get("elements", [])
        }
        events = list(scene.get("events", []))
        if len(events) > 1:
            errors.append(f"{scene_id} 超过每幕 1 个 focus-push")
        transition = scene.get("transition") or {}
        erase_start = int(transition.get("eraseStartMs", int(scene.get("sceneStartMs", 0)) + duration))
        previous_end = -1
        for event in sorted(events, key=lambda item: int(item.get("startMs", 0))):
            effect = str(event.get("effect", ""))
            target_id = str(event.get("targetElementId") or event.get("targetElement") or "")
            start = int(event.get("startMs", -1))
            end = int(event.get("endMs", -1))
            budget = int(event.get("timeBudgetMs", end - start))
            if effect != "focus-push":
                errors.append(f"{scene_id}/{target_id} V3.2 默认只允许 focus-push：{effect}")
            if target_ids and target_id not in target_ids:
                errors.append(f"{scene_id}/{target_id} 未指向 annotation 元素")
            if start < 0 or end <= start or end > duration:
                errors.append(f"{scene_id}/{target_id} 动画时间越界 {start}-{end}/{duration}")
            if budget != end - start:
                errors.append(f"{scene_id}/{target_id} timeBudgetMs 与真实窗口不一致")
            if effect == "focus-push" and budget < FOCUS_PUSH_MS:
                errors.append(f"{scene_id}/{target_id} focus-push 低于 1200ms")
            if event.get("gesturePath") or event.get("path"):
                errors.append(f"{scene_id}/{target_id} V3.2 focus-push 不得带自动 gesturePath/path")

            target = event.get("focusTarget") or {}
            target_ok = str(target.get("source", "")) == "foreground-pixels"
            try:
                x, y = int(target["x"]), int(target["y"])
                w, h = int(target["width"]), int(target["height"])
                safe_top = int(target.get("subtitleSafeTopPx", round(height * SUBTITLE_SAFE_TOP_RATIO)))
            except (KeyError, TypeError, ValueError):
                target_ok = False
                x = y = w = h = safe_top = 0
            if float(target.get("confidence", 0.0) or 0.0) < FOCUS_PUSH_CONFIDENCE_MIN:
                target_ok = False
            if width and height and (x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height or y + h > safe_top):
                target_ok = False
            if not target_ok:
                errors.append(f"{scene_id}/{target_id} focusTarget 必须是高置信度前景目标且位于字幕安全区")

            phase = event.get("phase") or {}
            required = ("prepare", "camera", "hold", "leave")
            phase_ranges: list[tuple[int, int]] = []
            phase_ok = True
            for name in required:
                bounds = phase.get(name)
                if not isinstance(bounds, dict):
                    phase_ok = False
                    continue
                try:
                    left, right = int(bounds.get("startMs", 0)), int(bounds.get("endMs", 0))
                except (TypeError, ValueError):
                    phase_ok = False
                    continue
                phase_ranges.append((left, right))
                if right < left or left < start or right > end:
                    phase_ok = False
            if any(left[1] > right[0] for left, right in zip(phase_ranges, phase_ranges[1:])):
                phase_ok = False
            prepare = phase.get("prepare") or {}
            camera = phase.get("camera") or {}
            hold = phase.get("hold") or {}
            if int(prepare.get("endMs", 0)) - int(prepare.get("startMs", 0)) not in range(80, 121):
                phase_ok = False
            camera_ms = int(camera.get("endMs", 0)) - int(camera.get("startMs", 0))
            if camera_ms < 400 or camera_ms > 650:
                phase_ok = False
            hold_ms = int(hold.get("endMs", 0)) - int(hold.get("startMs", 0))
            if hold_ms < 400:
                phase_ok = False
            if "draw" in phase:
                phase_ok = False
            if not phase_ok:
                errors.append(f"{scene_id}/{target_id} V3.2 focus-push 阶段必须是 prepare/camera/hold/leave，且推近 400–650ms、停留至少 400ms")
            intensity = float(event.get("intensity", 0.0) or 0.0)
            if intensity < 0.04 or intensity > 0.06:
                errors.append(f"{scene_id}/{target_id} focus-push 缩放必须在 4%–6%")
            if (event.get("handBehavior") or {}).get("hideDuringCamera") is not True:
                errors.append(f"{scene_id}/{target_id} focus-push 未声明镜头期间隐藏手部")
            style = event.get("style") or {}
            if not str(style.get("colorSource", "")).startswith("style-registry:"):
                errors.append(f"{scene_id}/{target_id} 强调色没有回指 style registry")
            persist = int(event.get("persistUntilMs", end))
            if persist - end < POST_ACTION_HOLD_MS:
                errors.append(f"{scene_id}/{target_id} focus-push 后稳定画面不足 250ms")
            erase_rel = erase_start - int(scene.get("sceneStartMs", 0))
            if end + POST_ACTION_HOLD_MS > erase_rel:
                errors.append(f"{scene_id}/{target_id} 未在板擦前保留 250ms 稳定时间")
            if previous_end >= 0 and start < previous_end:
                errors.append(f"{scene_id}/{target_id} 与相邻动画重叠")
            previous_end = max(previous_end, end)
    return errors


def _validate_v33_plan(
    plan: dict[str, Any],
    annotations: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Validate one pathless, foreground-backed semantic event per scene."""

    errors: list[str] = []
    rules = plan.get("rules") or {}
    if rules.get("default_overlay_paths") is not False:
        errors.append("V3.3 必须关闭自动 overlay 路径")
    if rules.get("invent_geometry") is not False:
        errors.append("V3.3 必须关闭自动几何图形")
    if int(rules.get("max_semantic_effects_per_scene", 0) or 0) != 1:
        errors.append("V3.3 第一轮必须限制每幕最多一个语义后动画")

    for scene in plan.get("scenes", []):
        scene_id = str(scene.get("sceneId", ""))
        duration = int(scene.get("sceneDurationMs", 0) or 0)
        annotation = (annotations or {}).get(scene_id, {})
        canvas = annotation.get("canvas") or {}
        width = int(canvas.get("width", 0) or 0)
        height = int(canvas.get("height", 0) or 0)
        elements = {
            str(element.get("id") or element.get("label") or element.get("sequence")): element
            for element in annotation.get("elements", [])
        }
        events = list(scene.get("events", []))
        if len(events) > 1:
            errors.append(f"{scene_id} 超过每幕一个语义后动画")
        transition = scene.get("transition") or {}
        erase_abs = int(transition.get("eraseStartMs", int(scene.get("sceneStartMs", 0)) + duration))
        erase_rel = erase_abs - int(scene.get("sceneStartMs", 0))
        previous_end = -1

        for event in sorted(events, key=lambda item: int(item.get("startMs", 0))):
            effect = str(event.get("effect", ""))
            target_id = str(event.get("targetElementId") or event.get("targetElement") or "")
            start = int(event.get("startMs", -1))
            end = int(event.get("endMs", -1))
            budget = int(event.get("timeBudgetMs", end - start))
            if effect not in DEFAULT_EFFECTS:
                errors.append(f"{scene_id}/{target_id} V3.3 使用未知或不安全效果：{effect}")
            if elements and target_id not in elements:
                errors.append(f"{scene_id}/{target_id} 未指向 annotation 元素")
            if start < 0 or end <= start or end > duration:
                errors.append(f"{scene_id}/{target_id} 动画时间越界 {start}-{end}/{duration}")
            if budget != end - start:
                errors.append(f"{scene_id}/{target_id} timeBudgetMs 与真实窗口不一致")
            if effect in EFFECT_MIN_MS and budget < EFFECT_MIN_MS[effect]:
                errors.append(f"{scene_id}/{target_id} {effect} 低于 {EFFECT_MIN_MS[effect]}ms")
            if event.get("gesturePath") or event.get("path"):
                errors.append(f"{scene_id}/{target_id} V3.3 不得带自动 gesturePath/path")

            element = elements.get(target_id) or {}
            reveal = element.get("reveal") or {}
            element_end = int(reveal.get("startMs", 0)) + int(reveal.get("durationMs", 0))
            if elements and start < element_end + PRE_ACTION_MS:
                errors.append(f"{scene_id}/{target_id} 在目标完整绘制前开始动画")

            target = event.get("focusTarget") or {}
            target_ok = str(target.get("source", "")) == "foreground-pixels"
            try:
                x, y = int(target["x"]), int(target["y"])
                w, h = int(target["width"]), int(target["height"])
                safe_top = int(target.get("subtitleSafeTopPx", round(height * SUBTITLE_SAFE_TOP_RATIO)))
            except (KeyError, TypeError, ValueError):
                target_ok = False
                x = y = w = h = safe_top = 0
            if float(target.get("confidence", 0.0) or 0.0) < _effect_confidence_min(effect):
                target_ok = False
            if width and height and (x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height or y + h > safe_top):
                target_ok = False
            if not target_ok:
                errors.append(f"{scene_id}/{target_id} 必须使用字幕安全区内的高置信度真实前景目标")

            if (
                event.get("targetMaskSource") != "foreground-pixels-within-focusTarget"
                or (event.get("effectOptions") or {}).get("inventGeometry") is not False
                or (event.get("effectOptions") or {}).get("preserveOriginalPixels") is not True
            ):
                errors.append(f"{scene_id}/{target_id} 没有声明真实像素遮罩与禁止创造几何图形")
            if event.get("fallbackPolicy") != "stable-hold":
                errors.append(f"{scene_id}/{target_id} 缺少 stable-hold 安全降级")
            if not all(str(event.get(key, "")).strip() for key in ("semanticIntent", "semanticGoal", "startState", "action", "endState")):
                errors.append(f"{scene_id}/{target_id} 缺少语义目的或起点-动作-终点")

            phase = event.get("phase") or {}
            required = ("prepare", "camera", "hold", "leave") if effect == "focus-push" else ("prepare", "accent", "hold", "leave")
            phase_ranges: list[tuple[int, int]] = []
            phase_ok = True
            for name in required:
                bounds = phase.get(name)
                if not isinstance(bounds, dict):
                    phase_ok = False
                    continue
                left, right = int(bounds.get("startMs", 0)), int(bounds.get("endMs", 0))
                phase_ranges.append((left, right))
                if right < left or left < start or right > end:
                    phase_ok = False
            if any(left[1] > right[0] for left, right in zip(phase_ranges, phase_ranges[1:])):
                phase_ok = False
            prepare = phase.get("prepare") or {}
            if int(prepare.get("endMs", 0)) - int(prepare.get("startMs", 0)) not in range(80, 121):
                phase_ok = False
            hold = phase.get("hold") or {}
            if int(hold.get("endMs", 0)) - int(hold.get("startMs", 0)) < POST_ACTION_HOLD_MS:
                phase_ok = False
            if effect == "focus-push":
                camera = phase.get("camera") or {}
                camera_ms = int(camera.get("endMs", 0)) - int(camera.get("startMs", 0))
                if camera_ms < 400 or camera_ms > 650 or "accent" in phase:
                    phase_ok = False
                intensity = float(event.get("intensity", 0.0) or 0.0)
                if intensity < 0.04 or intensity > 0.06:
                    errors.append(f"{scene_id}/{target_id} focus-push 缩放必须在 4%–6%")
                if (event.get("handBehavior") or {}).get("hideDuringCamera") is not True:
                    errors.append(f"{scene_id}/{target_id} focus-push 未声明镜头期间隐藏手部")
            else:
                accent = phase.get("accent") or {}
                accent_ms = int(accent.get("endMs", 0)) - int(accent.get("startMs", 0))
                if accent_ms < 400 or accent_ms > 650 or "camera" in phase:
                    phase_ok = False
                if (event.get("handBehavior") or {}).get("showHandForEffect") is not False:
                    errors.append(f"{scene_id}/{target_id} 安全后动画不得伪装成手绘动作")
            if not phase_ok:
                errors.append(f"{scene_id}/{target_id} 动画阶段不符合 V3.3 时间门禁")

            style = event.get("style") or {}
            if not str(style.get("colorSource", "")).startswith("style-registry:"):
                errors.append(f"{scene_id}/{target_id} 强调色没有回指 style registry")
            persist = int(event.get("persistUntilMs", end))
            if persist - end < POST_ACTION_HOLD_MS:
                errors.append(f"{scene_id}/{target_id} 动画后稳定画面不足 250ms")
            if end + POST_ACTION_HOLD_MS > erase_rel:
                errors.append(f"{scene_id}/{target_id} 未在板擦前保留 250ms 稳定时间")
            if persist > erase_rel:
                errors.append(f"{scene_id}/{target_id} persistUntilMs 已进入板擦区间")
            if previous_end >= 0 and start < previous_end:
                errors.append(f"{scene_id}/{target_id} 与相邻动画重叠")
            previous_end = max(previous_end, end)
    errors.extend(_validate_v33_transition_copies(plan))
    return errors


def _validate_v33_transition_copies(plan: dict[str, Any]) -> list[str]:
    """Keep scene transitions and the top-level transition index identical.

    Event times are scene-local, while transition times are absolute word-clock
    milliseconds. V3.3 deliberately stores each transition twice for efficient
    scene rendering and cross-scene QA, so both copies must remain byte-level
    equivalent as JSON objects.
    """

    errors: list[str] = []
    scenes = list(plan.get("scenes") or [])
    transitions = list(plan.get("transitions") or [])
    top_by_scene: dict[str, dict[str, Any]] = {}
    for transition in transitions:
        scene_id = str(transition.get("fromSceneId", ""))
        if not scene_id:
            errors.append("顶层 transitions[] 存在缺少 fromSceneId 的条目")
            continue
        if scene_id in top_by_scene:
            errors.append(f"顶层 transitions[] 中 {scene_id} 出现重复条目")
            continue
        top_by_scene[scene_id] = transition

    scene_ids = {str(scene.get("sceneId", "")) for scene in scenes}
    for scene_id in sorted(set(top_by_scene) - scene_ids):
        errors.append(f"顶层 transitions[] 的 {scene_id} 没有对应场景")

    for index, scene in enumerate(scenes):
        scene_id = str(scene.get("sceneId", ""))
        scene_transition = scene.get("transition") or None
        top_transition = top_by_scene.get(scene_id)
        is_final = index == len(scenes) - 1
        if is_final and (scene_transition or top_transition):
            errors.append(f"最后一幕 {scene_id} 不得设置板擦转场")
            continue
        if bool(scene_transition) != bool(top_transition):
            errors.append(f"{scene_id} 的 scenes[].transition 与顶层 transitions[] 缺失状态不一致")
            continue
        if not scene_transition:
            continue
        if scene_transition != top_transition:
            errors.append(f"{scene_id} 的 scenes[].transition 与顶层 transitions[] 内容不一致")

        transition = scene_transition
        required = (
            "fromSceneId", "toSceneId", "previousWordEndMs", "nextSceneFirstWordMs",
            "gapMs", "stableHoldMs", "eraseStartMs", "eraseEndMs", "eraseDurationMs",
            "cleanCanvasStartMs", "cleanCanvasEndMs", "cleanCanvasMs", "status",
        )
        missing = [key for key in required if key not in transition]
        if missing:
            errors.append(f"{scene_id} 转场缺少派生字段：{', '.join(missing)}")
            continue
        try:
            scene_start = int(scene.get("sceneStartMs", 0))
            # The silence after narration belongs to the outgoing render. Never
            # stretch the narration/annotation interval to accommodate an erase.
            scene_end = int(scenes[index + 1].get("sceneStartMs", 0)) if not is_final else int(scene.get("sceneEndMs", 0))
            if "renderEndMs" in scene and int(scene["renderEndMs"]) != scene_end:
                errors.append(f"{scene_id} renderEndMs 必须等于下一幕开始时间")
            previous_end = int(transition["previousWordEndMs"])
            next_start = int(transition["nextSceneFirstWordMs"])
            erase_start = int(transition["eraseStartMs"])
            erase_end = int(transition["eraseEndMs"])
            clean_start = int(transition["cleanCanvasStartMs"])
            clean_end = int(transition["cleanCanvasEndMs"])
            stable_hold = int(transition["stableHoldMs"])
            erase_duration = int(transition["eraseDurationMs"])
            clean_duration = int(transition["cleanCanvasMs"])
            gap = int(transition["gapMs"])
        except (TypeError, ValueError):
            errors.append(f"{scene_id} 转场时间字段必须是整数")
            continue

        next_scene_id = str(scenes[index + 1].get("sceneId", "")) if not is_final else ""
        if transition.get("fromSceneId") != scene_id or transition.get("toSceneId") != next_scene_id:
            errors.append(f"{scene_id} 转场的前后幕 ID 不一致")
        if not (
            scene_start <= previous_end <= erase_start < erase_end
            and erase_end == clean_start < clean_end <= scene_end
        ):
            errors.append(f"{scene_id} 转场时间顺序或场景边界无效")
        if stable_hold != erase_start - previous_end or stable_hold < STABLE_HOLD_MS:
            errors.append(f"{scene_id} 转场完整画面停留必须为非负值且与时间字段一致")
        if erase_duration != erase_end - erase_start or not MIN_ERASE_MS <= erase_duration <= MAX_ERASE_MS:
            errors.append(f"{scene_id} 板擦时长必须为 {MIN_ERASE_MS}-{MAX_ERASE_MS}ms 且与时间字段一致")
        if clean_duration != clean_end - clean_start or clean_duration < CLEAN_CANVAS_MS:
            errors.append(f"{scene_id} 净板时长必须至少 {CLEAN_CANVAS_MS}ms 且与时间字段一致")
        if erase_duration + clean_duration > MAX_ACTIVE_TRANSITION_MS:
            errors.append(f"{scene_id} 擦除与净板合计不得超过 {MAX_ACTIVE_TRANSITION_MS}ms")
        if gap != next_start - previous_end or gap < MIN_TRANSITION_GAP_MS:
            errors.append(f"{scene_id} 真实停顿预算必须至少 {MIN_TRANSITION_GAP_MS}ms 且与时间字段一致")
        if clean_end > next_start:
            errors.append(f"{scene_id} 净板结束不得晚于下一幕首词")
        if transition.get("status") != "ready":
            errors.append(f"{scene_id} 转场状态必须为 ready")
    return errors


def validate_animation_plan(
    plan: dict[str, Any],
    annotations: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Validate V3.3 defaults and retain V3.2/V3.1 compatibility paths."""

    plan_version = str(plan.get("planVersion", ""))
    if plan_version == DEFAULT_PLAN_VERSION:
        try:
            for item in plan.get("timingReservations", []):
                events = [event for scene in plan.get("scenes", [])
                          if str(scene.get("sceneId")) == str(item.get("sceneId"))
                          for event in scene.get("events", [])]
                if not any(str(event.get("targetElementId")) == str(item.get("targetElementId"))
                           and event.get("effect") == item.get("effect") for event in events):
                    return ["时长预留没有对应的已选后动画；不能无故压缩对象窗口"]
            if annotations is not None:
                known = set(annotations)
                for item in plan.get("timingReservations", []):
                    if str(item.get("sceneId")) not in known:
                        return ["时长预留引用了未知场景"]
                annotations = {sid: effective_annotation(ann, sid, plan) for sid, ann in annotations.items()}
        except (ValueError, TypeError, KeyError) as exc:
            return [f"时长预留无效：{exc}"]
        return _validate_v33_plan(plan, annotations)
    if plan_version == V32_PLAN_VERSION:
        return _validate_v32_plan(plan, annotations)
    if plan_version != LEGACY_PLAN_VERSION:
        return ["未知的 animation plan 版本"]
    errors: list[str] = []
    allowed_effects = {
        "gesture-arc", "gesture-underline", "gesture-bracket", "gesture-arrow", "focus-push",
        # Legacy readers may still receive these; the new planner never emits them.
        "focus-zoom", "pulse", "pulse/outline", "outline", "hand-drawn-circle", "circle",
    }
    for scene in plan.get("scenes", []):
        scene_id = str(scene.get("sceneId", ""))
        duration = int(scene.get("sceneDurationMs", 0))
        annotation = (annotations or {}).get(scene_id, {})
        canvas = annotation.get("canvas") or {}
        width = int(canvas.get("width", 0))
        height = int(canvas.get("height", 0))
        target_ids = {
            str(element.get("id") or element.get("label") or element.get("sequence"))
            for element in annotation.get("elements", [])
        }
        events = list(scene.get("events", []))
        if len(events) > 2:
            errors.append(f"{scene_id} 超过每幕 2 个显著强调")
        for event in events:
            effect = str(event.get("effect", ""))
            target_id = str(event.get("targetElementId") or event.get("targetElement") or "")
            start = int(event.get("startMs", -1))
            end = int(event.get("endMs", -1))
            budget = int(event.get("timeBudgetMs", end - start))
            if effect not in allowed_effects:
                errors.append(f"{scene_id}/{target_id} 使用未知效果 {effect}")
            if target_ids and target_id not in target_ids:
                errors.append(f"{scene_id}/{target_id} 未指向 annotation 元素")
            if start < 0 or end <= start or end > duration:
                errors.append(f"{scene_id}/{target_id} 动画时间越界 {start}-{end}/{duration}")
            if effect in {"gesture-arc", "gesture-underline", "gesture-bracket", "gesture-arrow"} and budget < MIN_GESTURE_MS:
                errors.append(f"{scene_id}/{target_id} 手绘动作低于 700ms")
            if effect == "focus-push" and budget < FOCUS_PUSH_MS:
                errors.append(f"{scene_id}/{target_id} focus-push 低于 1200ms")
            target = event.get("focusTarget") or {}
            if str(target.get("source", "")).startswith("foreground") and width and height:
                x, y = int(target.get("x", -1)), int(target.get("y", -1))
                w, h = int(target.get("width", -1)), int(target.get("height", -1))
                safe_top = int(target.get("subtitleSafeTopPx", round(height * SUBTITLE_SAFE_TOP_RATIO)))
                if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height or y + h > safe_top:
                    errors.append(f"{scene_id}/{target_id} focusTarget 超出画布或字幕安全区")
            path = event.get("gesturePath") or event.get("path") or []
            if effect.startswith("gesture-") and len(path) < 2:
                errors.append(f"{scene_id}/{target_id} 缺少 gesturePath")
            for point in path:
                if width and height and (len(point) < 2 or point[0] < 0 or point[1] < 0 or point[0] > width or point[1] > height):
                    errors.append(f"{scene_id}/{target_id} gesturePath 越界")
            phase = event.get("phase") or {}
            if effect.startswith("gesture-") and not all(key in phase for key in ("prepare", "draw", "hold", "leave")):
                errors.append(f"{scene_id}/{target_id} 缺少完整手势阶段")
            if effect == "focus-push" and not all(key in phase for key in ("prepare", "draw", "camera", "hold", "leave")):
                errors.append(f"{scene_id}/{target_id} 缺少 focus-push 阶段")
            style = event.get("style") or {}
            if not str(style.get("colorSource", "")).startswith("style-registry:"):
                errors.append(f"{scene_id}/{target_id} 强调色没有回指 style registry")
        transition = scene.get("transition") or {}
        if transition and events:
            erase_start = int(transition.get("eraseStartMs", 0)) - int(scene.get("sceneStartMs", 0))
            for event in events:
                if int(event.get("endMs", 0)) + POST_ACTION_HOLD_MS > erase_start:
                    errors.append(f"{scene_id}/{event.get('id', 'animation')} 未在板擦前保留 250ms 稳定时间")
    return errors
