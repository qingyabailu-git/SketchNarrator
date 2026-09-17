#!/usr/bin/env python3
"""Deterministic visual orchestration between locked voice timing and boards.

This module only plans sections, shots, beats, templates, and asset needs.  It
does not generate images or bind the project to a presenter IP.
"""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


class VisualPlanError(RuntimeError):
    """Raised when a visual plan cannot be mapped to the locked word timeline."""


SHOT_TYPES = {
    "a_host",
    "a_reaction",
    "b_whiteboard",
    "b_infographic",
    "b_screenshot",
    "b_text",
}
TEMPLATES = {
    "question",
    "focus",
    "three-items",
    "comparison",
    "cause",
    "process",
    "timeline",
    "summary",
}
COMPOSITIONS = {
    "causal-chain",
    "before-after",
    "center-spoke",
    "timeline",
    "vertical-layers",
    "character-action",
    "text-card",
    "quote-card",
    "split-text",
    "screenshot-frame",
    "host-frame",
    "reaction-frame",
}
MIN_SHOT_MS = 2_000
LONG_SHOT_BEAT_MS = 12_000
MIN_TRANSITION_PAUSE_MS = 300
OPENING_ANCHOR_START_MS = 100
OPENING_ANCHOR_RECOMMENDED_MS = 200
OPENING_ANCHOR_MAX_MS = 500
OPENING_ANCHOR_MAPPING = "opening-anchor"
PUNCTUATION = "，。！？；：、,.!?;:"

MOTION_ROLE_TERMS = {
    "relation": ("箭头", "关系线", "连接线", "流程线", "路径", "流向", "因果线", "arrow", "flow", "path"),
    "conclusion": ("结论", "总结", "结果", "核心", "重点", "关键", "归纳", "判断", "所以", "因此", "summary", "result", "conclusion"),
    "warning": ("提醒", "注意", "警告", "风险", "危险", "避坑", "warning", "risk", "caution"),
    "focus": ("聚焦", "放大", "推近", "focus", "push"),
}
PREFERRED_EFFECT_BY_ROLE = {
    "relation": "",
    "conclusion": "",
    "warning": "",
    "focus": "focus-push",
}
def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _word_id(word: dict[str, Any], index: int) -> str:
    for key in ("id", "word_id", "wordId"):
        candidate = _text(word.get(key))
        if candidate:
            return candidate
    return f"w-{index + 1:04d}"


def _prepare_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(words, list) or not words:
        raise VisualPlanError("words.json 必须包含非空 words 数组")
    prepared: list[dict[str, Any]] = []
    previous_start = -1
    for index, source in enumerate(words):
        if not isinstance(source, dict) or not _text(source.get("text")):
            raise VisualPlanError(f"words.json 第 {index + 1} 项无效")
        start, end = source.get("start_ms"), source.get("end_ms")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
            raise VisualPlanError(f"words.json 第 {index + 1} 项时间无效")
        if start < previous_start:
            raise VisualPlanError("words.json 必须按时间排序")
        previous_start = start
        item = dict(source)
        item["_index"] = index
        item["_id"] = _word_id(source, index)
        prepared.append(item)
    return prepared


def _scene_word_indices(scene: dict[str, Any], words: list[dict[str, Any]]) -> list[int]:
    start_ms, end_ms = int(scene["start_ms"]), int(scene["end_ms"])
    return [
        word["_index"]
        for word in words
        if int(word["start_ms"]) >= start_ms and int(word["end_ms"]) <= end_ms
    ]


def _asset_exists(value: str, project_root: Path | None) -> bool:
    if not project_root or not value or value.startswith(("presenter:", "screenshot:")):
        return False
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.is_file()


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return []


def _presenter_assets(project: dict[str, Any], visual: dict[str, Any]) -> list[str]:
    values: list[str] = []
    values.extend(_as_string_list(project.get("presenter_assets")))
    values.extend(_as_string_list(visual.get("presenter_assets")))
    assets = project.get("assets")
    if isinstance(assets, dict):
        values.extend(_as_string_list(assets.get("presenter")))
    return list(dict.fromkeys(values))


def _requested_shot(scene: dict[str, Any], visual: dict[str, Any]) -> str:
    requested = _text(visual.get("shot_type") or scene.get("shot_type"))
    if requested:
        return requested
    mode = _text(visual.get("mode") or scene.get("mode")).casefold()
    if mode in {"a-roll", "a_roll", "a"} or _text(visual.get("actor_slot") or scene.get("actor_slot")):
        return "a_host"
    return "b_whiteboard"


def _paper_metaphor_template(scene: dict[str, Any], visual: dict[str, Any]) -> str:
    """Map paper-metaphor keywords into the existing template vocabulary."""

    combined = "".join([
        _text(scene.get("title")),
        _text(scene.get("narration")),
        _text(scene.get("purpose")),
        _text(visual.get("purpose")),
    ])
    routes = (
        ("process", ("流程", "系统", "步骤", "阶段", "自动化", "生产")),
        ("comparison", ("对比", "选择", "判断", "两种", "权衡", "平衡", "边界")),
        ("cause", ("原因", "结果", "影响", "关系", "导致", "改变")),
        ("three-items", ("层级", "成长", "方向", "进阶", "清单", "资源", "矩阵")),
    )
    for template, keywords in routes:
        if any(keyword in combined for keyword in keywords):
            return template
    return "focus"


def _template_for(
    scene: dict[str, Any],
    visual: dict[str, Any],
    index: int,
    total: int,
    style_id: str = "",
) -> str:
    explicit = _text(visual.get("template") or scene.get("template"))
    if explicit:
        return explicit
    if style_id == "paper-metaphor-collage":
        return _paper_metaphor_template(scene, visual)
    narration = _text(scene.get("narration"))
    title = _text(scene.get("title"))
    composition = _text(visual.get("composition") or scene.get("composition"))
    combined = f"{title}{narration}"
    if "?" in combined or "？" in combined or any(token in combined for token in ("为什么", "怎么", "如何", "是否")):
        return "question"
    if composition == "before-after" or any(token in combined for token in ("对比", "区别", "前后", "错误", "正确")):
        return "comparison"
    if composition == "causal-chain" or any(token in combined for token in ("因为", "原因", "导致", "所以", "结果")):
        return "cause"
    if composition == "timeline" or any(token in combined for token in ("第一", "第二", "接着", "最后", "阶段", "时间")):
        return "timeline" if composition == "timeline" or "时间" in combined else "process"
    if composition in {"vertical-layers", "character-action"} or any(token in combined for token in ("步骤", "流程", "先", "再", "然后")):
        return "process"
    elements = visual.get("elements") or scene.get("elements") or []
    if isinstance(elements, list) and len(elements) >= 3:
        return "three-items"
    if index == total - 1 and any(token in combined for token in ("总结", "记住", "一句话", "因此")):
        return "summary"
    return "focus"


def _default_composition(
    shot_type: str,
    template: str,
    scene: dict[str, Any],
    visual: dict[str, Any],
    ordinal: int,
) -> str:
    explicit = _text(visual.get("composition") or scene.get("composition"))
    if explicit:
        return explicit
    if shot_type == "b_text":
        return ("text-card", "quote-card", "split-text")[ordinal % 3]
    if shot_type == "b_screenshot":
        return "screenshot-frame"
    if shot_type == "a_host":
        return "host-frame"
    if shot_type == "a_reaction":
        return "reaction-frame"
    template_map = {
        "question": "center-spoke",
        "focus": "center-spoke",
        "three-items": "timeline",
        "comparison": "before-after",
        "cause": "causal-chain",
        "process": "vertical-layers",
        "timeline": "timeline",
        "summary": "before-after",
    }
    return template_map.get(template, "center-spoke")


def _fallback_for(
    requested: str,
    scene_id: str,
    scene: dict[str, Any],
    project: dict[str, Any],
    visual: dict[str, Any],
    project_root: Path | None,
) -> tuple[str, dict[str, Any] | None, list[str]]:
    required = _as_string_list(visual.get("required_assets") or project.get("required_assets"))
    actor_slot = _text(visual.get("actor_slot") or scene.get("actor_slot")) or "default"
    if requested in {"a_host", "a_reaction"}:
        assets = _presenter_assets(project, visual)
        available = any(_asset_exists(item, project_root) for item in assets)
        if not available:
            required.append(f"presenter:{actor_slot}")
            return "b_text", {
                "from_shot_type": requested,
                "to_shot_type": "b_text",
                "reason": "missing-presenter-assets",
            }, list(dict.fromkeys(required))
    if requested == "b_screenshot":
        screenshot = _text(
            visual.get("screenshot_asset")
            or scene.get("screenshot_asset")
            or visual.get("recording_asset")
            or scene.get("recording_asset")
        )
        if screenshot:
            required.append(screenshot)
            if _asset_exists(screenshot, project_root):
                return requested, None, list(dict.fromkeys(required))
        required.append(f"screenshot:{scene_id}")
        return "b_whiteboard", {
            "from_shot_type": requested,
            "to_shot_type": "b_whiteboard",
            "reason": "missing-screenshot-asset",
        }, list(dict.fromkeys(required))
    return requested, None, list(dict.fromkeys(required))


def _element_list(scene: dict[str, Any], visual: dict[str, Any]) -> list[dict[str, Any]]:
    raw = visual.get("elements") or scene.get("elements") or []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if isinstance(item, dict):
            element = dict(item)
        else:
            element = {"label": _text(item)}
        element.setdefault("sequence", index)
        element.setdefault("label", f"视觉元素 {index}")
        result.append(element)
    return result or [{"sequence": 1, "label": _text(scene.get("title")) or "核心信息"}]


def _slice_indices(indices: list[int], count: int, ordinal: int) -> list[int]:
    if not indices:
        return []
    left = (ordinal * len(indices)) // count
    right = ((ordinal + 1) * len(indices)) // count
    if right <= left:
        return [indices[min(left, len(indices) - 1)]]
    return indices[left:right]


def _phrase_indices(words: list[dict[str, Any]], indices: list[int], phrase: str) -> list[int]:
    """Match across word tokens; reject ambiguous repeated phrases."""
    needle = _compact(phrase)
    if not needle:
        return []
    text = ""
    owners: list[int] = []
    for index in indices:
        token = _compact(_text(words[index]["text"]))
        text += token
        owners.extend([index] * len(token))
    offset = text.find(needle)
    if offset < 0 or text.find(needle, offset + 1) >= 0:
        return []
    return list(dict.fromkeys(owners[offset:offset + len(needle)]))


def _motion_directive(element: dict[str, Any], forced: dict[str, Any] | None) -> dict[str, Any]:
    """Describe why a post-reveal animation may exist without inventing one.

    Explicit beat fields win. Automatic classification is deliberately narrow:
    only a visible relation/path, conclusion, warning, or explicit focus cue is
    eligible. Ordinary people and objects remain stable after their region is
    drawn.
    """

    source = forced or {}
    explicit_role = _text(source.get("motion_role") or source.get("motionRole"))
    explicit_effect = _text(source.get("preferred_effect") or source.get("preferredEffect"))
    evidence = " ".join(
        _text(value)
        for value in (
            source.get("semantic_goal"), source.get("action"), source.get("purpose"),
            element.get("visual_action"), element.get("type"), element.get("role"),
            element.get("narrativeRole"), element.get("label"),
        )
    ).casefold()
    role = explicit_role
    if not role:
        for candidate, terms in MOTION_ROLE_TERMS.items():
            if any(term.casefold() in evidence for term in terms):
                role = candidate
                break
    if role not in PREFERRED_EFFECT_BY_ROLE:
        role = "none"
    preferred = explicit_effect or PREFERRED_EFFECT_BY_ROLE.get(role, "")
    if preferred not in set(PREFERRED_EFFECT_BY_ROLE.values()):
        preferred = ""
        role = "none"
    target = _text(source.get("target") or element.get("id") or element.get("label") or element.get("sequence"))
    goal = _text(
        source.get("semantic_goal")
        or source.get("semanticGoal")
        or element.get("narrativeRole")
        or element.get("role")
        or element.get("label")
    )
    static_anchors = _as_string_list(source.get("static_anchors") or source.get("staticAnchors") or element.get("static_anchors"))
    return {
        "motion_role": role,
        "semantic_goal": goal,
        "start_state": _text(source.get("start_state") or source.get("startState") or f"{target} 已完整绘制"),
        "end_state": _text(source.get("end_state") or source.get("endState") or "强调结束后恢复稳定画面"),
        "static_anchors": static_anchors,
        "animation_candidate": bool(preferred),
        "preferred_effect": preferred or None,
        "fallback": _text(source.get("fallback") or "stable-hold"),
    }


def _make_beat(
    shot_id: str,
    words: list[dict[str, Any]],
    indices: list[int],
    ordinal: int,
    element: dict[str, Any],
    forced: dict[str, Any] | None = None,
) -> dict[str, Any]:
    anchor_index = indices[0]
    anchor = words[anchor_index]
    action = _text((forced or {}).get("action") or element.get("visual_action") or "reveal")
    target = _text((forced or {}).get("target") or element.get("id") or element.get("label") or element.get("sequence"))
    moving = _as_string_list((forced or {}).get("moving_subjects") or element.get("moving_subjects"))
    beat = {
        "beat_id": f"{shot_id}-beat-{ordinal + 1:02d}",
        "trigger_phrase_id": anchor["_id"],
        "trigger_word_id": anchor["_id"],
        "trigger_word_index": anchor_index,
        "trigger_text": "".join(_text(words[index]["text"]) for index in indices),
        "start_ms": int(anchor["start_ms"]),
        "end_ms": int(anchor["end_ms"]),
        "action": action,
        "target": target,
        "mapping": "actual-word-window",
        "moving_subjects": moving,
    }
    relation = element.get("relation")
    if isinstance(relation, dict):
        beat["semantic_relation"] = {
            key: relation[key]
            for key in ("type", "from", "to", "narrationEvidence")
            if key in relation
        }
    beat.update(_motion_directive(element, forced))
    return beat


def _explicit_beats(
    shot_id: str,
    explicit: Any,
    words: list[dict[str, Any]],
    scene_indices: list[int],
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(explicit, list):
        return []
    by_id = {word["_id"]: word for word in words}
    result: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(explicit):
        if not isinstance(raw, dict):
            continue
        anchor_id = _text(raw.get("trigger_word_id") or raw.get("trigger_phrase_id"))
        anchor = by_id.get(anchor_id)
        if not anchor:
            matched = _phrase_indices(words, scene_indices, _text(raw.get("trigger_text")))
            anchor = words[matched[0]] if matched else None
        if not anchor or anchor["_index"] not in scene_indices:
            raise VisualPlanError(f"{shot_id} 的 beat 缺少可映射到 words.json 的真实触发边界")
        target = _text(raw.get("target"))
        element = next((item for item in elements if target in {_text(item.get("id")), _text(item.get("label")), _text(item.get("sequence"))}), None) if target else elements[min(ordinal, len(elements) - 1)]
        if element is None:
            raise VisualPlanError(f"{shot_id} 的 beat target 不属于本幕元素：{target}")
        result.append(_make_beat(shot_id, words, [anchor["_index"]], ordinal, element, raw))
    return result


def _apply_opening_anchor(
    beats: list[dict[str, Any]],
    shot_start_ms: int,
    shot_end_ms: int,
) -> None:
    """Keep the first visible stroke independent from its semantic trigger word."""
    if not beats:
        return
    first = beats[0]
    trigger_start = int(first.get("start_ms", shot_start_ms))
    trigger_end = int(first.get("end_ms", trigger_start))
    anchor_start = min(shot_start_ms + OPENING_ANCHOR_START_MS, max(shot_start_ms, shot_end_ms - 1))
    first["mapping"] = OPENING_ANCHOR_MAPPING
    first["trigger_window_ms"] = {
        "start_ms": trigger_start,
        "end_ms": trigger_end,
    }
    first["start_ms"] = anchor_start
    first["end_ms"] = max(trigger_end, min(shot_end_ms, anchor_start + 50))
    if int(first["end_ms"]) <= int(first["start_ms"]):
        first["end_ms"] = min(shot_end_ms, anchor_start + 1)


def _make_beats(
    shot_id: str,
    scene: dict[str, Any],
    visual: dict[str, Any],
    words: list[dict[str, Any]],
    scene_indices: list[int],
    shot_duration: int,
) -> list[dict[str, Any]]:
    elements = _element_list(scene, visual)
    explicit = _explicit_beats(shot_id, visual.get("beats") or scene.get("visual_beats"), words, scene_indices, elements)
    if explicit:
        beats = explicit
    else:
        beats = []
        for ordinal, element in enumerate(elements):
            phrase = _text(element.get("trigger_text") or element.get("triggerText") or element.get("narrationEvidence"))
            matched = _phrase_indices(words, scene_indices, phrase) if phrase else []
            if not matched:
                raise VisualPlanError(f"{shot_id} 元素 {element.get('id') or element.get('label')} 缺少唯一语义触发短句；请补 trigger_text 或显式 visual_beats 的真实词 ID，禁止平均分配时间")
            beats.append(_make_beat(shot_id, words, matched, ordinal, element))
    _apply_opening_anchor(
        beats,
        int(words[scene_indices[0]]["start_ms"]),
        int(words[scene_indices[-1]]["end_ms"]),
    )
    if shot_duration > LONG_SHOT_BEAT_MS:
        internal = [
            beat for beat in beats
            if beat.get("mapping") != OPENING_ANCHOR_MAPPING
            and int(beat["start_ms"]) > int(words[scene_indices[0]]["start_ms"])
            and int(beat["start_ms"]) < int(words[scene_indices[-1]]["end_ms"])
        ]
        if not internal:
            raise VisualPlanError(f"{shot_id} 长镜头缺少内部语义节拍；请提供真实触发短句，不能自动在中点添加强调")
    return beats


def _normalized_box(value: Any, label: str) -> list[float]:
    if not (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) for number in value)
    ):
        raise VisualPlanError(f"{label} 必须是 [x, y, width, height] 归一化区域")
    x, y, width, height = [float(number) for number in value]
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 0.84:
        raise VisualPlanError(f"{label} 超出画布或侵入底部字幕区")
    return [round(number, 4) for number in (x, y, width, height)]


LAYOUT_PRESETS = {
    "single": [[0.05, 0.07, 0.90, 0.72]],
    "side-by-side": [[0.05, 0.07, 0.43, 0.72], [0.52, 0.07, 0.43, 0.72]],
    "three-step": [[0.05, 0.07, 0.27, 0.72], [0.365, 0.07, 0.27, 0.72], [0.68, 0.07, 0.27, 0.72]],
    "hero-side": [[0.05, 0.07, 0.54, 0.72], [0.63, 0.07, 0.32, 0.34], [0.63, 0.45, 0.32, 0.34]],
    "grid-2x2": [[x, y, 0.43, 0.34] for y in (0.07, 0.45) for x in (0.05, 0.52)],
    "grid-3x2": [[x, y, 0.27, 0.34] for y in (0.07, 0.45) for x in (0.05, 0.365, 0.68)],
}


def _validate_spatial_allocation(regions: list[dict[str, Any]], element_ids: list[str], shot_id: str) -> None:
    """Validate pre-generation space, never the user's editable annotation boxes."""
    if not element_ids or any(not item for item in element_ids) or len(set(element_ids)) != len(element_ids):
        raise VisualPlanError(f"{shot_id} 空间规划需要每个绘制单元具有唯一稳定 ID")
    assigned = []
    region_ids = set()
    boxes = []
    for region in regions:
        ids = region.get("element_ids")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            raise VisualPlanError(f"{shot_id} 每个生图区域只分配一个独立绘制单元；不可分的对象须先在分镜中定义为同一单元")
        assigned.extend(ids)
        region_id = region.get("region_id")
        if not isinstance(region_id, str) or not region_id or region_id in region_ids:
            raise VisualPlanError(f"{shot_id} 生图区域 ID 缺失或重复")
        region_ids.add(region_id)
        boxes.append(_normalized_box(region.get("box"), f"{shot_id} 生图区域"))
    if sorted(assigned) != sorted(element_ids):
        raise VisualPlanError(f"{shot_id} 生图区域必须恰好覆盖全部对象 ID，不得遗漏、重复或引用未知对象")
    for index, (x, y, w, h) in enumerate(boxes):
        for bx, by, bw, bh in boxes[index + 1:]:
            if min(x + w, bx + bw) - max(x, bx) > 0.00001 and min(y + h, by + bh) - max(y, by) > 0.00001:
                raise VisualPlanError(f"{shot_id} 生图区域相互重叠；请在生图前调整空间，工作台编辑保存不受此限制")


def _native_layout_plan(
    shot: dict[str, Any],
    scene: dict[str, Any],
    visual: dict[str, Any],
) -> dict[str, Any]:
    """Allocate one space per semantic drawing unit before spending image quota."""
    elements = _element_list(scene, visual)
    element_ids = [_text(item.get("id")) for item in elements]
    if not 1 <= len(elements) <= 6:
        raise VisualPlanError(f"{shot['shot_id']} 生图前请将本幕整理为 1–6 个完整绘制单元，不能为填满布局硬拆对象")
    source = visual.get("layout") if isinstance(visual.get("layout"), dict) else {}
    explicit = source.get("native_regions")
    template = _text(source.get("template"))
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise VisualPlanError(f"{shot['shot_id']} native_regions 必须是非空对象数组")
        if template and template != "custom":
            raise VisualPlanError("自定义 native_regions 使用 template=custom，不可同时指定预设")
        template = "custom"
        regions = []
        for index, item in enumerate(explicit, 1):
            if not isinstance(item, dict):
                raise VisualPlanError("native_regions 必须是对象数组")
            regions.append({
                "region_id": _text(item.get("region_id")) or f"native-{index:02d}",
                "element_ids": item.get("element_ids"),
                "purpose": _text(item.get("purpose")) or "手绘语义画面",
                "box": _normalized_box(item.get("box"), "native region"),
            })
    else:
        template = template or {1: "single", 2: "side-by-side", 3: "three-step", 4: "grid-2x2", 5: "grid-3x2", 6: "grid-3x2"}[len(elements)]
        slots = LAYOUT_PRESETS.get(template)
        if slots is None or (len(slots) != len(elements) and not (template == "grid-3x2" and len(elements) == 5)):
            raise VisualPlanError(f"{shot['shot_id']} 布局 {template} 与对象数量不匹配；请选合适预设或 custom 区域")
        regions = [{
            "region_id": f"native-{index + 1:02d}",
            "element_ids": [element_ids[index]],
            "purpose": _text(item.get("label")),
            "box": list(slots[index]),
        } for index, item in enumerate(elements)]
    _validate_spatial_allocation(regions, element_ids, shot["shot_id"])
    return {
        "planned_before_board": True,
        "coordinate_space": "normalized-0-1",
        "template": template,
        "caption_region": [0.05, 0.86, 0.90, 0.12],
        "native_regions": regions,
        "semantic_ownership": {"native": [{
            "semantic_id": element_ids[index],
            "spoken_span": _text(item.get("label")),
            "role": _text(item.get("role") or item.get("type")) or "entity-action",
            "primary_owner": "native",
        } for index, item in enumerate(elements)]},
        "planning_sequence": ["semantic-decomposition", "time-budget", "spatial-allocation", "style-and-sound", "board-generation"],
        "board_generation_contract": "draw-inside-native-regions",
    }


def _transition_out(shot: dict[str, Any], next_shot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not next_shot:
        return None
    gap = int(next_shot["start_ms"]) - int(shot["end_ms"])
    return {
        "type": "real-pause-eraser",
        "pause_start_ms": int(shot["end_ms"]),
        "pause_end_ms": int(next_shot["start_ms"]),
        "pause_ms": gap,
        "audio_extension_ms": 0,
        "status": "ready" if gap >= MIN_TRANSITION_PAUSE_MS else "insufficient-real-pause",
    }


def _summary(shots: list[dict[str, Any]], words: list[dict[str, Any]], required_assets: list[dict[str, Any]]) -> dict[str, Any]:
    effective_a = sum(shot["mode"] == "A-roll" for shot in shots)
    effective_b = len(shots) - effective_a
    requested_a = sum(str(shot["requested_shot_type"]).startswith("a_") for shot in shots)
    fallbacks = [
        {
            "shot_id": shot["shot_id"],
            "from": shot["requested_shot_type"],
            "to": shot["shot_type"],
            "reason": shot["fallback"]["reason"],
        }
        for shot in shots
        if shot.get("fallback")
    ]
    markers = sorted({int(shot["start_ms"]) for shot in shots} | {
        int(beat["start_ms"]) for shot in shots for beat in shot.get("beats", [])
    })
    timeline_start = int(words[0]["start_ms"])
    timeline_end = int(words[-1]["end_ms"])
    gaps = [markers[0] - timeline_start] if markers else [timeline_end - timeline_start]
    gaps.extend(right - left for left, right in zip(markers, markers[1:]))
    gaps.append(timeline_end - markers[-1] if markers else 0)
    return {
        "shot_count": len(shots),
        "section_count": len({shot["section_id"] for shot in shots}),
        "a_roll_shots": effective_a,
        "b_roll_shots": effective_b,
        "a_roll_ratio": round(effective_a / len(shots), 3) if shots else 0,
        "b_roll_ratio": round(effective_b / len(shots), 3) if shots else 0,
        "requested_a_roll_shots": requested_a,
        "template_counts": {
            template: sum(shot["template"] == template for shot in shots)
            for template in sorted(TEMPLATES)
            if any(shot["template"] == template for shot in shots)
        },
        "longest_no_visual_change_ms": max(gaps) if gaps else 0,
        "required_assets": required_assets,
        "a_roll_fallbacks": fallbacks,
        "short_shot_exceptions": [
            shot["shot_id"] for shot in shots if shot.get("duration_exception")
        ],
        "expression_modes": {
            "native": sum(shot.get("expression_mode") == "native" for shot in shots),
        },
    }


def build_visual_plan(
    project: dict[str, Any],
    words: list[dict[str, Any]],
    project_root: str | Path | None = None,
    *,
    require_complete_coverage: bool = True,
) -> dict[str, Any]:
    """Build and validate a visual plan from storyboard sections and real words.

    Isolated scene diagnostics keep the complete word list so global word IDs remain
    stable, but may defer whole-timeline coverage to the subsequent full build.
    """

    prepared_words = _prepare_words(words)
    scenes = project.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise VisualPlanError("storyboard.json 必须包含非空 scenes 数组")
    root = Path(project_root).expanduser().resolve() if project_root else None
    shots: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    assigned: list[int] = []
    for scene_index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise VisualPlanError("每个 section 必须是对象")
        section_id = _text(scene.get("section_id") or scene.get("id"))
        if not section_id:
            raise VisualPlanError("section 缺少 section_id/id")
        indices = _scene_word_indices(scene, prepared_words)
        if not indices:
            raise VisualPlanError(f"{section_id} 没有可映射的真实口播词边界")
        assigned.extend(indices)
        visual = scene.get("visual") if isinstance(scene.get("visual"), dict) else {}
        requested = _requested_shot(scene, visual)
        if requested not in SHOT_TYPES:
            raise VisualPlanError(f"{section_id} 使用了不支持的 shot_type：{requested}")
        effective, fallback, required = _fallback_for(requested, section_id, scene, project, visual, root)
        template = _template_for(
            scene,
            visual,
            scene_index,
            len(scenes),
            _text(project.get("style_id")),
        )
        if template not in TEMPLATES:
            raise VisualPlanError(f"{section_id} 使用了不支持的 template：{template}")
        composition = _default_composition(effective, template, scene, visual, scene_index)
        if composition not in COMPOSITIONS:
            raise VisualPlanError(f"{section_id} 使用了不支持的 composition：{composition}")
        shot_id = f"{section_id}-shot-01"
        first, last = prepared_words[indices[0]], prepared_words[indices[-1]]
        duration = int(last["end_ms"]) - int(first["start_ms"])
        shot: dict[str, Any] = {
            "shot_id": shot_id,
            "section_id": section_id,
            "word_index_start": indices[0],
            "word_index_end": indices[-1],
            "word_ids": [prepared_words[index]["_id"] for index in indices],
            "start_phrase_id": first["_id"],
            "end_phrase_id": last["_id"],
            "start_ms": int(first["start_ms"]),
            "end_ms": int(last["end_ms"]),
            "mode": "A-roll" if effective.startswith("a_") else "B-roll",
            "requested_mode": "A-roll" if requested.startswith("a_") else "B-roll",
            "requested_shot_type": requested,
            "shot_type": effective,
            "purpose": _text(visual.get("purpose") or scene.get("purpose") or scene.get("narration")),
            "template": template,
            "composition": composition,
            "actor_slot": _text(visual.get("actor_slot") or scene.get("actor_slot")) or None,
            "emotion": _text(visual.get("emotion") or scene.get("emotion")) or None,
            "action": _text(visual.get("action") or scene.get("action")) or None,
            "beats": [],
            "transition_out": None,
            "required_assets": required,
            "fallback": fallback,
            "title_card": copy.deepcopy(scene.get("title_card"))
            if isinstance(scene.get("title_card"), dict)
            else None,
        }
        if duration < MIN_SHOT_MS:
            shot["duration_exception"] = "short-content-section"
        shot["beats"] = _make_beats(shot_id, scene, visual, prepared_words, indices, duration)
        shot["expression_mode"] = "native"
        shot["layout_plan"] = _native_layout_plan(shot, scene, visual)
        shots.append(shot)
        sections.append({
            "section_id": section_id,
            "title": _text(scene.get("title")) or section_id,
            "purpose": shot["purpose"],
            "shot_ids": [shot_id],
            "title_card": copy.deepcopy(shot.get("title_card")),
        })

    if require_complete_coverage:
        if sorted(assigned) != list(range(len(prepared_words))):
            raise VisualPlanError("视觉镜头未按顺序完整覆盖 words.json，存在遗漏、重复或交叉")
    elif assigned != sorted(set(assigned)):
        raise VisualPlanError("视觉镜头的词边界存在重复或交叉")
    for left, right in zip(shots, shots[1:]):
        left["transition_out"] = _transition_out(left, right)
    required_assets = []
    seen_assets: set[str] = set()
    for shot in shots:
        for asset in shot.get("required_assets", []):
            if asset in seen_assets:
                continue
            seen_assets.add(asset)
            required_assets.append({
                "asset": asset,
                "status": "available" if _asset_exists(asset, root) else "missing-or-logical-slot",
                "used_by": [item["shot_id"] for item in shots if asset in item.get("required_assets", [])],
            })
    plan = {
        "version": 1,
        "timebase": "absolute-word-ms",
        "source": {
            "project_version": project.get("version", 1),
            "style_id": project.get("style_id", "warm-pencil"),
            "word_count": len(prepared_words),
            "mapping": "locked words.json word boundaries; no character-count timing",
        },
        "rules": {
            "min_normal_shot_ms": MIN_SHOT_MS,
            "long_shot_beat_threshold_ms": LONG_SHOT_BEAT_MS,
            "max_simultaneous_moving_subjects": 2,
            "transition_source": "real-word-gap-only",
            "audio_extension_ms": 0,
        },
        "sections": sections,
        "shots": shots,
        "summary": _summary(shots, prepared_words, required_assets),
    }
    validate_visual_plan(plan, words, require_complete_coverage=require_complete_coverage)
    return plan


def _validate_beats(shot: dict[str, Any], words: list[dict[str, Any]]) -> None:
    beats = shot.get("beats", [])
    if not isinstance(beats, list):
        raise VisualPlanError(f"{shot['shot_id']} 的 beats 必须是数组")
    active: list[tuple[int, int, set[str]]] = []
    for beat in beats:
        if not isinstance(beat, dict):
            raise VisualPlanError(f"{shot['shot_id']} 包含无效 beat")
        index = beat.get("trigger_word_index")
        if not isinstance(index, int) or not 0 <= index < len(words):
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 没有有效真实词索引")
        word = words[index]
        if beat.get("trigger_phrase_id") != _word_id(word, index):
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 未绑定真实 phrase/word anchor")
        start, end = beat.get("start_ms"), beat.get("end_ms")
        if not isinstance(start, int) or not isinstance(end, int) or end < start:
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 时间无效")
        if beat.get("mapping") == OPENING_ANCHOR_MAPPING:
            if start - int(shot["start_ms"]) > OPENING_ANCHOR_MAX_MS:
                raise VisualPlanError(
                    f"{shot['shot_id']} 的开场锚点晚于 {OPENING_ANCHOR_MAX_MS}ms"
                )
        elif start < int(word["start_ms"]) or end > int(word["end_ms"]):
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 早于或超出真实口播边界")
        if start < int(shot["start_ms"]) or end > int(shot["end_ms"]):
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 超出镜头边界")
        moving = _as_string_list(beat.get("moving_subjects"))
        if len(set(moving)) > 2:
            raise VisualPlanError(f"{shot['shot_id']} 同时运动主体超过 2 个")
        if end > start and moving:
            active.append((start, end, set(moving)))
        role = _text(beat.get("motion_role"))
        preferred = _text(beat.get("preferred_effect"))
        if role not in {*PREFERRED_EFFECT_BY_ROLE, "none"}:
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 使用未知 motion_role：{role}")
        if bool(beat.get("animation_candidate")) != bool(preferred):
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 动画候选与 preferred_effect 不一致")
        if preferred and preferred != PREFERRED_EFFECT_BY_ROLE.get(role):
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 效果与语义角色不匹配")
        if preferred and (
            not _text(beat.get("semantic_goal"))
            or not _text(beat.get("start_state"))
            or not _text(beat.get("end_state"))
            or _text(beat.get("fallback")) != "stable-hold"
        ):
            raise VisualPlanError(f"{shot['shot_id']} 的 beat 缺少语义动画起点、目标、终点或安全降级")
    boundaries = sorted({point for start, end, _ in active for point in (start, end)})
    for left, right in zip(boundaries, boundaries[1:]):
        subjects = set()
        for start, end, moving in active:
            if start <= left and end >= right:
                subjects.update(moving)
        if len(subjects) > 2:
            raise VisualPlanError(f"{shot['shot_id']} 同时运动主体超过 2 个")


def validate_visual_plan(
    plan: dict[str, Any],
    words: list[dict[str, Any]],
    *,
    require_complete_coverage: bool = True,
) -> None:
    prepared = _prepare_words(words)
    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        raise VisualPlanError("visual-plan.json 必须包含非空 shots 数组")
    seen_ids: set[str] = set()
    ranges: list[tuple[int, int]] = []
    for shot in shots:
        if not isinstance(shot, dict):
            raise VisualPlanError("每个 shot 必须是对象")
        shot_id = _text(shot.get("shot_id"))
        if not shot_id or shot_id in seen_ids:
            raise VisualPlanError(f"shot_id 缺失或重复：{shot_id!r}")
        seen_ids.add(shot_id)
        requested = _text(shot.get("requested_shot_type"))
        effective = _text(shot.get("shot_type"))
        if requested not in SHOT_TYPES or effective not in SHOT_TYPES:
            raise VisualPlanError(f"{shot_id} 的 shot_type 不受支持")
        if requested.startswith("a_") and not effective.startswith("a_"):
            fallback = shot.get("fallback")
            if not isinstance(fallback, dict) or fallback.get("to_shot_type") != effective:
                raise VisualPlanError(f"{shot_id} 的 A-roll 降级缺少明确 fallback")
        if requested == "b_screenshot" and not shot.get("required_assets") and not shot.get("fallback"):
            raise VisualPlanError(f"{shot_id} 缺失截图/录屏素材且没有 required_assets 或 fallback")
        template, composition = _text(shot.get("template")), _text(shot.get("composition"))
        if template not in TEMPLATES or composition not in COMPOSITIONS:
            raise VisualPlanError(f"{shot_id} 的 template/composition 不受支持")
        start_index, end_index = shot.get("word_index_start"), shot.get("word_index_end")
        if not isinstance(start_index, int) or not isinstance(end_index, int) or not 0 <= start_index <= end_index < len(prepared):
            raise VisualPlanError(f"{shot_id} 的 word index 范围无效")
        expected_ids = [_word_id(prepared[index], index) for index in range(start_index, end_index + 1)]
        if shot.get("word_ids") != expected_ids:
            raise VisualPlanError(f"{shot_id} 的 word_ids 与真实口播顺序不一致")
        if shot.get("start_phrase_id") != expected_ids[0] or shot.get("end_phrase_id") != expected_ids[-1]:
            raise VisualPlanError(f"{shot_id} 缺少真实 start/end phrase anchor")
        start_ms, end_ms = shot.get("start_ms"), shot.get("end_ms")
        if start_ms != prepared[start_index]["start_ms"] or end_ms != prepared[end_index]["end_ms"] or end_ms <= start_ms:
            raise VisualPlanError(f"{shot_id} 的时间不是由真实 words 边界决定")
        beats = shot.get("beats")
        if isinstance(beats, list) and beats and beats[0].get("mapping") == OPENING_ANCHOR_MAPPING:
            opening_delay = int(beats[0].get("start_ms", start_ms)) - int(start_ms)
            if opening_delay > OPENING_ANCHOR_MAX_MS:
                raise VisualPlanError(
                    f"{shot_id} 的开场锚点在幕开始后 {opening_delay}ms，超过 {OPENING_ANCHOR_MAX_MS}ms"
                )
        ranges.append((start_index, end_index))
        if end_ms - start_ms < MIN_SHOT_MS and not shot.get("duration_exception"):
            raise VisualPlanError(f"{shot_id} 短于 {MIN_SHOT_MS}ms 但未标注例外")
        if end_ms - start_ms > LONG_SHOT_BEAT_MS:
            internal = [beat for beat in shot.get("beats", []) if start_ms < int(beat.get("start_ms", start_ms)) < end_ms]
            if not internal:
                raise VisualPlanError(f"{shot_id} 超过 {LONG_SHOT_BEAT_MS}ms 但没有内部视觉 beat")
        _validate_beats(shot, prepared)
        layout = shot.get("layout_plan")
        if not isinstance(layout, dict) or layout.get("planned_before_board") is not True:
            raise VisualPlanError(f"{shot_id} 缺少生成整板图前的统一 layout_plan")
        native_regions = layout.get("native_regions")
        if not isinstance(native_regions, list) or not native_regions:
            raise VisualPlanError(f"{shot_id} 缺少预先规划的手绘区域")
        for native in native_regions:
            if not isinstance(native, dict):
                raise VisualPlanError(f"{shot_id} 的 native region 必须是对象")
            _normalized_box(native.get("box"), f"{shot_id} 的 native region")
        # Older approved layouts are read as-is; never silently re-layout them.
        if "template" in layout:
            ownership = layout.get("semantic_ownership", {}).get("native", [])
            _validate_spatial_allocation(native_regions, [_text(item.get("semantic_id")) for item in ownership], shot_id)
        if shot.get("expression_mode") != "native":
            raise VisualPlanError(f"{shot_id} 的 expression_mode 必须为 native")
    flattened = [index for start, end in ranges for index in range(start, end + 1)]
    if require_complete_coverage:
        if flattened != list(range(len(prepared))):
            raise VisualPlanError("visual-plan.json 未完整、按顺序覆盖全部口播词，存在遗漏、重复或交叉")
    elif flattened != sorted(set(flattened)):
        raise VisualPlanError("visual-plan.json 的词边界存在重复或交叉")
    for first, second, third in zip(shots, shots[1:], shots[2:]):
        if (first["template"], first["composition"]) == (second["template"], second["composition"]) == (third["template"], third["composition"]):
            raise VisualPlanError("连续三个镜头重复使用完全相同的 template 与 composition")


def _md(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def render_visual_plan_markdown(plan: dict[str, Any]) -> str:
    summary = plan["summary"]
    lines = [
        "# 中文视觉编排表",
        "",
        "> 时间锚点全部来自锁定后的 `audio/words.json`；本表不按字数估算秒数，也不生成图片。",
        "",
        "## 编排摘要",
        "",
        f"- 镜头总数：{summary['shot_count']}，章节数：{summary['section_count']}",
        f"- A-roll：{summary['a_roll_shots']}（{summary['a_roll_ratio']:.1%}）；B-roll：{summary['b_roll_shots']}（{summary['b_roll_ratio']:.1%}）",
        f"- 最长无视觉变化：{summary['longest_no_visual_change_ms']}ms",
        f"- 模板次数：{json.dumps(summary['template_counts'], ensure_ascii=False)}",
        f"- A-roll 降级：{json.dumps(summary['a_roll_fallbacks'], ensure_ascii=False) if summary['a_roll_fallbacks'] else '无'}",
        f"- 素材需求：{json.dumps(summary['required_assets'], ensure_ascii=False) if summary['required_assets'] else '无'}",
        "",
        "## 镜头表",
        "",
        "| 镜头 | 真实时间 | 文字卡片 | 表达方式 | 模式/类型 | 模板 | 构图 | 目的 | 手绘空间规划 | beats | 素材与降级 |",
        "|---|---:|---|---|---|---|---|---|---|---|---|",
    ]
    for shot in plan["shots"]:
        beats = "；".join(
            f"{_md(beat['trigger_text'])}→{_md(beat['action'])}/{_md(beat['target'])}@{beat['start_ms']}ms"
            + (
                f"[{_md(beat['motion_role'])}:{_md(beat['preferred_effect'])}]"
                if beat.get("animation_candidate") else "[稳定]"
            )
            for beat in shot.get("beats", [])
        ) or "无"
        assets = ", ".join(shot.get("required_assets", [])) or "无"
        if shot.get("fallback"):
            assets += f"；fallback→{shot['fallback']['to_shot_type']}（{shot['fallback']['reason']}）"
        actor = "/".join(_md(shot.get(key)) for key in ("actor_slot", "emotion", "action") if shot.get(key)) or "逻辑槽位无"
        title_card = shot.get("title_card") if isinstance(shot.get("title_card"), dict) else {}
        title_card_text = str(title_card.get("text") or "无")
        layout = shot.get("layout_plan", {})
        layout_text = (
            "布局=" + str(layout.get("template", "legacy"))
            + "；手绘=" + json.dumps([{ "对象": item.get("element_ids"), "内容": item.get("purpose"), "区域": item.get("box") } for item in layout.get("native_regions", [])], ensure_ascii=False)
            + "；字幕=" + json.dumps(layout.get("caption_region"), ensure_ascii=False)
        )
        lines.append(
            "| "
            + " | ".join([
                _md(shot["shot_id"]),
                f"{shot['start_ms']}–{shot['end_ms']}ms",
                _md(title_card_text),
                _md(shot.get("expression_mode", "native")),
                f"{_md(shot['mode'])}/{_md(shot['shot_type'])}",
                _md(shot["template"]),
                _md(shot["composition"]),
                _md(shot["purpose"]),
                _md(layout_text),
                _md(beats),
                _md(assets),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## 约束记录",
        "",
        "- 普通镜头短于 2 秒时只允许带明确的短内容例外；超过 12 秒必须有内部真实 beat。",
        "- 连续三个镜头不能完全复用同一模板与构图；同时运动主体最多两个。",
        "- 板擦只使用真实停顿，`audio_extension_ms` 固定为 0；截图/录屏缺失时只列需求或走 fallback，不伪造界面。",
        "- 绘制后动画必须记录语义目的、完整绘制的起始状态、稳定结束状态和 `stable-hold` 降级；普通对象不自动运动。",
        "- 每幕先统一锁定手绘区和字幕区，再生成整板图。",
    ])
    return "\n".join(lines) + "\n"


__all__ = [
    "COMPOSITIONS",
    "LONG_SHOT_BEAT_MS",
    "MIN_SHOT_MS",
    "SHOT_TYPES",
    "TEMPLATES",
    "VisualPlanError",
    "build_visual_plan",
    "render_visual_plan_markdown",
    "validate_visual_plan",
]
