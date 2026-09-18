#!/usr/bin/env python3
"""Deterministic project state, validation, rendering, and composition."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
_RENDERER_SCRIPTS = Path(__file__).resolve().parents[1] / "renderer" / "scripts"
if str(_RENDERER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RENDERER_SCRIPTS))
from animation_plan import (
    AnimationPlanError,
    DEFAULT_PLAN_VERSION,
    build_animation_plan,
    reserve_animation_windows,
    validate_animation_plan,
    validate_transition_pause_budgets,
    transition_plan,
)
from visual_director import (
    OPENING_ANCHOR_MAX_MS,
    VisualPlanError,
    build_visual_plan,
    render_visual_plan_markdown,
    validate_visual_plan,
)
from text_policy import (
    TextPolicyError,
    apply_pronunciation_overrides,
    empty_pronunciation_overrides,
    parse_srt_cues,
    validate_caption_contract,
)
from board_layout import fit_board_safe_area
from board_qa import run as run_board_qa, semantic_crop_risks
from script_text import read_narration_text
from sfx import (
    SfxError,
    build_plan as build_sfx_plan,
    render_track as render_sfx_track,
)
from project_lock import project_lock, serialized_project
from bookends import BookendError, compose_bookends, prepare_bookend_spec, probe_media
from ffmpeg_runtime import runtime_info as local_runtime_info
from font_runtime import cjk_font_identity
from local_defaults import LocalDefaultsError, apply_local_defaults
from source_privacy import require_public_source, source_label, redact_source_log
from phase_budget import effective_annotation as apply_effective_annotation, plan_budgets, PHASE_POLICY
from pixel_contract import require_ownership, resolve_ownership, compile_masks, PIXEL_POLICY
from pacing import (
    DEFAULT_PACING_RATIO,
    calculate_adaptive_durations,
    pace_project_annotations,
)

VERSION = 3
ALLOWED_IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
COMPOSITIONS = {
    "causal-chain",
    "before-after",
    "center-spoke",
    "timeline",
    "vertical-layers",
    "character-action",
}
STYLE_REGISTRY_PATH = Path(__file__).parents[1] / "references" / "style-registry.json"
TIMELINE_EDGE_TOLERANCE_MS = 120


def configure_cli_text_streams() -> None:
    """Make Chinese CLI output portable across terminal locale defaults."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
TIMELINE_DURATION_TOLERANCE_MS = 250
EXPECTED_QA_REPORT_VERSION = 6
CONNECTOR_LABEL_TOKENS = ("箭头", "关系线", "连接线", "流程线", "指向线", "arrow")
CONNECTOR_RELATION_TYPES = {"direction", "causal", "process", "flow", "distance", "comparison", "trend"}
SCENE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkflowError(RuntimeError):
    pass


def validate_scene_id(value: Any) -> str:
    """Return a filesystem-safe scene id or reject the project before path use."""
    scene_id = str(value or "").strip()
    if not SCENE_ID_PATTERN.fullmatch(scene_id):
        raise WorkflowError(
            f"场景 id 不安全：{scene_id!r}；只允许 1–128 位英文字母、数字、点、下划线和连字符，"
            "且必须以字母或数字开头"
        )
    return scene_id


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"JSON 无效：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"JSON 顶层必须是对象：{path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def project_fingerprint(root: Path, project: dict[str, Any] | None = None) -> str:
    """Return the stable identity used to bind a panel change set to one project state."""
    project = project or read_json(root / "project.json")
    storyboard_path = root / "storyboard.json"
    storyboard = read_json(storyboard_path) if storyboard_path.is_file() else {"scenes": []}
    scene_ids = ",".join(str(scene.get("id", "")) for scene in storyboard.get("scenes", []))
    return f"{digest(root / 'project.json')}:{scene_ids}:{project.get('version', VERSION)}"


def animation_scene_ids_changed(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Return scene ids whose animation payload changed; global rule changes affect every scene."""
    canonical = lambda value: json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    before_scenes = {
        str(scene.get("sceneId", "")): scene
        for scene in before.get("scenes", [])
        if isinstance(scene, dict) and scene.get("sceneId")
    }
    after_scenes = {
        str(scene.get("sceneId", "")): scene
        for scene in after.get("scenes", [])
        if isinstance(scene, dict) and scene.get("sceneId")
    }
    before_transitions = {
        str(item.get("fromSceneId", "")): item
        for item in before.get("transitions", [])
        if isinstance(item, dict) and item.get("fromSceneId")
    }
    after_transitions = {
        str(item.get("fromSceneId", "")): item
        for item in after.get("transitions", [])
        if isinstance(item, dict) and item.get("fromSceneId")
    }
    before_global = {
        key: value for key, value in before.items()
        if key not in {"scenes", "transitions"}
    }
    after_global = {
        key: value for key, value in after.items()
        if key not in {"scenes", "transitions"}
    }
    all_scene_ids = sorted(
        set(before_scenes) | set(after_scenes) | set(before_transitions) | set(after_transitions)
    )
    if canonical(before_global) != canonical(after_global):
        return all_scene_ids
    return [
        scene_id
        for scene_id in all_scene_ids
        if (
            canonical(before_scenes.get(scene_id)) != canonical(after_scenes.get(scene_id))
            or canonical(before_transitions.get(scene_id)) != canonical(after_transitions.get(scene_id))
        )
    ]


def canonical_project(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def load_project(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    project = read_json(root / "project.json")
    state = read_json(root / "state.json")
    seen_scene_ids: set[str] = set()
    for scene in project.get("scenes", []):
        if not isinstance(scene, dict):
            raise WorkflowError("project.json 的 scenes 必须只包含对象")
        scene_id = validate_scene_id(scene.get("id"))
        if scene_id in seen_scene_ids:
            raise WorkflowError(f"project.json 包含重复场景 id：{scene_id}")
        seen_scene_ids.add(scene_id)
    approvals = state.setdefault("approvals", {})
    if "script_style" not in approvals:
        artifacts = state.get("artifacts", {}) if isinstance(state.get("artifacts"), dict) else {}
        downstream_evidence = any(
            artifacts.get(key)
            for key in ("audio", "words", "captions", "storyboard", "visual_plan")
        ) or bool(state.get("boards")) or bool(state.get("final_current"))
        approvals["script_style"] = {
            "approved": bool(artifacts.get("script") and downstream_evidence),
            "at": None,
            "migration": "inferred-from-existing-downstream-artifacts" if artifacts.get("script") and downstream_evidence else None,
        }
    approvals.setdefault("script_voice", {"approved": False, "at": None})
    approvals.setdefault("boards", {"approved": False, "at": None})
    return project, state


def mark_boards_stale(state: dict[str, Any], reason: str) -> None:
    """Keep existing board links visible while revoking downstream approval."""
    boards = state.get("boards")
    if not isinstance(boards, dict):
        state["boards"] = {}
        return
    for record in boards.values():
        if not isinstance(record, dict):
            continue
        record["stale"] = True
        record["stale_reason"] = reason


def reconcile_existing_boards(
    root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
) -> list[str]:
    """Recover conventional in-project board files whose state links were lost."""
    boards = state.setdefault("boards", {})
    if not isinstance(boards, dict):
        boards = {}
        state["boards"] = boards
    root_resolved = root.resolve()

    def registered_file(relative: Any) -> Path | None:
        if not relative:
            return None
        try:
            candidate = (root / str(relative)).resolve()
            candidate.relative_to(root_resolved)
        except (OSError, RuntimeError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    recovered: list[str] = []
    for scene in project.get("scenes", []):
        scene_id = str(scene.get("id", "")).strip()
        if not scene_id:
            continue
        record = boards.get(scene_id) if isinstance(boards.get(scene_id), dict) else {}
        image_path = registered_file(record.get("image"))
        annotation_path = registered_file(record.get("annotation"))
        record_complete = image_path is not None and annotation_path is not None
        if image_path is None:
            image_path = next(
                (
                    candidate
                    for extension in (".png", ".jpg", ".jpeg", ".webp")
                    if (candidate := root / "boards" / f"{scene_id}{extension}").is_file()
                ),
                None,
            )
        if annotation_path is None:
            candidate = root / "annotations" / f"{scene_id}.annotation.json"
            annotation_path = candidate if candidate.is_file() else None
        if image_path is None or annotation_path is None:
            continue
        if record_complete:
            continue
        recovered_record = dict(record)
        recovered_record.update({
            "image": image_path.relative_to(root).as_posix(),
            "annotation": annotation_path.relative_to(root).as_posix(),
            "image_sha256": digest(image_path),
            "annotation_sha256": digest(annotation_path),
            "stale": True,
            "stale_reason": "recovered-existing-files",
        })
        boards[scene_id] = recovered_record
        recovered.append(scene_id)
    return recovered


def load_style_registry() -> dict[str, Any]:
    registry = read_json(STYLE_REGISTRY_PATH)
    styles = registry.get("styles")
    if not isinstance(styles, list) or not styles:
        raise WorkflowError("风格注册表缺少 styles")
    return registry


def resolve_style(value: str) -> dict[str, Any]:
    requested = value.strip()
    lowered = requested.casefold()
    styles = load_style_registry()["styles"]
    for style in styles:
        candidates = [style.get("id", ""), style.get("name", ""), *style.get("aliases", [])]
        if lowered in {str(candidate).strip().casefold() for candidate in candidates}:
            return style
    fallback = next(style for style in styles if style["id"] == "warm-pencil")
    return {
        "id": "custom",
        "name": requested,
        "aliases": [],
        "intended_use": "用户指定风格",
        "prompt": fallback["prompt"],
        "renderer": fallback["renderer"],
    }


STYLE_RECOMMENDATION_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("guofeng-flat", ("历史", "古代", "诗词", "传统", "国学", "文物", "朝代", "神话"), "传统文化或历史叙事"),
    ("retro-newspaper", ("档案", "报纸", "媒体", "旧闻", "回顾", "年代", "新闻史"), "档案、媒体或年代回顾"),
    ("orderly-color-doodle", ("开源", "开发者", "软件", "智能体", "自动化", "协作", "产品功能", "工具生态"), "软件工具、开源项目或协作机制的清晰手绘讲解"),
    ("black-gold-tech", ("人工智能", "AI", "芯片", "算力", "未来科技", "发布会", "机器人", "大模型"), "科技产品、AI 或未来机制展示"),
    ("paper-metaphor-collage", ("隐喻", "权衡", "边界", "价值", "选择", "层级", "抽象概念"), "抽象概念、选择或层级关系"),
    ("business-doodle", ("商业", "经济", "金融", "公司", "职场", "营销", "品牌", "投资", "数据"), "商业机制或数据关系"),
    ("dark-chalkboard", ("数学", "公式", "物理", "化学", "编程", "算法", "天文", "课堂", "原理"), "课程式原理讲解"),
    ("comic-ink", ("为什么", "反转", "冲突", "误区", "真相", "搞笑", "故事", "悬念"), "问题钩子、冲突或反转叙事"),
    ("minimal-whiteboard", ("教程", "步骤", "流程", "方法", "工具", "怎么", "指南", "操作"), "流程、工具或方法说明"),
    ("warm-pencil", ("健康", "生活", "食物", "饮食", "动物", "人体", "科普", "日常"), "生活化温和科普"),
)


def recommend_style(topic: str, title: str = "") -> dict[str, Any]:
    text = f"{title} {topic}".casefold()
    scored: list[dict[str, Any]] = []
    for style_id, keywords, reason in STYLE_RECOMMENDATION_RULES:
        matched = [keyword for keyword in keywords if keyword.casefold() in text]
        if matched:
            scored.append({"style_id": style_id, "score": len(matched), "matched": matched, "reason": reason})
    order = {rule[0]: index for index, rule in enumerate(STYLE_RECOMMENDATION_RULES)}
    scored.sort(key=lambda item: (-int(item["score"]), order[str(item["style_id"])]))
    winner = scored[0] if scored else {
        "style_id": "warm-pencil", "score": 0, "matched": [], "reason": "未命中特定题材，采用通用温和科普风格"
    }
    style = resolve_style(str(winner["style_id"]))
    return {
        "mode": "auto",
        "style_id": style["id"],
        "style_name": style["name"],
        "reason": winner["reason"],
        "matched_keywords": winner["matched"],
        "candidates": [item["style_id"] for item in scored[:3]] or ["warm-pencil", "minimal-whiteboard", "comic-ink"],
        "requires_confirmation": True,
    }


def copy_artifact(source: str | Path, target: Path) -> Path:
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise WorkflowError(f"输入文件不存在：{src}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if src != target.resolve():
        shutil.copy2(src, target)
    return target


def verify_artifacts_current(root: Path, state: dict[str, Any]) -> None:
    stale: list[str] = []
    for name, record in state.get("artifacts", {}).items():
        path = root / record.get("path", "")
        if not path.is_file() or digest(path) != record.get("sha256"):
            stale.append(name)
    for scene_id, record in state.get("boards", {}).items():
        for key, hash_key in (("image", "image_sha256"), ("annotation", "annotation_sha256")):
            path = root / record.get(key, "")
            if not path.is_file() or digest(path) != record.get(hash_key):
                stale.append(f"{scene_id}.{key}")
    if stale:
        raise WorkflowError("这些已登记产物已变化，需要重新登记：" + ", ".join(stale))


def _suggest_storyboard_composition(scene: dict[str, Any], index: int, previous: str) -> str:
    """Fill only an omitted draft composition; the user still approves the compiled plan."""

    text = " ".join((str(scene.get("title", "")), str(scene.get("narration", ""))))
    rules = (
        ("before-after", ("对比", "区别", "前后", "原来", "后来")),
        ("causal-chain", ("因为", "导致", "所以", "结果", "原因")),
        ("timeline", ("时间", "阶段", "第一", "第二", "最后")),
        ("vertical-layers", ("步骤", "流程", "先", "然后", "接着")),
        ("character-action", ("人物", "动作", "你", "他", "她", "人们")),
    )
    semantic_candidates = [name for name, terms in rules if any(term in text for term in terms)]
    for candidate in semantic_candidates:
        if candidate != previous:
            return candidate
    fallbacks = ["center-spoke", "character-action", "causal-chain", "before-after", "timeline", "vertical-layers"]
    offset = index % len(fallbacks)
    for candidate in fallbacks[offset:] + fallbacks[:offset]:
        if candidate != previous:
            return candidate
    return "center-spoke"


def compile_storyboard_draft(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize safe draft fields before validation without inventing semantic timing."""

    compiled = copy.deepcopy(data)
    scenes = compiled.get("scenes")
    changes: list[str] = []
    if not isinstance(scenes, list):
        return compiled, changes
    previous_composition = ""
    for scene_index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("id") or f"scene-{scene_index + 1:02d}").strip()
        if not scene.get("id"):
            scene["id"] = scene_id
            changes.append(f"{scene_id}: generated scene id")
        composition = str(scene.get("composition") or "").strip()
        if not composition:
            composition = _suggest_storyboard_composition(scene, scene_index, previous_composition)
            scene["composition"] = composition
            changes.append(f"{scene_id}: selected composition {composition}")
        previous_composition = composition
        elements = scene.get("elements")
        if not isinstance(elements, list):
            continue
        normalized_elements: list[Any] = []
        for element_index, raw in enumerate(elements, 1):
            element = dict(raw) if isinstance(raw, dict) else {"label": str(raw)}
            if not str(element.get("id") or "").strip():
                element["id"] = f"{scene_id}-e{element_index:02d}"
                changes.append(f"{scene_id}: generated object id {element['id']}")
            if not str(element.get("trigger_text") or "").strip():
                for alias in ("triggerText", "narrationEvidence"):
                    value = str(element.get(alias) or "").strip()
                    if value:
                        element["trigger_text"] = value
                        changes.append(f"{scene_id}/{element['id']}: normalized {alias} to trigger_text")
                        break
            normalized_elements.append(element)
        scene["elements"] = normalized_elements
    return compiled, changes


TITLE_CARD_POSITIONS = {"top-left"}
TITLE_CARD_STYLES = {"outlined-label-v1"}
DEFAULT_TITLE_CARD_ACCENTS = ["#356AE6", "#43A85B"]


def compile_scene_title_cards(
    project: dict[str, Any], scenes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Normalize approved scene cards without inventing their semantic text."""

    profile = project.get("title_card_profile")
    profile = dict(profile) if isinstance(profile, dict) else {}
    enabled = bool(profile.get("enabled", False))
    required = bool(profile.get("required_per_scene", False))
    has_explicit_card = any(isinstance(scene.get("title_card"), dict) for scene in scenes)
    if not enabled and not required and not has_explicit_card:
        return copy.deepcopy(scenes)

    position = str(profile.get("position") or "top-left").strip()
    style = str(profile.get("style") or "outlined-label-v1").strip()
    max_chars = int(profile.get("max_text_chars") or 18)
    palette = profile.get("accent_palette")
    if not isinstance(palette, list) or not palette:
        palette = list(DEFAULT_TITLE_CARD_ACCENTS)
    palette = [str(value).strip() for value in palette if str(value).strip()]
    if not palette:
        palette = list(DEFAULT_TITLE_CARD_ACCENTS)

    issues: list[str] = []
    compiled = copy.deepcopy(scenes)
    for scene_index, scene in enumerate(compiled):
        scene_id = str(scene.get("id") or f"scene-{scene_index + 1:02d}")
        raw = scene.get("title_card")
        card = dict(raw) if isinstance(raw, dict) else {}
        text = " ".join(str(card.get("text") or "").split())
        if not text:
            if required:
                issues.append(f"{scene_id} 缺少 title_card.text；文字卡片必须在分镜阶段设计")
            scene.pop("title_card", None)
            continue
        card_position = str(card.get("position") or position).strip()
        card_style = str(card.get("style") or style).strip()
        accent = str(card.get("accent") or palette[scene_index % len(palette)]).strip()
        if card_position not in TITLE_CARD_POSITIONS:
            issues.append(f"{scene_id} 的文字卡片位置不受支持：{card_position}")
        if card_style not in TITLE_CARD_STYLES:
            issues.append(f"{scene_id} 的文字卡片样式不受支持：{card_style}")
        if max_chars > 0 and len(text) > max_chars:
            issues.append(f"{scene_id} 的文字卡片超过 {max_chars} 个字符：{text}")
        scene["title_card"] = {
            "text": text,
            "position": card_position,
            "style": card_style,
            "accent": accent,
        }
    if issues:
        detail = "\n".join(f"{index}. {issue}" for index, issue in enumerate(issues, 1))
        raise WorkflowError(f"分镜文字卡片有 {len(issues)} 个问题，请一次修正：\n{detail}")
    return compiled


def validate_storyboard(
    data: dict[str, Any],
    require_v2: bool = False,
    require_v3: bool = False,
) -> list[dict[str, Any]]:
    """Validate the complete storyboard and report every independent problem at once."""

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise WorkflowError("storyboard.json 必须包含非空 scenes 数组")
    issues: list[str] = []
    seen_scenes: set[str] = set()
    previous_end = 0
    previous_composition = ""
    declared_characters = {
        str(item.get("id", "")).strip()
        for item in data.get("characters", [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    for scene_index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            issues.append(f"第 {scene_index} 幕必须是对象")
            continue
        raw_scene_id = str(scene.get("id") or f"scene-{scene_index:02d}")
        try:
            scene_id = validate_scene_id(raw_scene_id)
        except WorkflowError as exc:
            issues.append(str(exc))
            scene_id = raw_scene_id
        if scene_id in seen_scenes:
            issues.append(f"场景 id 重复：{scene_id!r}")
        seen_scenes.add(scene_id)
        narration = str(scene.get("narration", "")).strip()
        if not narration:
            issues.append(f"{scene_id} 缺少 narration")
        start_ms, end_ms = scene.get("start_ms"), scene.get("end_ms")
        timing_valid = (
            isinstance(start_ms, int) and not isinstance(start_ms, bool)
            and isinstance(end_ms, int) and not isinstance(end_ms, bool)
            and start_ms >= 0 and end_ms > start_ms
        )
        if not timing_valid:
            issues.append(f"{scene_id} 的 start_ms/end_ms 无效")
        elif start_ms < previous_end:
            issues.append(f"{scene_id} 与前一幕时间重叠")
        if timing_valid:
            previous_end = end_ms
        elements = scene.get("elements")
        if not isinstance(elements, list) or not 1 <= len(elements) <= 6:
            issues.append(f"{scene_id} 必须包含 1–6 个按语义自适应的可画元素")
            elements = []
        seen_objects: set[str] = set()
        for element_index, element in enumerate(elements, 1):
            if not isinstance(element, dict):
                issues.append(f"{scene_id} 的第 {element_index} 个元素必须是对象")
                continue
            label = str(element.get("label") or "").strip()
            object_id = str(element.get("id") or "").strip()
            trigger = str(element.get("trigger_text") or "").strip()
            if not label:
                issues.append(f"{scene_id} 包含空元素")
            if not object_id:
                issues.append(f"{scene_id} 的第 {element_index} 个元素缺少稳定 id")
            elif object_id in seen_objects:
                issues.append(f"{scene_id} 内语义对象 id 重复：{object_id}")
            else:
                seen_objects.add(object_id)
            if require_v3 and not trigger and not scene.get("visual_beats"):
                issues.append(f"{scene_id}/{object_id or element_index} 缺少 trigger_text")
            label_text = label.casefold()
            if any(token in label_text for token in CONNECTOR_LABEL_TOKENS):
                if require_v3:
                    issues.append(f"{scene_id} 的新画面不能把箭头、关系线或分隔线作为绘制对象：{label}")
                else:
                    relation = element.get("relation")
                    relation_type = str((relation or {}).get("type", "")).strip() if isinstance(relation, dict) else ""
                    source = str((relation or {}).get("from", "")).strip() if isinstance(relation, dict) else ""
                    target = str((relation or {}).get("to", "")).strip() if isinstance(relation, dict) else ""
                    evidence = str((relation or {}).get("narrationEvidence", "")).strip() if isinstance(relation, dict) else ""
                    if relation_type not in CONNECTOR_RELATION_TYPES or not source or not target or not evidence:
                        issues.append(f"{scene_id} 的旧连接符元素“{label}”缺少完整 relation")
                    elif evidence not in narration:
                        issues.append(f"{scene_id} 的旧连接符元素“{label}”没有可回指口播的依据")
        composition = str(scene.get("composition", "")).strip()
        if require_v2 and composition not in COMPOSITIONS:
            issues.append(f"{scene_id} 必须指定受支持的 composition：{', '.join(sorted(COMPOSITIONS))}")
        if composition and composition == previous_composition:
            issues.append(f"{scene_id} 与前一幕重复使用构图 {composition}")
        if composition:
            previous_composition = composition
        character_ids = scene.get("character_ids", [])
        if character_ids and (
            not isinstance(character_ids, list)
            or any(str(character_id) not in declared_characters for character_id in character_ids)
        ):
            issues.append(f"{scene_id} 引用了未声明的 character_ids")
    if issues:
        detail = "\n".join(f"{index}. {issue}" for index, issue in enumerate(issues, 1))
        raise WorkflowError(f"storyboard.json 有 {len(issues)} 个问题，请一次修正：\n{detail}")
    return scenes


def validate_words(data: dict[str, Any], *, require_duration: bool = False) -> list[dict[str, Any]]:
    words = data.get("words")
    if not isinstance(words, list) or not words:
        raise WorkflowError("words.json 必须包含非空 words 数组")
    previous_start = -1
    previous_end = -1
    for word in words:
        if not isinstance(word, dict) or not str(word.get("text", "")).strip():
            raise WorkflowError("words.json 包含无效词条")
        start, end = word.get("start_ms"), word.get("end_ms")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
            raise WorkflowError("words.json 包含无效时间")
        if start < previous_start:
            raise WorkflowError("words.json 必须按时间排序")
        if start < previous_end:
            raise WorkflowError("words.json 词条时间不能与前一个词重叠")
        previous_start = start
        previous_end = end
    duration_ms = data.get("duration_ms")
    if require_duration and (not isinstance(duration_ms, int) or duration_ms <= 0):
        raise WorkflowError("words.json 必须包含正整数 duration_ms")
    if duration_ms is not None:
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            raise WorkflowError("words.json duration_ms 必须是正整数")
        if duration_ms < words[-1]["end_ms"]:
            raise WorkflowError("words.json duration_ms 早于最后一个词的结束时间")
    return words


def validate_visual_scenes_together(
    project: dict[str, Any],
    scenes: list[dict[str, Any]],
    words: list[dict[str, Any]],
    root: Path,
) -> None:
    """Report independent semantic timing problems for all scenes in one response."""

    issues: list[str] = []
    for scene in scenes:
        candidate = copy.deepcopy(project)
        candidate["scenes"] = [copy.deepcopy(scene)]
        try:
            build_visual_plan(
                candidate,
                words,
                root,
                require_complete_coverage=False,
            )
        except VisualPlanError as exc:
            issues.append(f"{scene.get('id', 'unknown-scene')}: {exc}")
    if issues:
        detail = "\n".join(f"{index}. {issue}" for index, issue in enumerate(issues, 1))
        raise WorkflowError(f"视觉语义有 {len(issues)} 个场景问题，请一次修正：\n{detail}")


def parse_caption_texts(srt_text: str) -> list[str]:
    try:
        return [str(cue["text"]) for cue in parse_srt_cues(srt_text)]
    except TextPolicyError as exc:
        raise WorkflowError(str(exc)) from exc


def normalize_annotation_geometry(data: dict[str, Any]) -> dict[str, Any]:
    """Round safe panel geometry to integer canvas pixels in place.

    Browser pointer coordinates are fractional on scaled displays.  A public
    editor must absorb that implementation detail instead of asking users to
    repair JSON.  Non-numeric schema errors are still left for validation.
    """

    canvas = data.get("canvas")
    if not isinstance(canvas, dict):
        return data
    width, height = canvas.get("width"), canvas.get("height")
    if not isinstance(width, int) or isinstance(width, bool) or not isinstance(height, int) or isinstance(height, bool):
        return data
    for element in data.get("elements", []) if isinstance(data.get("elements"), list) else []:
        if not isinstance(element, dict) or not isinstance(element.get("region"), dict):
            continue
        region = element["region"]
        values = [region.get(key) for key in ("x", "y", "width", "height")]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values):
            continue
        x = min(max(round(float(values[0])), 0), max(0, width - 1))
        y = min(max(round(float(values[1])), 0), max(0, height - 1))
        region["x"] = x
        region["y"] = y
        region["width"] = min(max(1, round(float(values[2]))), max(1, width - x))
        region["height"] = min(max(1, round(float(values[3]))), max(1, height - y))
    return data


def reconcile_annotation_semantic_ids(
    data: dict[str, Any], expected_scene: dict[str, Any] | None = None, *, migrate: bool = False
) -> bool:
    """Align annotation identity metadata with the approved storyboard.

    The workbench is an editor for geometry, order, and timing. It must not
    make users maintain the upstream semantic ID contract. When the element
    count is unchanged, preserve IDs that can be identified directly and use
    source IDs, labels, trigger text, then sequence as deterministic fallbacks
    for older drafts. A split or merge changes the approved semantic model and
    must remain a producer-side operation instead of being guessed here.
    """

    if not isinstance(expected_scene, dict):
        return False
    elements = data.get("elements")
    planned = expected_scene.get("elements")
    if not isinstance(elements, list) or not isinstance(planned, list) or not planned:
        return False

    expected_ids = [
        str(item.get("id") or item.get("sequence") or index + 1)
        if isinstance(item, dict) else str(index + 1)
        for index, item in enumerate(planned)
    ]
    if len(elements) != len(expected_ids):
        raise WorkflowError(
            f"{expected_scene.get('id', '当前场景')} 的工作台区域数量与已确认分镜不一致；"
            "语义拆分或合并由制作层同步，用户无需编辑分镜"
        )
    if len(set(expected_ids)) != len(expected_ids):
        raise WorkflowError("已确认分镜包含重复语义元素 id；请由制作层修复上游分镜")

    if not migrate:
        actual = [str(element.get("id") or "") for element in elements]
        if len(set(actual)) != len(actual) or set(actual) != set(expected_ids):
            raise WorkflowError("身份关联尚未明确；请显式 migrate-project，不按标签或顺序自动修复")
        for element in elements:
            element["sourceElementIds"] = [str(element["id"])]
        return False

    changed = False
    remaining = set(expected_ids)
    assignments: list[str | None] = [None] * len(elements)
    planned_by_id = {sid: item for sid, item in zip(expected_ids, planned)}

    def choose(index: int, candidates: list[str]) -> None:
        for candidate in candidates:
            if candidate in remaining:
                assignments[index] = candidate
                remaining.remove(candidate)
                return

    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        old_id = str(element.get("id") or "")
        source_ids = element.get("sourceElementIds")
        if not isinstance(source_ids, list):
            source_ids = []
        choose(index, [old_id] + [str(value) for value in source_ids if str(value)])

    for index, element in enumerate(elements):
        if assignments[index] is not None or not isinstance(element, dict):
            continue
        label = str(element.get("label") or "").strip()
        trigger = str(element.get("triggerText") or "").strip()
        candidates: list[str] = []
        for sid in expected_ids:
            item = planned_by_id[sid]
            if label and str(item.get("label") or "").strip() == label:
                candidates.append(sid)
            elif trigger and str(item.get("trigger_text") or "").strip() == trigger:
                candidates.append(sid)
        if len(set(candidates) & remaining) == 1:
            choose(index, candidates)

    if any(assignment is None for assignment in assignments):
        raise WorkflowError("旧标注身份不能无歧义恢复；请明确对应关系，不能按顺序补配")

    for element, semantic_id in zip(elements, assignments):
        if not isinstance(element, dict) or semantic_id is None:
            continue
        if str(element.get("id") or "") != semantic_id:
            changed = True
        if element.get("sourceElementIds") != [semantic_id]:
            changed = True
        element["id"] = semantic_id
        element["sourceElementIds"] = [semantic_id]
    return changed


def validate_caption_semantics(script_text: str, srt_text: str) -> list[str]:
    """Enforce one-line punctuation-free captions; semantic cuts are warnings."""

    try:
        cues, _warnings = validate_caption_contract(script_text, srt_text)
    except TextPolicyError as exc:
        raise WorkflowError(f"字幕 QA 未通过：{exc}") from exc
    return [str(cue["text"]) for cue in cues]


def validate_annotation(data: dict[str, Any], expected_scene: dict[str, Any] | None = None) -> None:
    canvas = data.get("canvas")
    if not isinstance(canvas, dict):
        raise WorkflowError("标注缺少 canvas")
    width, height = canvas.get("width"), canvas.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise WorkflowError("canvas.width/height 必须是正整数")
    duration = data.get("sceneDurationMs")
    if not isinstance(duration, int) or duration <= 0:
        raise WorkflowError("sceneDurationMs 必须是正整数")
    if expected_scene:
        expected_duration = expected_scene["end_ms"] - expected_scene["start_ms"]
        if abs(duration - expected_duration) > 250:
            raise WorkflowError(
                f"{expected_scene['id']} 标注时长 {duration}ms 与配音场景 {expected_duration}ms 不一致"
            )
    drawing_plan = data.get("drawingPlan")
    if drawing_plan is not None:
        if not isinstance(drawing_plan, dict):
            raise WorkflowError("drawingPlan 必须是对象")
        if drawing_plan.get("mode", "layered") not in {"layered", "legacy"}:
            raise WorkflowError("drawingPlan.mode 只能是 layered 或 legacy")
        if drawing_plan.get("strokePlanner", "semantic-v2") not in {"semantic-v2", "reading-bands-v1"}:
            raise WorkflowError("drawingPlan.strokePlanner 只能是 semantic-v2 或 reading-bands-v1")
        if drawing_plan.get("colorSchedule", "object-progressive-v1") not in {
            "object-progressive-v1", "scene-final-v1"
        }:
            raise WorkflowError("drawingPlan.colorSchedule 只能是 object-progressive-v1 或 scene-final-v1")
        if drawing_plan.get("pixelOwnership", PIXEL_POLICY) not in {
            "semantic-masks-v1", "semantic-exclusive-v2", "semantic-exclusive-v1", "exclusive-nearest-v1", "later-sequence-v1"
        }:
            raise WorkflowError(
                "drawingPlan.pixelOwnership 必须使用 semantic-masks-v1；旧名称仅作字段兼容"
            )
        reserve = drawing_plan.get("colorReserveRatio", 0.32)
        if not isinstance(reserve, (int, float)) or not 0.18 <= float(reserve) <= 0.45:
            raise WorkflowError("drawingPlan.colorReserveRatio 必须在 0.18–0.45 之间")
        minimum_color = drawing_plan.get("minimumColorMs", 900)
        if not isinstance(minimum_color, int) or minimum_color < 240:
            raise WorkflowError("drawingPlan.minimumColorMs 必须是不小于 240 的整数")
    elements = data.get("elements")
    if not isinstance(elements, list) or not elements:
        raise WorkflowError("标注必须包含 elements")
    if data.get("status") == "draft":
        raise WorkflowError("标注仍为 draft；请实际查看图片并完成区域和绘制时序")
    first_reveal = elements[0].get("reveal") if isinstance(elements[0], dict) else None
    if isinstance(first_reveal, dict):
        first_start = first_reveal.get("startMs")
        if isinstance(first_start, int) and first_start > OPENING_ANCHOR_MAX_MS:
            raise WorkflowError(
                f"首个可见元素必须在幕开始后 {OPENING_ANCHOR_MAX_MS}ms 内起笔；"
                f"当前为 {first_start}ms"
            )
    ids = [str(e.get("id") or "") if isinstance(e, dict) else "" for e in elements]
    if any(not key for key in ids) or len(ids) != len(set(ids)):
        raise WorkflowError("语义元素 id 必须非空且唯一，从分镜到标注保持不变")
    planned = (expected_scene or {}).get("elements") or []
    expected_ids = {str(item.get("id") or item.get("sequence") or index) if isinstance(item, dict) else str(index)
                    for index, item in enumerate(planned, 1)}
    if expected_ids and set(ids) != expected_ids:
        raise WorkflowError(
            "分镜与标注语义结构不一致；请由制作层同步后重新打开工作台，用户无需编辑分镜"
        )
    for element in elements:
        if element.get("sourceElementIds", [str(element["id"])]) != [str(element["id"])]:
            raise WorkflowError("sourceElementIds 必须只引用当前同名分镜元素；标注阶段不能重新分组")
    sequences: list[int] = []
    previous_end = 0
    for element in elements:
        if not isinstance(element, dict):
            raise WorkflowError("标注元素必须是对象")
        sequence = element.get("sequence")
        if not isinstance(sequence, int):
            raise WorkflowError("标注 sequence 必须是整数")
        sequences.append(sequence)
        region = element.get("region")
        if not isinstance(region, dict):
            raise WorkflowError("标注元素缺少 region")
        x, y, w, h = (region.get(k) for k in ("x", "y", "width", "height"))
        if not all(isinstance(v, int) for v in (x, y, w, h)) or x < 0 or y < 0 or w <= 0 or h <= 0:
            raise WorkflowError("region 必须使用画布内的整数像素")
        if x + w > width or y + h > height:
            raise WorkflowError("region 超出画布")
        reveal = element.get("reveal")
        if not isinstance(reveal, dict):
            raise WorkflowError("标注元素缺少 reveal")
        start, length = reveal.get("startMs"), reveal.get("durationMs")
        if not isinstance(start, int) or not isinstance(length, int) or start < 0 or length <= 0:
            raise WorkflowError("reveal.startMs/durationMs 无效")
        if start < previous_end:
            raise WorkflowError("同一支笔的区域时序不得重叠")
        if start + length > duration:
            raise WorkflowError("区域绘制时序超过 sceneDurationMs")
        previous_end = start + length
    if sorted(sequences) != list(range(1, len(elements) + 1)):
        raise WorkflowError("sequence 必须从 1 连续编号")


def validate_panel_storyboard(
    candidate: dict[str, Any], current: dict[str, Any], project: dict[str, Any]
) -> None:
    """Validate the producer-generated storyboard delta from the workbench.

    The panel never exposes storyboard editing. When a user adds or removes
    a visual region, the private production layer emits this file with only
    the scene element list changed; narration and the audio-derived scene
    timeline remain immutable.
    """

    # Editable element lists may be empty or semantically incomplete.
    current_scenes = current.get("scenes", [])
    candidate_scenes = candidate.get("scenes", [])
    if [scene.get("id") for scene in candidate_scenes] != [scene.get("id") for scene in current_scenes]:
        raise WorkflowError("工作台只能同步场景内视觉元素，不能新增、删除或重排口播场景")
    immutable_keys = (
        "title", "narration", "start_ms", "end_ms", "composition", "character_ids", "title_card"
    )
    for old_scene, new_scene in zip(current_scenes, candidate_scenes):
        for key in immutable_keys:
            if new_scene.get(key) != old_scene.get(key):
                raise WorkflowError(f"{new_scene.get('id', '当前场景')} 的 {key} 属于口播时间轴，不能由工作台修改")
        ids: list[str] = []
        for element in new_scene.get("elements", []):
            if not isinstance(element, dict):
                raise WorkflowError(f"{new_scene.get('id', '当前场景')} 的分镜元素必须是对象")
            semantic_id = str(element.get("id") or "").strip()
            if not semantic_id:
                raise WorkflowError(f"{new_scene.get('id', '当前场景')} 存在没有语义 id 的分镜元素")
            ids.append(semantic_id)
        if len(ids) != len(set(ids)):
            raise WorkflowError(f"{new_scene.get('id', '当前场景')} 的分镜语义元素 id 重复")


def storyboard_semantic_diff(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Describe semantic edits while ignoring draw-order-only changes.

    Array position and sequence affect execution order, so they require the
    board/order confirmation only. Adding, removing, or changing an object's
    meaning or narration binding returns to the script/voice/storyboard gate.
    """

    def semantic_scenes(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for scene in value.get("scenes", []):
            if not isinstance(scene, dict):
                continue
            scene_id = str(scene.get("id") or "")
            objects: dict[str, Any] = {}
            for element in scene.get("elements", []):
                if not isinstance(element, dict):
                    continue
                object_id = str(element.get("id") or "")
                objects[object_id] = {
                    key: item
                    for key, item in element.items()
                    if key not in {"sequence", "order"}
                }
            result[scene_id] = objects
        return result

    before = semantic_scenes(current)
    after = semantic_scenes(candidate)
    scene_rows: list[dict[str, Any]] = []
    for scene_id in sorted(set(before) | set(after)):
        before_objects = before.get(scene_id, {})
        after_objects = after.get(scene_id, {})
        added = sorted(set(after_objects) - set(before_objects))
        removed = sorted(set(before_objects) - set(after_objects))
        changed = sorted(
            object_id
            for object_id in set(before_objects) & set(after_objects)
            if before_objects[object_id] != after_objects[object_id]
        )
        if added or removed or changed:
            scene_rows.append({
                "scene_id": scene_id,
                "before_count": len(before_objects),
                "after_count": len(after_objects),
                "added_ids": added,
                "removed_ids": removed,
                "changed_ids": changed,
            })
    return {
        "changed": bool(scene_rows),
        "before_count": sum(len(objects) for objects in before.values()),
        "after_count": sum(len(objects) for objects in after.values()),
        "scenes": scene_rows,
    }


def storyboard_semantics_changed(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Return whether semantics changed; kept as a small public compatibility helper."""

    return bool(storyboard_semantic_diff(current, candidate)["changed"])

def audio_duration_ms(
    path: Path,
    python_executable: Path | None = None,
    ffprobe_executable: str | Path | None = None,
) -> int | None:
    if path.suffix.lower() == ".wav" and path.is_file():
        try:
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                if rate > 0:
                    return round(handle.getnframes() / float(rate) * 1000)
        except (wave.Error, OSError):
            pass
    ffprobe = str(ffprobe_executable) if ffprobe_executable else str(
        (local_runtime_info().get("ffprobe") or {}).get("path") or ""
    )
    if ffprobe:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            try:
                return round(float(result.stdout.strip()) * 1000)
            except ValueError:
                pass
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                return round(handle.getnframes() / handle.getframerate() * 1000)
        except (wave.Error, OSError):
            pass
    try:
        import av

        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            sample_rate = int(stream.codec_context.sample_rate or 0)
            samples = sum(frame.samples for frame in container.decode(stream))
            if sample_rate:
                return round(samples / sample_rate * 1000)
    except Exception:
        pass
    if python_executable and Path(python_executable).is_file():
        code = (
            "import av,sys; c=av.open(sys.argv[1]); s=c.streams.audio[0]; "
            "rate=int(s.codec_context.sample_rate or 0); samples=sum(f.samples for f in c.decode(s)); "
            "c.close(); print(round(samples/rate*1000) if rate else '')"
        )
        result = subprocess.run(
            [str(python_executable), "-c", code, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            try:
                return int(result.stdout.strip())
            except ValueError:
                pass
    return None


def duration_plan(
    scenes: list[dict[str, Any]],
    words: list[dict[str, Any]],
    measured_audio_ms: int | None,
) -> dict[str, int]:
    timeline_end_ms = max(scene["end_ms"] for scene in scenes)
    words_end_ms = max(word["end_ms"] for word in words)
    target_duration_ms = max(timeline_end_ms, words_end_ms, measured_audio_ms or 0)
    return {
        "timeline_end_ms": timeline_end_ms,
        "words_end_ms": words_end_ms,
        "audio_duration_ms": measured_audio_ms or words_end_ms,
        "target_duration_ms": target_duration_ms,
        "tail_extension_ms": max(0, target_duration_ms - timeline_end_ms),
    }


def frame_render_plan(
    scenes: list[dict[str, Any]],
    target_duration_ms: int,
    fps: int,
) -> dict[str, Any]:
    """Map absolute scene times to an exact cumulative video frame budget."""
    if fps <= 0:
        raise WorkflowError("fps 必须是正整数")
    if not scenes:
        raise WorkflowError("没有可渲染场景")
    target_frames = max(1, math.ceil(target_duration_ms * fps / 1000))
    quantized_target_ms = round(target_frames * 1000 / fps)
    previous_end_frame = 0
    planned: list[dict[str, int | str]] = []
    for index, scene in enumerate(scenes):
        start_frame = math.ceil(int(scene["start_ms"]) * fps / 1000)
        render_end_ms = int(scenes[index + 1]["start_ms"]) if index + 1 < len(scenes) else target_duration_ms
        if render_end_ms < int(scene["end_ms"]):
            raise WorkflowError(f"{scene['id']} 的口播结束超出渲染窗口")
        end_frame = math.ceil(render_end_ms * fps / 1000)
        if end_frame <= previous_end_frame:
            raise WorkflowError(f"{scene['id']} 的绝对帧区间无效")
        lead_frames = max(0, start_frame - previous_end_frame)
        scene_frames = end_frame - previous_end_frame
        if lead_frames >= scene_frames:
            raise WorkflowError(f"{scene['id']} 没有剩余内容帧")
        lead_ms = round(lead_frames * 1000 / fps)
        content_ms = int(scene["end_ms"]) - int(scene["start_ms"])
        tail_ms = max(0, round(end_frame * 1000 / fps) - int(scene["end_ms"]))
        planned.append({
            "scene_id": str(scene["id"]),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "lead_frames": lead_frames,
            "target_frames": scene_frames,
            "total_ms": lead_ms + content_ms + tail_ms,
        })
        previous_end_frame = end_frame
    if previous_end_frame != target_frames:
        raise WorkflowError(
            f"帧计划总数 {previous_end_frame} 与目标 {target_frames} 不一致；"
            "请检查最后一幕与音频时间轴"
        )
    return {
        "fps": fps,
        "target_frames": target_frames,
        "target_duration_ms": quantized_target_ms,
        "scenes": planned,
    }


def init_project(root: Path, title: str, topic: str, duration_sec: int, style: str) -> None:
    if (root / "project.json").exists():
        raise WorkflowError(f"项目已经存在：{root}")
    for folder in ("source", "script", "audio", "boards", "annotations", "previews", "renders", "deliverables"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    if style.strip().casefold() == "auto":
        style_selection = recommend_style(topic, title)
        style_profile = resolve_style(style_selection["style_id"])
    else:
        style_profile = resolve_style(style)
        style_selection = {
            "mode": "user",
            "style_id": style_profile["id"],
            "style_name": style_profile["name"],
            "reason": "用户显式选择",
            "matched_keywords": [],
            "candidates": [style_profile["id"]],
            "requires_confirmation": False,
        }
    project = {
        "version": VERSION,
        "title": title,
        "topic": topic,
        "target_duration_sec": duration_sec,
        "aspect_ratio": "16:9",
        "style": style_profile["name"],
        "style_id": style_profile["id"],
        "renderer_profile": {
            **style_profile["renderer"],
            "pixel_ownership": PIXEL_POLICY,
        },
        "style_selection": style_selection,
        "voice_selection": {
            "provider": "edge",
            "voice": "zh-CN-XiaoxiaoNeural",
            "mode": "default",
            "alternative_auditions_generated": False,
        },
        "sfx_profile": {
            "enabled": True,
            "preset": "classic-light",
            "mode": "deterministic",
        },
        "created_at": now(),
        "scenes": [],
    }
    try:
        project = apply_local_defaults(project, Path(__file__).resolve().parents[1])
    except LocalDefaultsError as exc:
        raise WorkflowError(str(exc)) from exc
    state = {
        "version": VERSION,
        "stage": "prepare-script",
        "approvals": {
            "script_style": {"approved": False, "at": None},
            "script_voice": {"approved": False, "at": None},
            "boards": {"approved": False, "at": None},
        },
        "artifacts": {},
        "boards": {},
        "final_current": False,
        "qa": {},
        "updated_at": now(),
    }
    write_json(root / "project.json", project)
    write_json(root / "state.json", state)


def stage_script(root: Path, script: str, style: str | None = None) -> None:
    project, state = load_project(root)
    source_extract = state.get("source_extract")
    if isinstance(source_extract, dict):
        source_status = str(source_extract.get("status", ""))
        if source_status != "awaiting-script-review":
            detail = str(source_extract.get("error") or "参考素材提取尚未成功完成")
            raise WorkflowError(
                "存在未完成的参考素材提取，不能登记推断或代写稿："
                f"{detail}。请先重新运行 extract-source 并取得 extraction.json。"
            )
        quality = source_extract.get("quality")
        if not isinstance(quality, dict) or quality.get("status") != "passed":
            raise WorkflowError("参考素材转写质量尚未通过，不能登记口播稿；请重新运行 extract-source")
        required_source_files = {"text", "srt", "vtt", "cues"}
        recorded_files = source_extract.get("files")
        if not isinstance(recorded_files, dict) or not required_source_files.issubset(recorded_files):
            raise WorkflowError("参考素材提取记录不完整，不能登记口播稿；请重新运行 extract-source")
        for name in required_source_files:
            record = recorded_files.get(name)
            if not isinstance(record, dict) or not record.get("path"):
                raise WorkflowError("参考素材提取记录不完整，不能登记口播稿；请重新运行 extract-source")
            artifact = root / str(record["path"])
            if not artifact.is_file() or digest(artifact) != record.get("sha256"):
                raise WorkflowError(f"参考素材提取产物 {name} 已缺失或变化；请重新运行 extract-source")
        if source_extract.get("reference_scope") == "full":
            require_source_review(root, state)
            analysis = source_extract.get("analysis")
            if not isinstance(analysis, dict):
                raise WorkflowError("完整参考视频分析记录缺失，不能登记口播稿；请重新运行 extract-source")
            analysis_files = analysis.get("files")
            required_analysis_files = {"manifest", "markdown", "contact_sheet"}
            if not isinstance(analysis_files, dict) or not required_analysis_files.issubset(analysis_files):
                raise WorkflowError("完整参考视频分析产物不完整，不能登记口播稿；请重新运行 extract-source")
            for name in required_analysis_files:
                record = analysis_files.get(name)
                if not isinstance(record, dict) or not record.get("path"):
                    raise WorkflowError("完整参考视频分析产物不完整，不能登记口播稿；请重新运行 extract-source")
                artifact = root / str(record["path"])
                if not artifact.is_file() or digest(artifact) != record.get("sha256"):
                    raise WorkflowError(f"参考视频分析产物 {name} 已缺失或变化；请重新运行 extract-source")
            keyframes = analysis.get("keyframes")
            if not isinstance(keyframes, list) or not keyframes:
                raise WorkflowError("完整参考视频分析没有关键帧，不能登记口播稿；请重新运行 extract-source")
            for record in keyframes:
                if not isinstance(record, dict) or not record.get("path"):
                    raise WorkflowError("完整参考视频分析关键帧记录不完整；请重新运行 extract-source")
                artifact = root / str(record["path"])
                if not artifact.is_file() or digest(artifact) != record.get("sha256"):
                    raise WorkflowError("参考视频分析关键帧已缺失或变化；请重新运行 extract-source")
    script_source = Path(script).expanduser().resolve()
    if not script_source.is_file():
        raise WorkflowError(f"口播稿不存在：{script_source}")
    script_text = read_narration_text(script_source)
    if not script_text:
        raise WorkflowError("口播稿为空或只包含 Markdown 结构")
    if style:
        style_profile = resolve_style(style)
        project["style"] = style_profile["name"]
        project["style_id"] = style_profile["id"]
        project["renderer_profile"] = {
            **style_profile["renderer"],
            "pixel_ownership": PIXEL_POLICY,
        }
        project["style_selection"] = {
            "mode": "user",
            "style_id": style_profile["id"],
            "style_name": style_profile["name"],
            "reason": "用户在第一道确认前显式选择",
            "matched_keywords": [],
            "candidates": [style_profile["id"]],
            "requires_confirmation": True,
        }
    narration_target = root / "script" / "narration.md"
    narration_target.parent.mkdir(parents=True, exist_ok=True)
    narration_target.write_text(script_text + "\n", encoding="utf-8")
    state["artifacts"] = {
        "script": {
            "path": "script/narration.md",
            "sha256": digest(narration_target),
        }
    }
    state["approvals"] = {
        "script_style": {"approved": False, "at": None},
        "script_voice": {"approved": False, "at": None},
        "boards": {"approved": False, "at": None},
    }
    mark_boards_stale(state, "script-or-style-changed")
    state["render_cache"] = {}
    state["final_current"] = False
    state["final_sha256"] = None
    state["qa"] = {}
    refresh_stage(root, project, state)
    write_json(root / "project.json", project)
    write_json(root / "state.json", state)


def require_script_style_approval(root: Path, display_script: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    project, state = load_project(root)
    approval = state["approvals"]["script_style"]
    if approval.get("approved") is not True:
        raise WorkflowError("第一次确认尚未完成：请先登记口播稿和风格，再由用户确认")
    record = state.get("artifacts", {}).get("script")
    if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
        raise WorkflowError("第一次确认缺少锁定口播稿记录")
    locked = root / str(record["path"])
    if not locked.is_file() or digest(locked) != record["sha256"]:
        raise WorkflowError("第一次确认后的口播稿已变化，请重新登记并确认")
    if display_script is not None:
        candidate_text = read_narration_text(display_script)
        locked_text = read_narration_text(locked)
        if candidate_text != locked_text:
            raise WorkflowError("本次展示文案与第一次确认锁定的口播稿不一致")
    return project, state


def extract_source(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Extract a reviewable transcript package without locking the narration."""

    root = canonical_project(root)
    try:
        require_public_source(str(args.input))
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
    project, state = load_project(root)
    skill_root = renderer_root()
    extractor = skill_root / "scripts" / "extract_subtitles.py"
    if not extractor.is_file():
        raise WorkflowError(f"底层 Skill 缺少提取脚本：{extractor}")
    reference_scope = str(getattr(args, "reference_scope", "transcript"))
    if reference_scope not in {"transcript", "full"}:
        raise WorkflowError("参考范围必须是 transcript 或 full")
    analyzer = skill_root / "scripts" / "analyze_reference_video.py"
    if reference_scope == "full" and not analyzer.is_file():
        raise WorkflowError(f"底层 Skill 缺少完整参考视频分析脚本：{analyzer}")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = root / "source" / "extracted" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "extraction.log"
    parsed = urllib.parse.urlsplit(str(args.input))
    if parsed.scheme in {"http", "https"}:
        input_kind = "url"
        input_display = source_label(str(args.input))
    else:
        input_kind = "local-media"
        input_display = Path(args.input).expanduser().name
    state["source_extract"] = {
        "at": now(),
        "input_kind": input_kind,
        "input": input_display,
        "reference_scope": reference_scope,
        "status": "preparing",
        "output_dir": str(out_dir.relative_to(root)).replace("\\", "/"),
        "log": str(log_path.relative_to(root)).replace("\\", "/"),
    }
    state["updated_at"] = now()
    write_json(root / "state.json", state)
    print("EXTRACTION_STATUS=preparing-environment", flush=True)
    try:
        python = prepare_extraction_environment()
    except (WorkflowError, OSError) as exc:
        state["source_extract"].update({"status": "failed", "error": str(exc), "failed_at": now()})
        state["updated_at"] = now()
        write_json(root / "state.json", state)
        raise
    command = [
        str(python),
        str(extractor),
        str(args.input),
        "--out-dir",
        str(out_dir),
        "--model",
        str(args.model),
        "--device",
        str(args.device),
        "--source-language",
        str(getattr(args, "source_language", "auto")),
    ]
    if args.force_asr:
        command.append("--force-asr")
    if args.asr_python:
        command.extend(["--asr-python", str(args.asr_python)])
    state["source_extract"]["status"] = "running"
    state["updated_at"] = now()
    write_json(root / "state.json", state)
    print(f"EXTRACTION_STATUS=running LOG={log_path}", flush=True)
    tail: list[str] = []
    try:
        with log_path.open("w", encoding="utf-8") as log_stream:
            process = subprocess.Popen(
                command,
                cwd=skill_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if process.stdout is None:
                raise WorkflowError("参考素材提取器没有可读输出流")
            for raw_line in process.stdout:
                raw_line = redact_source_log(raw_line)
                line = raw_line.rstrip("\r\n")
                print(line, flush=True)
                log_stream.write(raw_line)
                log_stream.flush()
                if line:
                    tail.append(line)
                    tail = tail[-40:]
            return_code = process.wait()
    except (OSError, WorkflowError) as exc:
        state["source_extract"].update({"status": "failed", "error": str(exc), "failed_at": now()})
        state["updated_at"] = now()
        write_json(root / "state.json", state)
        raise WorkflowError(f"参考素材提取无法启动：{exc}") from exc
    if return_code != 0:
        detail = tail[-1] if tail else f"提取器退出码 {return_code}；详见 {log_path}"
        state["source_extract"].update({"status": "failed", "error": detail, "failed_at": now()})
        state["updated_at"] = now()
        write_json(root / "state.json", state)
        raise WorkflowError(f"参考素材提取失败：{detail}")
    manifest_path = out_dir / "extraction.json"
    try:
        manifest = read_json(manifest_path)
    except WorkflowError as exc:
        state["source_extract"].update({"status": "failed", "error": str(exc), "failed_at": now()})
        state["updated_at"] = now()
        write_json(root / "state.json", state)
        raise
    quality = manifest.get("quality")
    if not isinstance(quality, dict) or quality.get("status") != "passed":
        reasons = quality.get("errors", []) if isinstance(quality, dict) else []
        detail = "转写质量检查未通过"
        if reasons:
            detail += "：" + "；".join(str(reason) for reason in reasons)
        state["source_extract"].update({"status": "failed", "error": detail, "failed_at": now()})
        state["updated_at"] = now()
        write_json(root / "state.json", state)
        raise WorkflowError(detail)
    files: dict[str, dict[str, str]] = {}
    for name, value in manifest.get("outputs", {}).items():
        path = Path(value).resolve()
        if path.is_file() and root in path.parents:
            files[name] = {
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "sha256": digest(path),
            }
    words_value = manifest.get("words_path")
    if words_value:
        words_path = Path(words_value).resolve()
        if words_path.is_file() and root in words_path.parents:
            files["words"] = {
                "path": str(words_path.relative_to(root)).replace("\\", "/"),
                "sha256": digest(words_path),
            }
    missing = sorted({"text", "srt", "vtt", "cues"} - set(files))
    audio_value = manifest.get("audio_path")
    audio_path = Path(audio_value).resolve() if audio_value else None
    if audio_path is None or not audio_path.is_file() or root not in audio_path.parents:
        missing.append("audio")
    else:
        files["audio"] = {
            "path": str(audio_path.relative_to(root)).replace("\\", "/"),
            "sha256": digest(audio_path),
        }
    if missing:
        detail = "提取器未生成完整产物：" + ", ".join(missing)
        state["source_extract"].update({"status": "failed", "error": detail, "failed_at": now()})
        state["updated_at"] = now()
        write_json(root / "state.json", state)
        raise WorkflowError(detail)
    analysis_record: dict[str, Any] | None = None
    if reference_scope == "full":
        analysis_dir = out_dir / "analysis"
        analysis_command = [
            str(python),
            str(analyzer),
            str(args.input),
            "--out-dir",
            str(analysis_dir),
            "--transcript-manifest",
            str(manifest_path),
            "--max-keyframes",
            str(getattr(args, "max_keyframes", 20)),
        ]
        state["source_extract"].update({"status": "analyzing-video", "analysis_output_dir": str(analysis_dir.relative_to(root)).replace("\\", "/")})
        state["updated_at"] = now()
        write_json(root / "state.json", state)
        print(f"REFERENCE_ANALYSIS_STATUS=running LOG={log_path}", flush=True)
        analysis_tail: list[str] = []
        try:
            with log_path.open("a", encoding="utf-8") as log_stream:
                log_stream.write("\n=== full reference video analysis ===\n")
                process = subprocess.Popen(
                    analysis_command,
                    cwd=skill_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                if process.stdout is None:
                    raise WorkflowError("完整参考视频分析器没有可读输出流")
                for raw_line in process.stdout:
                    raw_line = redact_source_log(raw_line)
                    line = raw_line.rstrip("\r\n")
                    print(line, flush=True)
                    log_stream.write(raw_line)
                    log_stream.flush()
                    if line:
                        analysis_tail.append(line)
                        analysis_tail = analysis_tail[-40:]
                analysis_return_code = process.wait()
        except (OSError, WorkflowError) as exc:
            state["source_extract"].update({"status": "failed", "error": str(exc), "failed_at": now()})
            state["updated_at"] = now()
            write_json(root / "state.json", state)
            raise WorkflowError(f"完整参考视频分析无法启动：{exc}") from exc
        if analysis_return_code != 0:
            detail = analysis_tail[-1] if analysis_tail else f"分析器退出码 {analysis_return_code}；详见 {log_path}"
            state["source_extract"].update({"status": "failed", "error": detail, "failed_at": now()})
            state["updated_at"] = now()
            write_json(root / "state.json", state)
            raise WorkflowError(f"完整参考视频分析失败：{detail}")
        analysis_manifest_path = analysis_dir / "source-analysis.json"
        try:
            analysis_manifest = read_json(analysis_manifest_path)
        except WorkflowError as exc:
            state["source_extract"].update({"status": "failed", "error": str(exc), "failed_at": now()})
            state["updated_at"] = now()
            write_json(root / "state.json", state)
            raise
        analysis_files: dict[str, dict[str, str]] = {
            "manifest": {
                "path": str(analysis_manifest_path.relative_to(root)).replace("\\", "/"),
                "sha256": digest(analysis_manifest_path),
            }
        }
        for name in ("markdown", "contact_sheet"):
            value = analysis_manifest.get("outputs", {}).get(name)
            path = Path(value).resolve() if value else None
            if path is None or not path.is_file() or root not in path.parents:
                detail = f"完整参考视频分析缺少 {name}"
                state["source_extract"].update({"status": "failed", "error": detail, "failed_at": now()})
                state["updated_at"] = now()
                write_json(root / "state.json", state)
                raise WorkflowError(detail)
            analysis_files[name] = {
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "sha256": digest(path),
            }
        keyframe_records: list[dict[str, str]] = []
        for item in analysis_manifest.get("keyframes", []):
            if not isinstance(item, dict) or not item.get("path"):
                continue
            path = (analysis_dir / str(item["path"])).resolve()
            if path.is_file() and root in path.parents:
                keyframe_records.append({
                    "scene_id": str(item.get("scene_id") or ""),
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "sha256": digest(path),
                })
        if not keyframe_records:
            detail = "完整参考视频分析没有生成关键帧"
            state["source_extract"].update({"status": "failed", "error": detail, "failed_at": now()})
            state["updated_at"] = now()
            write_json(root / "state.json", state)
            raise WorkflowError(detail)
        pacing = analysis_manifest.get("structure", {}).get("pacing", {})
        analysis_record = {
            "status": "awaiting-codex-visual-review",
            "files": analysis_files,
            "keyframes": keyframe_records,
            "scene_count": pacing.get("scene_count"),
            "cuts_per_minute": pacing.get("cuts_per_minute"),
            "downloaded_full_video": analysis_manifest.get("resource_notice", {}).get("downloaded_full_video"),
        }
    state["source_extract"] = {
        "at": now(),
        "input_kind": input_kind,
        "input": input_display,
        "reference_scope": reference_scope,
        "requested_language": manifest.get("requested_language"),
        "transcript_language": manifest.get("transcript_language"),
        "transcript_source": manifest.get("transcript_source"),
        "requested_asr_model": manifest.get("requested_asr_model"),
        "asr_model": manifest.get("asr_model"),
        "asr_model_source": manifest.get("asr_model_source"),
        "cached_asr_models": manifest.get("cached_asr_models", []),
        "quality": quality,
        "cue_count": manifest.get("cue_count"),
        "word_count": manifest.get("word_count"),
        "manifest": {
            "path": str(manifest_path.relative_to(root)).replace("\\", "/"),
            "sha256": digest(manifest_path),
        },
        "files": files,
        "status": "awaiting-script-review",
        "output_dir": str(out_dir.relative_to(root)).replace("\\", "/"),
        "log": str(log_path.relative_to(root)).replace("\\", "/"),
    }
    if analysis_record is not None:
        state["source_extract"]["analysis"] = analysis_record
    state["updated_at"] = now()
    write_json(root / "state.json", state)
    print("EXTRACTION_STATUS=complete", flush=True)
    return {
        "project": project["title"],
        "source_extract": state["source_extract"],
        "next_action": (
            "实际查看完整参考分析的 contact sheet 与关键帧，再结合提取稿改写并锁定最终口播；不要直接复制原视频。"
            if reference_scope == "full"
            else "审核 source 提取稿，改写并锁定最终口播；不要直接把自动字幕当成正式稿。"
        ),
    }


def refresh_stage(root: Path, project: dict[str, Any], state: dict[str, Any]) -> str:
    if state.get("final_current"):
        try:
            require_render_receipt(root, state)
        except (WorkflowError, OSError):
            state.update({"final_current": False, "final_sha256": None, "qa": {}})
    artifacts = state.get("artifacts", {})
    approvals = state.setdefault("approvals", {})
    approvals.setdefault("script_style", {"approved": False, "at": None})
    approvals.setdefault("script_voice", {"approved": False, "at": None})
    approvals.setdefault("boards", {"approved": False, "at": None})
    if approvals["boards"].get("approved") and int(project.get("version", 1)) >= 3:
        try:
            require_effective_plan(root, project, state, approved=True)
        except (WorkflowError, OSError, KeyError) as exc:
            approvals["boards"] = {"approved": False, "at": None, "stale_reason": str(exc)}
            state.update({"final_current": False, "final_sha256": None, "qa": {}})
    if not artifacts.get("script"):
        stage = "prepare-script"
    elif not approvals["script_style"]["approved"]:
        stage = "await-script-style-approval"
    elif not all(artifacts.get(k) for k in ("storyboard", "audio", "words", "captions")):
        stage = "prepare-script-voice"
    elif not approvals["script_voice"]["approved"]:
        stage = "await-script-voice-approval"
    else:
        scene_ids = [scene["id"] for scene in project.get("scenes", [])]
        boards = state.get("boards", {})
        complete = bool(scene_ids) and all(
            scene_id in boards
            and (root / boards[scene_id]["image"]).is_file()
            and (root / boards[scene_id]["annotation"]).is_file()
            for scene_id in scene_ids
        )
        plan_ready = int(project.get("version", 1)) < 3
        if complete and not plan_ready:
            try:
                require_effective_plan(root, project, state)
                plan_ready = True
            except (WorkflowError, OSError, KeyError):
                plan_ready = False
        if not complete or not plan_ready:
            stage = "prepare-boards"
        elif not approvals["boards"]["approved"]:
            stage = "await-boards-approval"
        elif state.get("final_current") and (root / "deliverables" / "final.mp4").is_file():
            qa = state.get("qa", {})
            if qa_acceptance_current(root, state):
                stage = "complete"
            elif qa.get("automated_ok") is False:
                stage = "fix-required"
            else:
                stage = "await-final-qa"
        else:
            stage = "ready-to-render"
    state["stage"] = stage
    state["updated_at"] = now()
    return stage


def qa_acceptance_current(root: Path, state: dict[str, Any]) -> bool:
    """Only accept a final whose current QA report and recorded approval still agree."""
    try:
        require_render_receipt(root, state)
    except (WorkflowError, OSError):
        return False
    qa = state.get("qa", {})
    report_path = root / "deliverables" / "qa-report.json"
    final_path = root / "deliverables" / "final.mp4"
    if not (
        isinstance(qa, dict)
        and qa.get("automated_ok") is True
        and qa.get("manual_approved") is True
        and report_path.is_file()
        and final_path.is_file()
    ):
        return False
    final_hash = digest(final_path)
    if final_hash != state.get("final_sha256") or final_hash != qa.get("final_sha256"):
        return False
    if qa.get("report_sha256") != digest(report_path):
        return False
    try:
        report = read_json(report_path)
    except WorkflowError:
        return False
    return bool(
        report.get("version") == EXPECTED_QA_REPORT_VERSION
        and report.get("ok") is True
        and report.get("final", {}).get("sha256") == final_hash
        and report.get("visual_review", {}).get("accepted") is True
    )


def require_board_qa_for_render(root: Path) -> dict[str, Any]:
    """Collect fast board diagnostics before rendering; visual findings are advisory."""
    report = run_board_qa(root)
    high_risks = [
        risk
        for scene in report.get("scenes", [])
        if isinstance(scene, dict)
        for risk in scene.get("visual_risks", [])
        if isinstance(risk, dict) and risk.get("severity") == "high"
    ]
    if high_risks:
        print("BOARD_VISUAL_RISK=" + "；".join(str(item.get("message", "")) for item in high_risks[:6]))
    if report.get("ok"):
        return report
    errors = [str(item) for item in report.get("errors", []) if str(item).strip()]
    detail = "；".join(errors[:3]) or "未知板图或标注错误"
    print(f"BOARD_QA_ADVISORY={detail}；画面质量提示不否决已确认计划，继续按该计划执行")
    return report


def prepare_voice_text(script_path: Path, overrides_path: Path | None, out_path: Path) -> dict[str, Any]:
    if not script_path.is_file():
        raise WorkflowError(f"口播稿不存在：{script_path}")
    display_text = read_narration_text(script_path)
    if not display_text:
        raise WorkflowError("口播稿为空")
    overrides_data = empty_pronunciation_overrides()
    if overrides_path is not None:
        overrides_data = read_json(overrides_path)
    try:
        tts_text, applied = apply_pronunciation_overrides(display_text, overrides_data)
    except TextPolicyError as exc:
        raise WorkflowError(f"发音覆盖无效：{exc}") from exc
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tts_text + "\n", encoding="utf-8")
    return {"output": str(out_path), "applied": applied, "tts_text": tts_text}


def prepare_optional_environment(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(renderer_root() / "scripts" / "prepare_env.py"),
    ]
    if getattr(args, "upgrade", False):
        command.append("--upgrade")
    for provider in args.provider:
        command.extend(["--with-provider", provider])
    if set(args.provider) & {"piper", "sherpa", "voicestudio"}:
        command.append("--install-alignment")
    if args.extraction:
        command.append("--install-extraction")
    if args.install_ffmpeg:
        command.append("--install-ffmpeg")
    return subprocess.run(command, check=False).returncode


def prepare_extraction_environment() -> Path:
    """Prepare only source-extraction dependencies and return the renderer Python.

    FFmpeg remains an explicit, user-approved setup choice.  The extraction
    command may discover an already available isolated FFmpeg runtime, but this
    helper never requests its installation.
    """

    skill_root = renderer_root()
    command = [
        sys.executable,
        str(skill_root / "scripts" / "prepare_env.py"),
        "--install-extraction",
    ]
    result = subprocess.run(
        command,
        cwd=skill_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise WorkflowError("参考素材提取环境准备失败：" + detail[-1200:])
    return renderer_python(skill_root, prepare=False)


def run_tts_command(root: Path, args: argparse.Namespace) -> int:
    display_path = Path(args.display_script or args.script).expanduser().resolve()
    require_script_style_approval(root, display_path)
    provider = args.provider
    if provider in {"edge", "azure"} and not args.voice:
        args.voice = "zh-CN-XiaoxiaoNeural"
    if provider in {"edge", "piper"}:
        from tts_free import run as run_provider

        return run_provider(args)
    if provider == "azure":
        from tts_azure import run as run_provider

        return run_provider(args)
    if provider == "elevenlabs":
        from tts_elevenlabs import run as run_provider

        if not args.model:
            args.model = "eleven_multilingual_v2"
        return run_provider(args)
    if provider == "sherpa":
        from tts_sherpa import run as run_provider

        if not args.model_dir:
            print("[needs-input] --model-dir", file=sys.stderr)
            return 3
        return run_provider(args)
    if provider == "voicestudio":
        from tts_voicestudio import run as run_provider

        args.command = "synthesize"
        args.base_url = args.base_url or "http://127.0.0.1:3900"
        args.model = args.model or "tts-1"
        args.voice = args.voice or "default"
        return run_provider(args)
    raise WorkflowError(f"未知配音后端：{provider}")


def run_alignment_command(root: Path, args: argparse.Namespace) -> int:
    display_path = Path(args.script).expanduser().resolve()
    require_script_style_approval(root, display_path)
    from align_words import transcribe

    return transcribe(args)


def validate_timeline_contract(
    words_data: dict[str, Any],
    words: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    audio_duration: int | None,
) -> dict[str, int]:
    word_end = max(int(word["end_ms"]) for word in words)
    words_duration = int(words_data["duration_ms"])
    caption_end = max(int(cue["end_ms"]) for cue in cues)
    reference_duration = int(audio_duration or words_duration)
    if word_end > reference_duration + TIMELINE_EDGE_TOLERANCE_MS:
        raise WorkflowError(f"最后一个词结束于 {word_end}ms，超过音频时长 {reference_duration}ms")
    if caption_end > reference_duration + TIMELINE_EDGE_TOLERANCE_MS:
        raise WorkflowError(f"最后一条字幕结束于 {caption_end}ms，超过音频时长 {reference_duration}ms")
    if abs(words_duration - reference_duration) > TIMELINE_DURATION_TOLERANCE_MS:
        raise WorkflowError(
            f"words.json duration_ms {words_duration}ms 与音频时长 {reference_duration}ms 不一致"
        )
    if abs(caption_end - word_end) > TIMELINE_DURATION_TOLERANCE_MS:
        raise WorkflowError(f"字幕末尾 {caption_end}ms 与逐词末尾 {word_end}ms 不一致")
    return {
        "audio_duration_ms": reference_duration,
        "words_duration_ms": words_duration,
        "last_word_end_ms": word_end,
        "last_caption_end_ms": caption_end,
    }


def stage_script_voice(root: Path, args: argparse.Namespace) -> None:
    script_path = Path(args.script).resolve()
    if not script_path.is_file():
        raise WorkflowError("口播稿为空或不存在")
    project, state = require_script_style_approval(root, script_path)
    storyboard_data, storyboard_normalizations = compile_storyboard_draft(
        read_json(Path(args.storyboard).resolve())
    )
    project_version = int(project.get("version", 1))
    scenes = validate_storyboard(
        storyboard_data,
        require_v2=project_version >= 2,
        require_v3=project_version >= 3,
    )
    scenes = compile_scene_title_cards(project, scenes)
    storyboard_data["scenes"] = scenes
    words_data = read_json(Path(args.words).resolve())
    words = validate_words(words_data, require_duration=True)
    if int(project.get("version", 1)) >= 3:
        try:
            validate_transition_pause_budgets(scenes, words)
        except AnimationPlanError as exc:
            raise WorkflowError(str(exc)) from exc
    script_text = read_narration_text(script_path)
    if not script_text:
        raise WorkflowError("口播稿为空或只包含 Markdown 结构")
    overrides_arg = getattr(args, "pronunciation_overrides", None)
    overrides_path = Path(overrides_arg).resolve() if overrides_arg else None
    overrides_data = read_json(overrides_path) if overrides_path else empty_pronunciation_overrides()
    try:
        expected_tts_text, applied_overrides = apply_pronunciation_overrides(script_text, overrides_data)
    except TextPolicyError as exc:
        raise WorkflowError(f"发音覆盖无效：{exc}") from exc
    tts_script_arg = getattr(args, "tts_script", None)
    if overrides_path is not None and not tts_script_arg:
        raise WorkflowError("使用发音覆盖时必须提供 --tts-script，证明实际送入 TTS 的文本")
    tts_text = read_narration_text(Path(tts_script_arg).resolve()) if tts_script_arg else script_text
    if tts_text != expected_tts_text:
        raise WorkflowError("--tts-script 与展示文案及已确认发音覆盖推导出的文本不一致")
    captions_path = Path(args.captions).resolve()
    if not captions_path.is_file() or "-->" not in captions_path.read_text(encoding="utf-8-sig"):
        raise WorkflowError("captions.srt 为空或格式无效")
    try:
        caption_cues, caption_warnings = validate_caption_contract(
            script_text, captions_path.read_text(encoding="utf-8-sig")
        )
    except TextPolicyError as exc:
        raise WorkflowError(f"字幕 QA 未通过：{exc}") from exc
    max_timing = max(word["end_ms"] for word in words)
    max_scene = max(scene["end_ms"] for scene in scenes)
    if abs(max_timing - max_scene) > 1500:
        raise WorkflowError(f"逐词时间末尾 {max_timing}ms 与分镜末尾 {max_scene}ms 相差过大")

    audio_source = Path(args.audio).resolve()
    duration = audio_duration_ms(audio_source)
    timeline_contract = validate_timeline_contract(words_data, words, caption_cues, duration)
    if int(project.get("version", 1)) >= 3:
        prospective = copy.deepcopy(project)
        prospective["scenes"] = scenes
        prospective["characters"] = storyboard_data.get("characters", [])
        validate_visual_scenes_together(prospective, scenes, words, root)
        try:
            build_visual_plan(prospective, words, root)
        except VisualPlanError as exc:
            raise WorkflowError(f"视觉语义尚未准备好，未登记或覆盖配音文件：{exc}") from exc
    audio_target = root / "audio" / f"narration{audio_source.suffix.lower()}"
    storyboard_target = root / "storyboard.json"
    write_json(storyboard_target, storyboard_data)
    copied = {
        "storyboard": storyboard_target,
        "audio": copy_artifact(audio_source, audio_target),
        "words": copy_artifact(args.words, root / "audio" / "words.json"),
        "captions": copy_artifact(captions_path, root / "audio" / "captions.srt"),
    }
    narration_target = root / "script" / "narration.md"
    narration_target.write_text(script_text + "\n", encoding="utf-8")
    copied["script"] = narration_target
    tts_target = root / "audio" / "tts-script.txt"
    tts_target.write_text(tts_text + "\n", encoding="utf-8")
    overrides_target = root / "audio" / "pronunciation-overrides.json"
    write_json(overrides_target, {"version": 1, "overrides": applied_overrides})
    copied["tts_script"] = tts_target
    copied["pronunciation_overrides"] = overrides_target

    project["scenes"] = scenes
    project["characters"] = [
        dict(character)
        for character in storyboard_data.get("characters", [])
        if isinstance(character, dict) and str(character.get("id", "")).strip()
    ]
    project["audio_duration_ms"] = timeline_contract["audio_duration_ms"]
    project["caption_warnings"] = caption_warnings
    project["pronunciation_override_count"] = len(applied_overrides)
    provider = str(getattr(args, "provider", "edge") or "edge").lower()
    if provider not in {"edge", "piper", "azure", "elevenlabs", "sherpa", "voicestudio"}:
        raise WorkflowError(f"未知配音后端：{provider}")
    project["voice_selection"] = {
        "provider": provider,
        "voice": getattr(args, "voice", None),
        "voice_id": getattr(args, "voice_id", None),
        "model": getattr(args, "model", None),
        "timing_source": words_data.get("source"),
        "alternative_auditions_generated": False,
    }
    visual_plan_path: Path | None = None
    visual_plan_md_path: Path | None = None
    if int(project.get("version", 1)) >= 3:
        try:
            visual_plan = build_visual_plan(project, words, root)
        except VisualPlanError as exc:
            raise WorkflowError(f"视觉编排无法生成：{exc}") from exc
        visual_plan_path = root / "visual-plan.json"
        visual_plan_md_path = root / "visual-plan.md"
        write_json(visual_plan_path, visual_plan)
        visual_plan_md_path.write_text(render_visual_plan_markdown(visual_plan), encoding="utf-8")
    artifact_records = {
        key: {"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": digest(path)}
        for key, path in copied.items()
    }
    if visual_plan_path and visual_plan_md_path:
        artifact_records["visual_plan"] = {
            "path": str(visual_plan_path.relative_to(root)).replace("\\", "/"),
            "sha256": digest(visual_plan_path),
        }
        artifact_records["visual_plan_markdown"] = {
            "path": str(visual_plan_md_path.relative_to(root)).replace("\\", "/"),
            "sha256": digest(visual_plan_md_path),
        }
    state["artifacts"] = artifact_records
    state["approvals"]["script_voice"] = {"approved": False, "at": None}
    state["approvals"]["boards"] = {"approved": False, "at": None}
    mark_boards_stale(state, "script-voice-or-timeline-changed")
    state["render_cache"] = {}
    state["final_current"] = False
    state["final_sha256"] = None
    state["qa"] = {}
    refresh_stage(root, project, state)
    write_json(root / "project.json", project)
    write_json(root / "state.json", state)
    if visual_plan_path and visual_plan_md_path:
        print(f"VISUAL_PLAN={visual_plan_path}")
        print(f"VISUAL_PLAN_MD={visual_plan_md_path}")
        print("VISUAL_PLAN_SUMMARY=" + json.dumps(visual_plan["summary"], ensure_ascii=False))
    if caption_warnings:
        print("CAPTION_WARNINGS=" + json.dumps(caption_warnings, ensure_ascii=False))
    if storyboard_normalizations:
        print("STORYBOARD_NORMALIZED=" + json.dumps(storyboard_normalizations, ensure_ascii=False))


@serialized_project()
def approve(root: Path, gate: str) -> None:
    project, state = load_project(root)
    stage = refresh_stage(root, project, state)
    if gate == "script-style":
        if stage != "await-script-style-approval":
            raise WorkflowError(f"当前不能确认口播稿和风格，状态是 {stage}")
        script_record = state.get("artifacts", {}).get("script")
        if not isinstance(script_record, dict) or not script_record.get("path"):
            raise WorkflowError("缺少待确认口播稿")
        script_path = root / str(script_record["path"])
        if not script_path.is_file() or digest(script_path) != script_record.get("sha256"):
            raise WorkflowError("待确认口播稿已变化，请重新登记")
        state["approvals"]["script_style"] = {
            "approved": True,
            "at": now(),
            "script_sha256": script_record["sha256"],
            "style_id": project.get("style_id"),
        }
        if isinstance(project.get("style_selection"), dict):
            project["style_selection"]["requires_confirmation"] = False
    elif gate == "script-voice":
        if stage != "await-script-voice-approval":
            raise WorkflowError(f"当前不能确认脚本和配音，状态是 {stage}")
        audio_record = state.get("artifacts", {}).get("audio") or {}
        state["approvals"]["script_voice"] = {
            "approved": True,
            "at": now(),
            "audio_sha256": audio_record.get("sha256"),
            "provider": (project.get("voice_selection") or {}).get("provider"),
        }
    elif gate == "boards":
        if stage != "await-boards-approval":
            raise WorkflowError(f"当前不能确认画面，状态是 {stage}")
        require_effective_plan(root, project, state)
        review_path = root / "previews" / "ownership-report.json"
        board_review = read_json(review_path) if review_path.is_file() else check_boards(root)
        accepted_risks = [
            risk
            for scene in board_review.get("scenes", [])
            if isinstance(scene, dict)
            for risk in scene.get("visual_risks", [])
            if isinstance(risk, dict)
        ]
        state["approvals"]["boards"] = {
            "approved": True, "at": now(),
            "visual_quality_authority": "user-confirmed-plan",
            "visual_warnings_are_advisory": True,
            "accepted_visual_risks": accepted_risks,
            "ownership_report_sha256": digest(review_path) if review_path.is_file() else None,
            "plan_sha256": digest(root / "animation-plan.json") if int(project.get("version", 1)) >= 3 else None,
            "input_fingerprint": plan_input_fingerprint(root, project, state),
        }
        for record in state.get("boards", {}).values():
            if isinstance(record, dict):
                record.pop("stale", None)
                record.pop("stale_reason", None)
    else:
        raise WorkflowError(f"未知确认关卡：{gate}")
    refresh_stage(root, project, state)
    write_json(root / "project.json", project)
    write_json(root / "state.json", state)


def register_source_review(root: Path, review_file: str) -> None:
    project, state = load_project(root)
    analysis = state.get("source_extract", {}).get("analysis", {})
    manifest_record = analysis.get("files", {}).get("manifest", {})
    manifest_path = root / str(manifest_record.get("path", ""))
    if not manifest_path.is_file() or digest(manifest_path) != manifest_record.get("sha256"):
        raise WorkflowError("请先完成完整参考分析，再登记实际观看结论")
    review = read_json(Path(review_file).resolve())
    for key in ("opening_hook", "visual_style", "subtitle_style", "pacing", "adaptation"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise WorkflowError(f"参考观看结论缺少 {key}")
    windows = review.get("motion_windows")
    if not isinstance(windows, list) or not windows:
        raise WorkflowError("需记录实际观看的连续片段 motion_windows，不能仅看联系表")
    duration = int(read_json(manifest_path).get("source", {}).get("duration_ms", 0))
    for window in windows:
        if (not isinstance(window, dict) or not isinstance(window.get("start_ms"), int)
                or not isinstance(window.get("end_ms"), int)
                or not 0 <= window["start_ms"] < window["end_ms"] <= duration
                or not str(window.get("observation", "")).strip()):
            raise WorkflowError("每个 motion_windows 条目需包含有效视频时间范围与 observation")
    if not any(window["start_ms"] == 0 and window["end_ms"] >= min(3000, duration) for window in windows):
        raise WorkflowError("实际观看记录必须覆盖开场 0–3 秒")
    review["source_manifest_sha256"] = digest(manifest_path)
    target = root / "source" / "visual-review.json"
    write_json(target, review)
    analysis["visual_review"] = {"path": "source/visual-review.json", "sha256": digest(target)}
    analysis["status"] = "reviewed"
    write_json(root / "state.json", state)


def require_source_review(root: Path, state: dict[str, Any]) -> None:
    analysis = state.get("source_extract", {}).get("analysis", {})
    record = analysis.get("visual_review", {})
    path = root / str(record.get("path", ""))
    manifest_record = analysis.get("files", {}).get("manifest", {})
    if (not path.is_file() or digest(path) != record.get("sha256")
            or read_json(path).get("source_manifest_sha256") != manifest_record.get("sha256")):
        raise WorkflowError("完整参考分析尚缺实际观看记录；请用 review-source 登记画面、节奏和连续运动结论")


def annotation_template(root: Path, scene_id: str, image: str) -> Path:
    from PIL import Image
    project, _ = load_project(root)
    scene = next((item for item in project.get("scenes", []) if item["id"] == scene_id), None)
    if scene is None:
        raise WorkflowError(f"不存在场景：{scene_id}")
    with Image.open(image) as board:
        width, height = board.size
    scene_dur = int(scene["end_ms"]) - int(scene["start_ms"])
    elements = []
    for index, raw in enumerate(scene.get("elements") or [], 1):
        item = raw if isinstance(raw, dict) else {"label": str(raw)}
        identity = str(item.get("id") or item.get("sequence") or index)
        elements.append({"id": identity, "sequence": index, "sourceElementIds": [identity],
                         "label": item.get("label", ""), "triggerText": item.get("trigger_text") or item.get("triggerText") or "",
                         "region": None, "reveal": {"startMs": None, "durationMs": None}})
    visual_plan_path = root / "visual-plan.json"
    if visual_plan_path.is_file():
        try:
            vp = read_json(visual_plan_path)
            shot = next((s for s in vp.get("shots", []) if s.get("section_id") == scene_id), None)
            if shot:
                shot_start = int(shot.get("start_ms", 0))
                for el in elements:
                    beat = next((b for b in shot.get("beats", []) if b.get("target") == el["id"] or b.get("trigger_text") == el["triggerText"]), None)
                    if beat and beat.get("start_ms") is not None:
                        el["reveal"]["startMs"] = max(100, int(beat["start_ms"]) - shot_start)
                elements = calculate_adaptive_durations(elements, scene_dur)
        except Exception:
            pass
    target = root / "annotations" / f"{scene_id}.draft.json"
    if target.exists():
        raise WorkflowError(f"草稿已存在，未覆盖：{target}")
    write_json(target, {"sceneId": scene_id, "status": "draft", "canvas": {"width": width, "height": height},
                        "sceneDurationMs": scene_dur, "elements": elements})
    return target


def pace_annotations_command(
    root: Path,
    ratio: float = DEFAULT_PACING_RATIO,
    scene_id: str | None = None,
) -> dict[str, Any]:
    with project_lock(root):
        report = pace_project_annotations(root, ratio=ratio, scene_id=scene_id)
        if report.get("total_elements_paced", 0) > 0:
            project, state = load_project(root)
            mark_boards_stale(state, "时序已通过 pace-annotations 优化，需重新准备动画计划")
            write_json(root / "state.json", state)
        return report


def _synchronize_saved_storyboard_state(
    root: Path, project: dict[str, Any], state: dict[str, Any], storyboard: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Repair producer-owned registrations after a panel storyboard save."""

    if (project.get("scenes") != storyboard.get("scenes")
            or state.get("semantic_sync_pending")
            or not (root / "visual-plan.json").is_file()):
        project["scenes"] = copy.deepcopy(storyboard.get("scenes", []))
        if isinstance(storyboard.get("characters"), list):
            project["characters"] = copy.deepcopy(storyboard["characters"])
        words_path = root / "audio" / "words.json"
        if not words_path.is_file():
            raise WorkflowError("storyboard 已变化，但缺少 words.json，无法同步视觉计划")
        visual_plan = build_visual_plan(project, validate_words(read_json(words_path)), root)
        visual_plan_path = root / "visual-plan.json"
        visual_plan_md_path = root / "visual-plan.md"
        write_json(visual_plan_path, visual_plan)
        visual_plan_md_path.write_text(render_visual_plan_markdown(visual_plan), encoding="utf-8")
        state.setdefault("artifacts", {})["visual_plan"] = {
            "path": "visual-plan.json", "sha256": digest(visual_plan_path)
        }
        state["artifacts"]["visual_plan_markdown"] = {
            "path": "visual-plan.md", "sha256": digest(visual_plan_md_path)
        }
    storyboard_path = root / "storyboard.json"
    if storyboard_path.is_file():
        state.setdefault("artifacts", {})["storyboard"] = {
            "path": "storyboard.json", "sha256": digest(storyboard_path)
        }
    scenes_by_id = {str(item.get("id")): item for item in project.get("scenes", [])}
    for scene_id, record in state.get("boards", {}).items():
        if not isinstance(record, dict) or scene_id not in scenes_by_id:
            continue
        annotation_path = root / str(record.get("annotation", ""))
        image_path = root / str(record.get("image", ""))
        if not annotation_path.is_file() or not image_path.is_file():
            continue
        annotation = read_json(annotation_path)
        validate_annotation(annotation, scenes_by_id[scene_id])
        state["boards"][scene_id]["annotation_sha256"] = digest(annotation_path)
        state["boards"][scene_id]["image_sha256"] = digest(image_path)
    return project, state


def migrate_project(root: Path) -> dict:
    """Explicit, conservative identity migration. Never infer approval or timing."""
    project, state = load_project(root)
    storyboard = read_json(root / "storyboard.json")
    pending = []
    candidates = {}
    for scene in storyboard.get("scenes", []):
        sid = str(scene["id"])
        for index, element in enumerate(scene.get("elements", []), 1):
            element.setdefault("id", str(index))
            if not element.get("trigger_text") and not scene.get("visual_beats"):
                pending.append(f"{sid}/{element['id']} 缺少真实口播触发依据")
        record = state.get("boards", {}).get(sid, {})
        relative = record.get("annotation")
        if not relative:
            pending.append(f"{sid} 缺少标注")
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            raise WorkflowError("迁移只能处理当前项目内标注")
        ann = read_json(path)
        try:
            reconcile_annotation_semantic_ids(ann, scene, migrate=True)
        except WorkflowError as exc:
            pending.append(f"{sid}: {exc}")
            continue
        candidates[path] = ann
    candidates[root / "storyboard.json"] = storyboard
    project["scenes"] = copy.deepcopy(storyboard.get("scenes", []))
    candidates[root / "project.json"] = project
    state.setdefault("approvals", {})["script_voice"] = {"approved": False, "at": None}
    state["approvals"]["boards"] = {"approved": False, "at": None}
    state.update({"final_current": False, "qa": {}, "render_cache": {},
                  "stage": "await-script-voice-approval", "migration_pending": pending,
                  "semantic_sync_pending": True})
    candidates[root / "state.json"] = state
    backup = root / "migration-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True)
    for path in candidates:
        if path.is_file():
            dest = backup / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    try:
        for path, value in candidates.items():
            write_json(path, value)
    except Exception:
        for path in candidates:
            original = backup / path.relative_to(root)
            if original.is_file():
                shutil.copy2(original, path)
        raise
    report = {"status": "needs-review", "pending": pending,
              "backup": str(backup.relative_to(root)), "plan_rebuilt": False}
    write_json(root / "migration-report.json", report)
    return report


@serialized_project()
def rebuild_plan(root: Path) -> None:
    """Explicit preparation is transactional; saving edits never calls it."""
    project, state = load_project(root)
    paths = [root / name for name in ("project.json", "state.json", "visual-plan.json", "visual-plan.md", "animation-plan.json")]
    previous = {path: path.read_bytes() if path.is_file() else None for path in paths}
    backup = root / "plan-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True)
    for path, content in previous.items():
        if content is not None:
            (backup / path.name).write_bytes(content)
    try:
        storyboard = read_json(root / "storyboard.json")
        project, state = _synchronize_saved_storyboard_state(root, project, state, storyboard)
        upstream_state = copy.deepcopy(state)
        upstream_state.get("artifacts", {}).pop("animation_plan", None)
        verify_artifacts_current(root, upstream_state)
        if refresh_animation_plan(root, project, state, force=True) is None:
            raise WorkflowError("尚缺逐词时间或整板标注，无法准备计划")
        state.update({"final_current": False, "final_sha256": None, "qa": {}, "render_cache": {},
                      "semantic_sync_pending": False, "production_status": "needs-approval"})
        refresh_stage(root, project, state)
        write_json(root / "project.json", project)
        write_json(root / "state.json", state)
    except Exception:
        for path, content in previous.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise



def diagnose_board(root: Path, scene_id: str, image: Path, annotation: dict) -> dict:
    """Local evidence only: never prepare or mutate the execution plan."""
    from PIL import Image
    import numpy as np
    scene_id = validate_scene_id(scene_id)
    report = {"scene_id": scene_id, "image": str(image),
              "element_ids": [str(e.get("id", "")) for e in annotation.get("elements", [])],
              "errors": [], "ok": False}
    try:
        rgb = np.asarray(Image.open(image).convert("RGB"))
        owner, foreground, metrics = resolve_ownership(rgb, annotation)
        risks = semantic_crop_risks(owner, foreground, annotation)
        warnings = [*metrics.get("warnings", []), *(risk["message"] for risk in risks)]
        report.update(
            metrics=metrics,
            errors=metrics["errors"],
            warnings=warnings,
            visual_risks=risks,
            ok=not metrics["errors"],
        )
        unresolved = foreground & (owner == 0)
        if unresolved.any():
            ys, xs = np.where(unresolved)
            report["unresolved_bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
            overlay = rgb.copy()
            overlay[unresolved] = [255, 0, 80]
            target = root / "previews" / f"ownership-{scene_id}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(overlay).save(target)
            report["diagnostic_image"] = str(target.relative_to(root))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report["errors"] = [str(exc)]
    report["next_action"] = ("review-or-confirm" if report.get("warnings") else "ready") if report["ok"] else "修正无法读取的图像或标注数据；不因画面质量疑点反复重试"
    write_json(root / "previews" / f"ownership-{scene_id}.json", report)
    return report


def check_boards(root: Path) -> dict:
    project, state = load_project(root)
    reports = []
    for scene in project.get("scenes", []):
        sid = str(scene["id"])
        record = state.get("boards", {}).get(sid, {})
        try:
            annotation = read_json(root / record["annotation"])
            reports.append(diagnose_board(root, sid, root / record["image"], annotation))
        except (WorkflowError, OSError, KeyError) as exc:
            reports.append({"scene_id": sid, "ok": False, "errors": [str(exc)]})
    result = {"ok": all(r["ok"] for r in reports), "scenes": reports}
    write_json(root / "previews" / "ownership-report.json", result)
    return result


@serialized_project()
def add_board(root: Path, scene_id: str, image: str, annotation: str) -> None:
    project, state = load_project(root)
    if not state["approvals"]["script_voice"]["approved"]:
        raise WorkflowError("第二次确认前不能登记整板图")
    scene = next((item for item in project.get("scenes", []) if item["id"] == scene_id), None)
    if not scene:
        raise WorkflowError(f"分镜中不存在场景：{scene_id}")
    image_source = Path(image).resolve()
    if image_source.suffix.lower() not in ALLOWED_IMAGES:
        raise WorkflowError("整板图必须是 PNG、JPG 或 WebP")
    annotation_source = Path(annotation).resolve()
    annotation_data = read_json(annotation_source)
    from PIL import Image
    try:
        with Image.open(image_source) as board:
            if board.size != (annotation_data.get("canvas", {}).get("width"), annotation_data.get("canvas", {}).get("height")):
                raise WorkflowError("标注 canvas 与原始图片尺寸不一致；请按实际图片建立区域，禁止猜测坐标后缩放")
    except WorkflowError:
        raise
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"无法读取原始板图尺寸：{image_source}") from exc
    normalize_annotation_geometry(annotation_data)
    reconcile_annotation_semantic_ids(annotation_data, scene)
    validate_annotation(annotation_data, scene)
    if annotation_data.get("sceneId") and annotation_data["sceneId"] != scene_id:
        raise WorkflowError("annotation.sceneId 与 --scene-id 不一致")
    if int(project.get("version", 1)) >= 3 and "drawingPlan" not in annotation_data:
        renderer_profile = project.get("renderer_profile") if isinstance(project.get("renderer_profile"), dict) else {}
        annotation_data["drawingPlan"] = {
            "version": 2,
            "mode": str(renderer_profile.get("draw_mode", "layered")),
            "strokePlanner": str(renderer_profile.get("stroke_planner", "semantic-v2")),
            "colorSchedule": str(renderer_profile.get("color_schedule", "object-progressive-v1")),
            "pixelOwnership": str(renderer_profile.get("pixel_ownership", PIXEL_POLICY)),
            "colorReserveRatio": float(renderer_profile.get("color_reserve_ratio", 0.32)),
            "minimumColorMs": int(renderer_profile.get("minimum_color_ms", 900)),
        }
    elif int(project.get("version", 1)) >= 3 and isinstance(annotation_data.get("drawingPlan"), dict):
        annotation_data["drawingPlan"].setdefault("pixelOwnership", PIXEL_POLICY)
    image_target = root / "boards" / f"{scene_id}{image_source.suffix.lower()}"
    annotation_target = root / "annotations" / f"{scene_id}.annotation.json"
    project_aspect = project.get("aspect_ratio") or project.get("aspectRatio")
    preserve_layout = False
    visual_plan_path = root / "visual-plan.json"
    if visual_plan_path.is_file():
        visual_plan = read_json(visual_plan_path)
        preserve_layout = any(
            isinstance(shot, dict)
            and str(shot.get("section_id")) == scene_id
            and isinstance(shot.get("layout_plan"), dict)
            and shot["layout_plan"].get("planned_before_board") is True
            for shot in visual_plan.get("shots", [])
        )
    import tempfile
    # Stage only this board; full-project planning is an explicit operation.
    with tempfile.TemporaryDirectory(prefix="board-stage-") as stage_dir:
        staged_image = Path(stage_dir) / image_target.name
        staged_annotation = Path(stage_dir) / annotation_target.name
        layout_result = fit_board_safe_area(image_source, staged_image, annotation_data,
                                            project_aspect=project_aspect, preserve_layout=preserve_layout)
        reconcile_annotation_semantic_ids(annotation_data, scene)
        validate_annotation(annotation_data, scene)
        write_json(staged_annotation, annotation_data)
        image_target.parent.mkdir(parents=True, exist_ok=True)
        annotation_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_image, image_target)
        shutil.copy2(staged_annotation, annotation_target)
    state.setdefault("boards", {})[scene_id] = {
        "image": str(image_target.relative_to(root)).replace("\\", "/"),
        "annotation": str(annotation_target.relative_to(root)).replace("\\", "/"),
        "image_sha256": digest(image_target),
        "annotation_sha256": digest(annotation_target),
        "visual_safe_layout": layout_result,
    }
    state["approvals"]["boards"] = {"approved": False, "at": None}
    state["final_current"] = False
    state["final_sha256"] = None
    state["qa"] = {}
    diagnostic = diagnose_board(root, scene_id, image_target, annotation_data)
    state["boards"][scene_id]["ownership_ready"] = diagnostic["ok"]
    state["production_status"] = "needs-plan" if diagnostic["ok"] else "needs-annotation"
    state.setdefault("artifacts", {}).setdefault("animation_plan", {})["stale_reason"] = "板图登记变化；请显式准备并确认执行计划"
    print(f"BOARD_SAVED={scene_id}")
    print(f"BOARD_DIAGNOSTIC={root / 'previews' / ('ownership-' + scene_id + '.json')}")
    if not diagnostic["ok"]:
        print(f"[warn] {scene_id} 已保存，生产待处理：" + "；".join(diagnostic["errors"]))
    refresh_stage(root, project, state)
    write_json(root / "state.json", state)


def renderer_root() -> Path:
    candidate = Path(__file__).resolve().parents[1] / "renderer"
    if (candidate / "scripts" / "render_stream_whiteboard.py").is_file():
        return candidate.resolve()
    raise WorkflowError("SketchNarrator 内置 renderer 不完整，缺少 render_stream_whiteboard.py")


def prepare_animation_plan(
    root: Path, project: dict[str, Any], state: dict[str, Any],
    annotation_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build without saving the plan; ownership checks write derived preview diagnostics."""

    if int(project.get("version", 1)) < 3:
        return None
    words_path = root / "audio" / "words.json"
    scene_ids = [scene["id"] for scene in project.get("scenes", [])]
    if not words_path.is_file() or not scene_ids:
        return None
    storyboard_path = root / "storyboard.json"
    if storyboard_path.is_file() and project.get("scenes") != read_json(storyboard_path).get("scenes"):
        raise WorkflowError("project 与 storyboard 场景不一致；不能通过延长口播边界修补转场")
    annotations: dict[str, dict[str, Any]] = {}
    board_images: dict[str, Path] = {}
    for scene_id in scene_ids:
        record = state.get("boards", {}).get(scene_id)
        if not record:
            return None
        annotation_path = root / record["annotation"]
        if not annotation_path.is_file():
            return None
        annotations[scene_id] = copy.deepcopy((annotation_overrides or {}).get(scene_id, read_json(annotation_path)))
        validate_annotation(annotations[scene_id], next(scene for scene in project["scenes"] if scene["id"] == scene_id))
        image_path = root / record.get("image", "")
        if image_path.is_file():
            from PIL import Image
            try:
                with Image.open(image_path) as board:
                    canvas = annotations[scene_id].get("canvas", {})
                    if board.size != (canvas.get("width"), canvas.get("height")):
                        raise WorkflowError(f"{scene_id} 标注画布与已登记图片尺寸不一致")
            except WorkflowError:
                raise
            except (OSError, ValueError) as exc:
                raise WorkflowError(f"无法读取 {scene_id} 的板图尺寸") from exc
            board_images[scene_id] = image_path
    # Check every scene before optional animation classification can fail on the first one.
    ownership_reports = [diagnose_board(root, sid, board_images[sid], ann) for sid, ann in annotations.items()]
    write_json(root / "previews" / "ownership-report.json", {"ok": all(r["ok"] for r in ownership_reports), "scenes": ownership_reports})
    ownership_errors = [r["scene_id"] + ": " + "；".join(r["errors"]) for r in ownership_reports if not r["ok"]]
    if ownership_errors:
        raise WorkflowError("像素归属待处理（已逐幕汇总，未改变分镜）：\n" + "\n".join(ownership_errors) + "\n查看 previews/ownership-report.json 和对应问题图；不要重画或合并无关场景")
    try:
        visual_plan_path = root / "visual-plan.json"
        visual_plan = read_json(visual_plan_path) if visual_plan_path.is_file() else None
        style_config = resolve_style(str(project.get("style_id") or project.get("style") or "warm-pencil"))
        renderer_profile = project.get("renderer_profile") or style_config.get("renderer", {})
        selected_hand_mode = str(renderer_profile.get("hand_mode", "small-hand"))
        if selected_hand_mode == "bare-tip":
            selected_hand_mode = "no-hand"
        validated_words = validate_words(read_json(words_path))
        original_annotations = copy.deepcopy(annotations)
        timing_reservations = reserve_animation_windows(
            project,
            annotations,
            validated_words,
            visual_plan=visual_plan,
            board_images=board_images,
        )
        plan = build_animation_plan(
            project,
            annotations,
            validated_words,
            visual_plan=visual_plan,
            board_images=board_images,
            style_config=style_config,
            hand_mode=selected_hand_mode,
        )
        used = {(str(scene["sceneId"]), str(event.get("targetElementId")), event.get("effect"))
                for scene in plan["scenes"] for event in scene.get("events", [])}
        if any((str(item["sceneId"]), str(item["targetElementId"]), item["effect"]) not in used
               for item in timing_reservations):
            # A reservation is meaningful only for the effect actually selected.
            annotations = original_annotations
            timing_reservations = []
            plan = build_animation_plan(project, annotations, validated_words, visual_plan=visual_plan,
                                        board_images=board_images, style_config=style_config, hand_mode=selected_hand_mode)
        plan["ownershipReview"] = {
            r["scene_id"]: {
                "warnings": r.get("warnings", []),
                "visualRisks": r.get("visual_risks", []),
                "metrics": r.get("metrics", {}),
            }
            for r in ownership_reports
        }
        plan["timingReservations"] = timing_reservations
        from PIL import Image
        import numpy as np
        plan["objectMasks"] = {
            sid: compile_masks(np.asarray(Image.open(board_images[sid]).convert("RGB")), ann)
            for sid, ann in original_annotations.items()
        }
        plan["renderSettings"] = {"fps": int(renderer_profile.get("fps", 30)),
                                  "capLongEdge": int(renderer_profile.get("cap_long_edge", 1920))}
        plan["drawingBudgets"] = plan_budgets(original_annotations, plan)
        if str(plan.get("planVersion", "")) != DEFAULT_PLAN_VERSION:
            raise WorkflowError(
                f"默认 animation-plan 版本应为 {DEFAULT_PLAN_VERSION}，实际为 {plan.get('planVersion')}"
            )
        plan_errors = validate_animation_plan(plan, annotations)
        if plan_errors:
            raise WorkflowError("动画计划 QA 未通过：" + "；".join(plan_errors))
    except (AnimationPlanError, ValueError, TypeError) as exc:
        raise WorkflowError(str(exc)) from exc
    return plan


def refresh_animation_plan(root: Path, project: dict[str, Any], state: dict[str, Any], *, force: bool = False) -> Path | None:
    """Generate explicitly, or preserve an existing saved plan verbatim."""
    state.setdefault("approvals", {}).setdefault("boards", {"approved": False, "at": None})
    if (root / "animation-plan.json").is_file() and not force:
        try:
            require_effective_plan(root, project, state)
        except WorkflowError as exc:
            state.setdefault("artifacts", {}).setdefault("animation_plan", {})["stale_reason"] = str(exc)
            state["approvals"]["boards"] = {"approved": False, "at": None}
        return root / "animation-plan.json"
    plan = prepare_animation_plan(root, project, state)
    if plan is None:
        return None
    plan_path = root / "animation-plan.json"
    previous_hash = state.get("artifacts", {}).get("animation_plan", {}).get("sha256")
    plan["inputFingerprint"] = plan_input_fingerprint(root, project, state)
    write_json(plan_path, plan)
    plan_hash = digest(plan_path)
    state.setdefault("artifacts", {})["animation_plan"] = {
        "path": str(plan_path.relative_to(root)).replace("\\", "/"),
        "sha256": plan_hash,
        "input_fingerprint": plan["inputFingerprint"],
        "origin": "generated",
    }
    if previous_hash != plan_hash:
        # Any new effective plan requires the board/order gate again.
        state["approvals"]["boards"] = {"approved": False, "at": None}
        state["final_current"] = False
        state["final_sha256"] = None
        state["qa"] = {}
        # The plan also contains reveal timing and transitions.  A scene can
        # change without producing a timing reservation, so reservation rows
        # are not a complete cache-invalidation index.
        state["render_cache"] = {}
    return plan_path


def plan_input_fingerprint(root: Path, project: dict[str, Any], state: dict[str, Any]) -> str:
    """Bind a saved plan to actual semantic inputs, independent of mutable state hashes."""
    paths = {"storyboard.json", "visual-plan.json", "audio/words.json"}
    audio = state.get("artifacts", {}).get("audio", {}).get("path")
    if audio:
        paths.add(str(audio))
    for scene in project.get("scenes", []):
        record = state.get("boards", {}).get(str(scene["id"]), {})
        paths.update(str(record[key]) for key in ("image", "annotation") if record.get(key))
    value = {
        "pixel_policy": PIXEL_POLICY, "phase_policy": PHASE_POLICY,
        "contract_sources": {name: digest(_RENDERER_SCRIPTS / name)
                             for name in ("pixel_contract.py", "phase_budget.py")},
        "project": {key: project.get(key) for key in ("version", "scenes", "renderer_profile", "style", "style_id")},
        "files": {path: digest(root / path) if (root / path).is_file() else None for path in sorted(paths)},
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def require_effective_plan(root: Path, project: dict[str, Any], state: dict[str, Any], *, approved: bool = False) -> dict | None:
    if int(project.get("version", 1)) < 3:
        return None
    path = root / "animation-plan.json"
    record = state.get("artifacts", {}).get("animation_plan", {})
    if not path.is_file() or not record.get("sha256"):
        raise WorkflowError("缺少已登记的有效计划；请通过 rebuild-plan 生成后在工作台确认")
    plan_hash = digest(path)
    if record.get("sha256") != plan_hash:
        raise WorkflowError("计划文件已变化但未保存登记；请通过工作台保存，不能在渲染时覆盖")
    fingerprint = plan_input_fingerprint(root, project, state)
    plan = read_json(path)
    if str(plan.get("planVersion")) != DEFAULT_PLAN_VERSION:
        raise WorkflowError("旧计划仅兼容查看；请显式重建后确认当前执行协议")
    if record.get("input_fingerprint") != fingerprint or plan.get("inputFingerprint") != fingerprint:
        raise WorkflowError("计划所依据的上游已变化或属于旧协议；保留原计划，请显式 rebuild-plan 后重新确认")
    annotations = {str(scene["id"]): read_json(root / state["boards"][str(scene["id"])]["annotation"])
                   for scene in project.get("scenes", [])}
    errors = validate_animation_plan(plan, annotations)
    if errors:
        raise WorkflowError("有效计划不符合执行契约：" + "；".join(errors))
    try:
        if plan.get("drawingBudgets") != plan_budgets(annotations, plan):
            raise WorkflowError("已保存的阶段预算与当前编译结果不一致；请显式重建并重新确认")
    except ValueError as exc:
        raise WorkflowError(f"阶段预算无效：{exc}") from exc
    from PIL import Image
    import numpy as np
    masks = plan.get("objectMasks")
    if not isinstance(masks, dict) or set(masks) != set(annotations):
        raise WorkflowError("旧计划缺少明确对象掩码；请显式重建并重新确认")
    for sid, ann in annotations.items():
        expected_ids = {str(e["id"]) for e in ann["elements"]}
        if set(masks[sid]) != expected_ids:
            raise WorkflowError(f"{sid} 的计划掩码与对象 ID 不一致")
        derived = effective_annotation_for_plan(ann, sid, plan)
        try:
            require_ownership(np.asarray(Image.open(root / state["boards"][sid]["image"]).convert("RGB")), derived)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
    require_plan_context(project, plan, annotations, validate_words(read_json(root / "audio" / "words.json")))
    if approved:
        gate = state.get("approvals", {}).get("boards", {})
        if not gate.get("approved") or gate.get("plan_sha256") != plan_hash or gate.get("input_fingerprint") != fingerprint:
            raise WorkflowError("当前计划并非第三次确认批准的版本；请在工作台重新确认画面与顺序")
    return plan


def require_plan_context(project: dict, plan: dict, annotations: dict, words: list[dict]) -> None:
    """Check immutable semantic and word-clock fields, allowing edits within them."""
    if str(plan.get("planVersion")) != DEFAULT_PLAN_VERSION:
        raise WorkflowError("保存的有效计划必须使用当前 V3.3 协议")
    scenes = project.get("scenes", [])
    planned = plan.get("scenes", [])
    if [str(s.get("sceneId")) for s in planned] != [str(s["id"]) for s in scenes]:
        raise WorkflowError("有效计划必须逐一对应当前分镜场景，不能遗漏、增加或重排")
    for index, (scene, planned_scene) in enumerate(zip(scenes, planned)):
        sid = str(scene["id"])
        validate_annotation(annotations[sid], scene)
        expected = {"sceneStartMs": int(scene["start_ms"]), "sceneEndMs": int(scene["end_ms"]),
                    "narrationEndMs": int(scene["end_ms"]), "sceneDurationMs": annotations[sid]["sceneDurationMs"],
                    "renderEndMs": int(scenes[index + 1]["start_ms"]) if index + 1 < len(scenes) else int(scene["end_ms"])}
        if any(planned_scene.get(key) != value for key, value in expected.items()):
            raise WorkflowError(f"{sid} 的计划时间基准与真实分镜不一致")
    try:
        expected_transitions = {str(t["fromSceneId"]): t for t in transition_plan(scenes, words)}
    except AnimationPlanError as exc:
        raise WorkflowError(str(exc)) from exc
    for transition in plan.get("transitions", []):
        expected = expected_transitions.get(str(transition.get("fromSceneId")))
        if not expected or any(transition.get(key) != expected[key] for key in
                               ("fromSceneId", "toSceneId", "previousWordEndMs", "nextSceneFirstWordMs", "gapMs")):
            raise WorkflowError("计划转场的停顿来源与真实逐词时间不一致")


def snapshot_effective_plan(root: Path, project: dict[str, Any], state: dict[str, Any], *, preview: bool = False) -> Path:
    """Each job consumes an immutable content-addressed copy of the saved plan."""
    source = root / "animation-plan.json"
    if int(project.get("version", 1)) < 3:
        return source
    require_effective_plan(root, project, state, approved=not preview)
    expected = state["artifacts"]["animation_plan"]["sha256"]
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise WorkflowError("计划在准备执行时发生变化，请重新保存确认")
    target = root / ("previews" if preview else "renders") / ".plans" / f"{expected}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        target.write_bytes(content)
    if digest(target) != expected:
        raise WorkflowError("执行计划快照内容不一致，不能覆盖或继续使用")
    return target


def effective_annotation_for_plan(annotation: dict[str, Any], scene_id: str,
                                  animation_plan: dict[str, Any] | None, fps: int | None = None) -> dict[str, Any]:
    try:
        return apply_effective_annotation(annotation, scene_id, animation_plan, fps)
    except (ValueError, TypeError, KeyError) as exc:
        raise WorkflowError(f"{scene_id} 的执行预算无效：{exc}") from exc


def _probe_renderer_python(candidate: Path) -> bool:
    if not candidate.is_file():
        return False
    if os.name == "nt":
        if candidate.suffix.lower() != ".exe":
            return False
        try:
            with open(candidate, "rb") as f:
                header = f.read(2)
            if header != b"MZ":
                return False
        except Exception:
            return False
    try:
        res = subprocess.run(
            [str(candidate), "-c", "import cv2, numpy, av, PIL"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return res.returncode == 0
    except Exception:
        return False


def renderer_python(root: Path, prepare: bool = True) -> Path:
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if _probe_renderer_python(candidate):
            return candidate
    command = [sys.executable, str(root / "scripts" / "prepare_env.py")]
    if not prepare:
        command.append("--check")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in result.stdout.splitlines():
        if line.startswith("ENV_PY="):
            path = Path(line.split("=", 1)[1].strip())
            if _probe_renderer_python(path):
                return path
    raise WorkflowError("白板渲染环境不可用。" + (result.stderr.strip() or result.stdout.strip()))


def renderer_runtime_info(skill_root: Path, python: Path) -> dict[str, Any]:
    helper = skill_root / "scripts" / "ffmpeg_runtime.py"
    if not helper.is_file():
        return {
            "available": False,
            "path": None,
            "source": None,
            "capabilities": {"libx264": False, "subtitles": False, "concat_demuxer": False},
            "ffprobe": {"available": False, "path": None},
            "errors": [f"缺少统一 FFmpeg 检测器：{helper}"],
        }
    result = subprocess.run(
        [str(python), str(helper), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise WorkflowError("FFmpeg 运行时检测失败：" + (result.stderr.strip() or result.stdout.strip()))
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError("FFmpeg 运行时检测结果不是有效 JSON") from exc
    if not isinstance(info, dict):
        raise WorkflowError("FFmpeg 运行时检测结果格式无效")
    return info


def render_input_snapshot(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Hash actual production inputs, independently of mutable state hashes."""
    paths = {"project.json", "storyboard.json", "visual-plan.json", "animation-plan.json", "audio/words.json", "audio/captions.srt"}
    for name, record in state.get("artifacts", {}).items():
        if name not in {"sfx_plan", "sfx_track"} and record.get("path"):
            paths.add(str(record["path"]))
    for record in state.get("boards", {}).values():
        paths.update(str(record[key]) for key in ("image", "annotation") if record.get(key))
    files = {}
    for relative in sorted(paths):
        path = root / relative
        files[relative] = digest(path) if path.is_file() else None
    return {"version": 1, "files": files}


def require_render_receipt(root: Path, state: dict[str, Any]) -> None:
    receipt_path = root / "deliverables" / "render-receipt.json"
    final = root / "deliverables" / "final.mp4"
    if not receipt_path.is_file() or not final.is_file():
        raise WorkflowError("缺少成片输入凭据；旧成片不能按当前输入验收，需通过 render 生成新凭据")
    receipt = read_json(receipt_path)
    if receipt.get("final_sha256") != digest(final) or receipt.get("inputs") != render_input_snapshot(root, state):
        raise WorkflowError("成片与当前生产输入不匹配；请重新登记变化的上游并渲染，不能手动补 state 哈希")


def renderer_fingerprint(skill_root: Path) -> str:
    files: list[Path] = []
    for folder, patterns in (("scripts", ("*.py",)), ("assets", ("*.json", "*.png"))):
        base = skill_root / folder
        for pattern in patterns:
            files.extend(path for path in base.glob(pattern) if path.is_file())
    hasher = hashlib.sha256()
    for path in sorted(files, key=lambda item: str(item.relative_to(skill_root)).casefold()):
        relative = str(path.relative_to(skill_root)).replace("\\", "/")
        hasher.update(relative.encode("utf-8"))
        hasher.update(digest(path).encode("ascii"))
    title_card_renderer = skill_root.parent / "scripts" / "title_card.py"
    if title_card_renderer.is_file():
        hasher.update(b"public-scripts/title_card.py")
        hasher.update(digest(title_card_renderer).encode("ascii"))
    return hasher.hexdigest()


def _ffmpeg_filter_escape(value: str) -> str:
    return value.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def build_subtitle_filter(
    duration_sec: float,
    font_path: str | Path | None = None,
    font_style: str | None = None,
) -> str:
    """Build one portable libass filter using the same CJK font as PyAV."""
    resolved_path, font_family = cjk_font_identity(font_path)
    fonts_dir = _ffmpeg_filter_escape(str(resolved_path.parent))
    family = font_family.replace(",", " ").replace("'", "")
    bold = 1 if str(font_style or "").strip().casefold() == "bold" else 0
    return (
        f"tpad=stop_mode=clone:stop_duration=2,trim=duration={duration_sec:.6f},"
        f"subtitles=audio/captions.srt:fontsdir='{fonts_dir}':force_style="
        f"'FontName={family},Bold={bold},FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00181818,BorderStyle=1,Outline=2,Shadow=0,MarginV=36,Alignment=2'"
    )


def scene_render_cache_payload(
    root: Path,
    record: dict[str, Any],
    scene_plan: dict[str, Any],
    frame_plan: dict[str, Any],
    renderer_profile: dict[str, Any],
    fps: int,
    cap_long_edge: int,
    hand_mode: str,
    renderer_hash: str,
    effective_annotation_sha256: str | None = None,
    title_card: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "cache_version": 1,
        "image_sha256": digest(root / record["image"]),
        "annotation_sha256": digest(root / record["annotation"]),
        "effective_annotation_sha256": effective_annotation_sha256,
        "title_card": title_card,
        "animation_scene": scene_plan,
        "frame_plan": frame_plan,
        "renderer_profile": renderer_profile,
        "fps": fps,
        "cap_long_edge": cap_long_edge,
        "hand_mode": hand_mode,
        "renderer_sha256": renderer_hash,
        "encoding": "mp4v-to-h264-v1",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), payload


def scene_render_cache_key(
    root: Path,
    record: dict[str, Any],
    scene_plan: dict[str, Any],
    frame_plan: dict[str, Any],
    renderer_profile: dict[str, Any],
    fps: int,
    cap_long_edge: int,
    hand_mode: str,
    renderer_hash: str,
    effective_annotation_sha256: str | None = None,
    title_card: dict[str, Any] | None = None,
) -> str:
    key, _ = scene_render_cache_payload(
        root, record, scene_plan, frame_plan, renderer_profile, fps, cap_long_edge, hand_mode, renderer_hash,
        effective_annotation_sha256, title_card,
    )
    return key


def scene_invalidation_reason(
    root: Path,
    cached_entry: dict[str, Any] | None,
    new_payload: dict[str, Any],
) -> str:
    if not isinstance(cached_entry, dict) or not cached_entry.get("key"):
        return "no_prior_cache"
    relative = cached_entry.get("path")
    if not relative or not (root / str(relative)).is_file():
        return "cached_output_missing"
    if digest(root / str(relative)) != cached_entry.get("sha256"):
        return "cached_output_hash_mismatch"
    old_payload = cached_entry.get("payload", {})
    if not old_payload:
        return "cache_key_mismatch"
    if old_payload.get("image_sha256") != new_payload.get("image_sha256"):
        return "image_modified"
    if old_payload.get("annotation_sha256") != new_payload.get("annotation_sha256"):
        return "annotation_modified"
    if old_payload.get("effective_annotation_sha256") != new_payload.get("effective_annotation_sha256"):
        return "effective_annotation_modified"
    if old_payload.get("title_card") != new_payload.get("title_card"):
        return "title_card_modified"
    if old_payload.get("animation_scene") != new_payload.get("animation_scene"):
        return "animation_scene_modified"
    if old_payload.get("frame_plan") != new_payload.get("frame_plan"):
        return "frame_plan_modified"
    if old_payload.get("renderer_profile") != new_payload.get("renderer_profile"):
        return "renderer_profile_modified"
    if old_payload.get("hand_mode") != new_payload.get("hand_mode"):
        return "hand_mode_modified"
    if old_payload.get("renderer_sha256") != new_payload.get("renderer_sha256"):
        return "renderer_code_modified"
    if old_payload.get("fps") != new_payload.get("fps") or old_payload.get("cap_long_edge") != new_payload.get("cap_long_edge"):
        return "resolution_or_fps_modified"
    return "cache_invalidated"


def cache_record_current(root: Path, record: dict[str, Any] | None, key: str) -> bool:
    if not isinstance(record, dict) or record.get("key") != key:
        return False
    relative = record.get("path")
    expected = record.get("sha256")
    if not relative or not expected:
        return False
    output = root / str(relative)
    return output.is_file() and digest(output) == expected


def read_scene_cache_ledger(root: Path) -> dict[str, dict[str, Any]]:
    """Read successful per-scene renders independently of whole-pipeline state."""

    path = root / "renders" / "scene-cache.json"
    if not path.is_file():
        return {}
    try:
        payload = read_json(path)
    except WorkflowError as exc:
        print(f"[warn] 忽略损坏的场景缓存索引：{exc}")
        return {}
    scenes = payload.get("scenes")
    if not isinstance(scenes, dict):
        return {}
    return {
        str(scene_id): record
        for scene_id, record in scenes.items()
        if isinstance(record, dict)
    }


def write_scene_cache_ledger(root: Path, scenes: dict[str, dict[str, Any]]) -> None:
    """Persist each completed scene so a later merge/SFX failure cannot discard it."""

    write_json(root / "renders" / "scene-cache.json", {
        "version": 1,
        "updated_at": now(),
        "scenes": scenes,
    })


def run_checked(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    print("RUN=" + " ".join(command))
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    result = subprocess.run(command, cwd=cwd, env=process_env)
    if result.returncode != 0:
        raise WorkflowError(f"命令失败，退出码 {result.returncode}")


def preview(root: Path, scene_id: str | None) -> None:
    project, state = load_project(root)
    saved_plan = require_effective_plan(root, project, state)
    skill_root = renderer_root()
    python = renderer_python(skill_root)
    scene_ids = [scene_id] if scene_id else [scene["id"] for scene in project.get("scenes", [])]
    for current in scene_ids:
        record = state.get("boards", {}).get(current)
        if not record:
            raise WorkflowError(f"{current} 尚未登记整板图")
        output = root / "previews" / f"{current}-preview.png"
        annotation = effective_annotation_for_plan(read_json(root / record["annotation"]), current, saved_plan, 30)
        preview_annotation = root / "previews" / ".effective-annotations" / f"{current}.annotation.json"
        write_json(preview_annotation, annotation)
        run_checked([
            str(python), str(skill_root / "scripts" / "render_annotation_preview.py"),
            str(root / record["image"]), str(preview_annotation), str(output),
        ])
        print(f"PREVIEW={output}")
    all_scene_ids = [scene["id"] for scene in project.get("scenes", [])]
    if all(current in state.get("boards", {}) for current in all_scene_ids):
        board_report = run_board_qa(root)
        if board_report.get("errors"):
            print(
                "BOARD_QA_ADVISORY="
                + "；".join(str(item) for item in board_report.get("errors", [])[:6])
                + "；预览已生成，画面疑点由第三次确认决定"
            )
        print(f"BOARD_QA={root / 'previews' / 'board-qa.json'}")


def launch_panel(root: Path, host: str, port: int, no_open: bool, allow_lan: bool = False) -> None:
    project, state = load_project(root)
    recovered = reconcile_existing_boards(root, project, state)
    if recovered:
        state["approvals"]["boards"] = {"approved": False, "at": None}
        state["final_current"] = False
        state["final_sha256"] = None
        state["qa"] = {}
        write_json(root / "state.json", state)
        print("PANEL_BOARD_RECONCILED=" + ",".join(recovered))
    try:
        candidate = require_effective_plan(root, project, state)
        print("PANEL_PLAN_READY=" + ("true" if candidate is not None else "false reason=missing-inputs"))
    except WorkflowError as exc:
        # Keep the editor accessible so annotation issues can be repaired there.
        print(f"PANEL_PLAN_READY=false reason={exc}")
    server_script = Path(__file__).with_name("panel_server.py")
    if not server_script.is_file():
        raise WorkflowError("工作台启动器缺失：scripts/panel_server.py")
    command = [
        sys.executable, str(server_script), "--project", str(root),
        "--host", host, "--port", str(port),
    ]
    if no_open:
        command.append("--no-open")
    if allow_lan:
        command.append("--allow-lan")
    run_checked(command)


def render_panel_scene_preview(
    root: Path,
    scene_id: str,
    fps: int = 30,
    cap_long_edge: int = 960,
) -> dict[str, Any]:
    """Render one saved scene for the panel without touching final render state."""
    if fps < 8 or fps > 30:
        raise WorkflowError("工作台真实预览 fps 必须在 8 到 30 之间")
    if cap_long_edge < 480 or cap_long_edge > 1280:
        raise WorkflowError("工作台真实预览长边必须在 480 到 1280 之间")

    project, state = load_project(root)
    if int(project.get("version", 1)) >= 3:
        require_effective_plan(root, project, state)
    scene = next((item for item in project.get("scenes", []) if item.get("id") == scene_id), None)
    if not scene:
        raise WorkflowError(f"项目中不存在场景：{scene_id}")
    record = state.get("boards", {}).get(scene_id)
    if not isinstance(record, dict):
        raise WorkflowError(f"{scene_id} 尚未登记整板图")

    image_path = root / str(record.get("image", ""))
    annotation_path = root / str(record.get("annotation", ""))
    if not image_path.is_file() or not annotation_path.is_file():
        raise WorkflowError(f"{scene_id} 的整板图或标注文件缺失")
    annotation = read_json(annotation_path)
    animation_plan_path = snapshot_effective_plan(root, project, state, preview=True)
    animation_plan = read_json(animation_plan_path) if animation_plan_path.is_file() else None
    effective_annotation = effective_annotation_for_plan(annotation, scene_id, animation_plan, fps)
    validate_annotation(effective_annotation, scene)
    scene_index = next(index for index, item in enumerate(project["scenes"]) if item["id"] == scene_id)
    render_end = int(project["scenes"][scene_index + 1]["start_ms"]) if scene_index + 1 < len(project["scenes"]) else max(int(scene["end_ms"]), int(project.get("audio_duration_ms") or 0))
    duration_ms = render_end - int(scene["start_ms"])

    skill_root = renderer_root()
    python = renderer_python(skill_root)
    runtime = renderer_runtime_info(skill_root, python)
    runtime_env: dict[str, str] = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if runtime.get("available") and runtime.get("path"):
        runtime_env["WHITEBOARD_FFMPEG"] = str(runtime["path"])

    style = resolve_style(str(project.get("style_id") or project.get("style") or "warm-pencil"))
    renderer_profile = dict(project.get("renderer_profile") or style.get("renderer", {}))
    hand_mode = str(renderer_profile.get("hand_mode", "small-hand"))
    if hand_mode == "bare-tip":
        hand_mode = "no-hand"
    title_card = dict(scene.get("title_card")) if isinstance(scene.get("title_card"), dict) else None
    title_card_font_path: Path | None = None
    font_value = (project.get("font_profile") or {}).get("path") if isinstance(project.get("font_profile"), dict) else None
    if title_card and font_value:
        title_card_font_path = Path(str(font_value)).expanduser().resolve()
        if not title_card_font_path.is_file():
            raise WorkflowError(f"文字卡片字体不存在：{title_card_font_path}")

    presenter_manifest_path: Path | None = None
    presenter_profile = project.get("presenter_profile") if isinstance(project.get("presenter_profile"), dict) else {}
    manifest_value = renderer_profile.get("presenter_manifest") or presenter_profile.get("manifest")
    if hand_mode == "presenter" and manifest_value:
        presenter_manifest_path = Path(str(manifest_value)).expanduser()
        if not presenter_manifest_path.is_absolute():
            presenter_manifest_path = root / presenter_manifest_path
        presenter_manifest_path = presenter_manifest_path.resolve()
        if not presenter_manifest_path.is_file():
            raise WorkflowError(f"Presenter manifest 不存在：{presenter_manifest_path}")

    effective_annotation_bytes = (
        json.dumps(effective_annotation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    )
    cache_payload = {
        "scene_id": scene_id,
        "image_sha256": digest(image_path),
        "annotation_sha256": digest(annotation_path),
        "effective_annotation_sha256": hashlib.sha256(effective_annotation_bytes).hexdigest(),
        "animation_sha256": digest(animation_plan_path) if animation_plan_path.is_file() else None,
        "renderer_profile": renderer_profile,
        "presenter_manifest_sha256": digest(presenter_manifest_path) if presenter_manifest_path else None,
        "renderer_sha256": renderer_fingerprint(skill_root),
        "fps": fps,
        "cap_long_edge": cap_long_edge,
        "duration_ms": duration_ms,
        "title_card": title_card,
        "title_card_font_sha256": digest(title_card_font_path) if title_card_font_path else None,
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    output_dir = root / "previews" / "panel"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{scene_id}-{cache_key[:16]}.mp4"
    relative = str(output.relative_to(root)).replace("\\", "/")
    if output.is_file():
        return {
            "scene_id": scene_id,
            "path": relative,
            "sha256": digest(output),
            "duration_ms": duration_ms,
            "fps": fps,
            "cap_long_edge": cap_long_edge,
            "cache_hit": True,
        }

    profile_json = output_dir / f"{scene_id}-{cache_key[:16]}-profile.json"
    effective_annotation_path = output_dir / f"{scene_id}-{cache_key[:16]}-effective.annotation.json"
    write_json(effective_annotation_path, effective_annotation)
    target_frames = max(1, math.ceil(render_end * fps / 1000) - math.ceil(int(scene["start_ms"]) * fps / 1000))
    command = [
        str(python), str(skill_root / "scripts" / "render_stream_whiteboard.py"),
        str(image_path), str(effective_annotation_path), str(output.with_suffix(".rendering.mp4")),
        str(skill_root / "assets" / "drawing-hand.png"),
        "--ink-path", str(renderer_profile.get("ink_path", "skeleton")),
        "--ink-color-mode", str(renderer_profile.get("ink_color_mode", "source")),
        "--stroke-planner", str(renderer_profile.get("stroke_planner", "semantic-v2")),
        "--color-fill", str(renderer_profile.get("color_fill", "local-brush")),
        "--draw-mode", str(renderer_profile.get("draw_mode", "layered")),
        "--color-reserve-ratio", str(renderer_profile.get("color_reserve_ratio", 0.32)),
        "--minimum-color-ms", str(renderer_profile.get("minimum_color_ms", 900)),
        "--fps", str(fps),
        "--cap-long-edge", str(cap_long_edge),
        "--hand-mode", hand_mode,
        "--lead-frames", "0",
        "--target-frames", str(target_frames),
        "--total-ms", str(duration_ms),
        "--profile-json", str(profile_json),
    ]
    if animation_plan_path.is_file():
        command.extend(["--animation-plan", str(animation_plan_path), "--scene-id", scene_id])
    if presenter_manifest_path:
        command.extend(["--asset-manifest", str(presenter_manifest_path)])
    if title_card:
        command.extend([
            "--title-card-text", str(title_card["text"]),
            "--title-card-accent", str(title_card.get("accent") or "#356AE6"),
        ])
        if title_card_font_path:
            command.extend(["--title-card-font", str(title_card_font_path)])

    temporary = output.with_suffix(".rendering.mp4")
    temporary.unlink(missing_ok=True)
    started = time.perf_counter()
    process_env = os.environ.copy()
    process_env.update(runtime_env)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=process_env,
    )
    if result.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        detail = (result.stderr.strip() or result.stdout.strip())[-1600:]
        raise WorkflowError(f"{scene_id} 工作台真实预览失败：{detail}")
    latest_project, latest_state = load_project(root)
    require_effective_plan(root, latest_project, latest_state)
    if (digest(image_path) != cache_payload["image_sha256"]
            or digest(annotation_path) != cache_payload["annotation_sha256"]
            or digest(root / "animation-plan.json") != cache_payload["animation_sha256"]):
        raise WorkflowError("预览期间输入已变化，本次临时视频不能登记为当前预览")
    temporary.replace(output)
    return {
        "scene_id": scene_id,
        "path": relative,
        "sha256": digest(output),
        "duration_ms": duration_ms,
        "fps": fps,
        "cap_long_edge": cap_long_edge,
        "cache_hit": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }


def archive_final(root: Path) -> Path | None:
    final = root / "deliverables" / "final.mp4"
    if not final.is_file():
        return None
    archive = root / "deliverables" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = archive / f"final-before-render-{stamp}.mp4"
    shutil.copy2(final, target)
    receipt = root / "deliverables" / "render-receipt.json"
    if receipt.is_file():
        shutil.copy2(receipt, archive / f"final-before-render-{stamp}.receipt.json")
    return target


def render_presenter_bookends(
    root: Path,
    kind: str,
    intro_title: str | None,
    outro_message: str,
    fps: int,
    width: int,
    height: int,
) -> list[Path]:
    project, _ = load_project(root)
    skill_root = renderer_root()
    python = renderer_python(skill_root)
    script = skill_root / "scripts" / "render_presenter_bookend.py"
    if not script.is_file():
        raise WorkflowError("内置 renderer 尚未安装 Presenter 固定片段生成器")
    output_dir = root / "renders"
    output_dir.mkdir(parents=True, exist_ok=True)
    requested = ["intro", "outro"] if kind == "both" else [kind]
    presenter_profile = project.get("presenter_profile") if isinstance(project.get("presenter_profile"), dict) else {}
    manifest_value = presenter_profile.get("manifest")
    manifest_path: Path | None = None
    if manifest_value:
        manifest_path = Path(str(manifest_value)).expanduser()
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        manifest_path = manifest_path.resolve()
        if not manifest_path.is_file():
            raise WorkflowError(f"Presenter manifest 不存在：{manifest_path}")
    outputs: list[Path] = []
    for item in requested:
        text = (
            intro_title or str(project.get("topic") or project.get("title") or "今天的问题")
            if item == "intro"
            else outro_message
        )
        output = output_dir / f"presenter-{item}.mp4"
        command = [
            str(python), str(script),
            "--kind", item,
            "--text", text,
            "--output", str(output),
            "--fps", str(fps),
            "--width", str(width),
            "--height", str(height),
        ]
        if manifest_path:
            command.extend(["--manifest", str(manifest_path)])
        run_checked(command)
        outputs.append(output)
        print(f"PRESENTER_{item.upper()}={output}")
    return outputs


@serialized_project("render")
def render(
    root: Path,
    fps: int,
    cap_long_edge: int,
    hand_mode: str | None,
    jobs: int = 2,
    sfx_enabled_override: bool | None = None,
) -> None:
    if not 8 <= fps <= 60:
        raise WorkflowError("正式渲染 fps 必须在 8 到 60 之间，与计划帧预算范围一致")
    if jobs < 1 or jobs > 4:
        raise WorkflowError("--jobs 必须在 1 到 4 之间；默认 2，低内存设备可用 1")
    with project_lock(root):
        project, state = load_project(root)
        if int(project.get("version", 1)) >= 3:
            approved_plan = require_effective_plan(root, project, state, approved=True)
            if (fps != approved_plan.get("renderSettings", {}).get("fps")
                    or cap_long_edge != approved_plan.get("renderSettings", {}).get("capLongEdge")):
                raise WorkflowError("输出帧率或尺寸与批准计划不同；请更新渲染配置并重新准备、确认")
        stage = refresh_stage(root, project, state)
        if stage not in {"ready-to-render", "fix-required"}:
            raise WorkflowError(f"当前不能渲染，状态是 {stage}")
        verify_artifacts_current(root, state)
        board_report = require_board_qa_for_render(root)
        board_status = "passed" if board_report.get("ok") else "advisory"
        print(f"BOARD_QA={board_status} scenes={len(board_report.get('scenes', []))}")
        skill_root = renderer_root()
        python = renderer_python(skill_root)
        runtime = renderer_runtime_info(skill_root, python)
        ffprobe = runtime.get("ffprobe", {}).get("path")
        runtime_env: dict[str, str] = {}
        if runtime.get("available") and runtime.get("path"):
            runtime_env["WHITEBOARD_FFMPEG"] = str(runtime["path"])
            print(f"FFMPEG={runtime['path']} ({runtime.get('source')})")
        else:
            print(
                "[warn] 未找到隔离 FFmpeg，将使用较慢的 PyAV 回退。"
                "可在明确同意后运行底层 prepare_env.py --install-ffmpeg。"
            )
        font_profile = project.get("font_profile") if isinstance(project.get("font_profile"), dict) else {}
        font_path = str(font_profile.get("path") or "").strip() or None
        font_style = str(font_profile.get("style") or "").strip() or None
        if font_path:
            try:
                cjk_font_identity(font_path)
            except (OSError, RuntimeError, ValueError) as exc:
                raise WorkflowError(f"项目默认字体不可用：{font_path}：{exc}") from exc
            runtime_env["SKETCHNARRATOR_FONT"] = font_path
        audio_record = state["artifacts"]["audio"]["path"]
        audio_path = root / audio_record
        measured_audio_ms = audio_duration_ms(
            audio_path,
            python,
            runtime.get("ffprobe", {}).get("path"),
        )
        words_data = read_json(root / state["artifacts"]["words"]["path"])
        words = validate_words(words_data)
        plan = duration_plan(project["scenes"], words, measured_audio_ms)
        frames = frame_render_plan(project["scenes"], plan["target_duration_ms"], fps)
        target_duration_ms = int(frames["target_duration_ms"])
        target_frames = int(frames["target_frames"])
        project["audio_duration_ms"] = plan["audio_duration_ms"]
        project["render_duration_ms"] = target_duration_ms
        project["render_target_frames"] = target_frames
        project["render_fps"] = fps
        try:
            bookend_spec = prepare_bookend_spec(
                project,
                ffprobe,
            )
        except BookendError as exc:
            raise WorkflowError(str(exc)) from exc
        if bookend_spec:
            project["bookends"] = bookend_spec
            project["render_duration_ms"] = (
                target_duration_ms
                + int(bookend_spec["prefix_duration_ms"])
                + int(bookend_spec["suffix_duration_ms"])
            )

        renderer_profile = project.get("renderer_profile") or resolve_style(project.get("style", "warm-pencil"))["renderer"]
        ink_path = renderer_profile.get("ink_path", "skeleton")
        stroke_planner = renderer_profile.get("stroke_planner", "semantic-v2")
        color_fill = renderer_profile.get("color_fill", "local-brush")
        draw_mode = renderer_profile.get("draw_mode", "layered")
        color_reserve_ratio = renderer_profile.get("color_reserve_ratio", 0.32)
        minimum_color_ms = renderer_profile.get("minimum_color_ms", 900)
        selected_hand_mode = hand_mode or renderer_profile.get("hand_mode", "small-hand")
        if hand_mode and hand_mode != renderer_profile.get("hand_mode", "small-hand"):
            raise WorkflowError("手部模式与批准的配置不同，请在工作台保存配置并重新确认")
        if selected_hand_mode == "bare-tip":
            selected_hand_mode = "no-hand"
        presenter_profile = project.get("presenter_profile") if isinstance(project.get("presenter_profile"), dict) else {}
        presenter_manifest_value = renderer_profile.get("presenter_manifest") or presenter_profile.get("manifest")
        presenter_manifest_path: Path | None = None
        if selected_hand_mode == "presenter" and presenter_manifest_value:
            presenter_manifest_path = Path(str(presenter_manifest_value)).expanduser()
            if not presenter_manifest_path.is_absolute():
                presenter_manifest_path = root / presenter_manifest_path
            presenter_manifest_path = presenter_manifest_path.resolve()
            if not presenter_manifest_path.is_file():
                raise WorkflowError(f"Presenter manifest 不存在：{presenter_manifest_path}")
            presenter_manifest = read_json(presenter_manifest_path)
            asset_hashes = {"manifest": digest(presenter_manifest_path)}
            for asset_name, asset_record in (presenter_manifest.get("assets") or {}).items():
                if not isinstance(asset_record, dict) or not asset_record.get("runtime_file"):
                    continue
                asset_path = presenter_manifest_path.parent / str(asset_record["runtime_file"])
                if not asset_path.is_file():
                    raise WorkflowError(f"Presenter 资产不存在：{asset_path}")
                asset_hashes[str(asset_name)] = digest(asset_path)
            renderer_profile = dict(renderer_profile)
            renderer_profile["presenter_asset_fingerprint"] = asset_hashes
        animation_plan_path = snapshot_effective_plan(root, project, state)
        animation_plan = read_json(animation_plan_path) if animation_plan_path.is_file() else {"scenes": []}
        plan_by_scene = {str(item.get("sceneId")): item for item in animation_plan.get("scenes", [])}
        frame_by_scene = {str(item["scene_id"]): item for item in frames["scenes"]}
        # Resolve every optional audio input before starting expensive scene
        # rendering. --no-sfx returns before any machine-local settings lookup.
        sfx_plan_path = root / "audio" / "sfx-plan.json"
        sfx_track_path: Path | None = None
        sfx_event_count = 0
        try:
            sfx_plan, sfx_preset_root = build_sfx_plan(
                root,
                project,
                state,
                animation_plan,
                target_duration_ms,
                Path(__file__).resolve().parents[1],
                enabled_override=sfx_enabled_override,
            )
            write_json(sfx_plan_path, sfx_plan)
            state.setdefault("artifacts", {})["sfx_plan"] = {
                "path": "audio/sfx-plan.json",
                "sha256": digest(sfx_plan_path),
            }
            if sfx_plan.get("enabled") and sfx_preset_root is not None:
                sfx_track_path = root / "audio" / "sfx-track.wav"
                sfx_result = render_sfx_track(sfx_plan, sfx_preset_root, sfx_track_path)
                sfx_event_count = int(sfx_result["events"])
                state["artifacts"]["sfx_track"] = {
                    "path": "audio/sfx-track.wav",
                    "sha256": digest(sfx_track_path),
                    "events": sfx_event_count,
                    "preset": str(sfx_plan.get("preset")),
                }
                print(f"SFX_PLAN={sfx_plan_path}")
                print(f"SFX_TRACK={sfx_track_path} events={sfx_event_count}")
            else:
                state.setdefault("artifacts", {}).pop("sfx_track", None)
                print("SFX=disabled")
        except SfxError as exc:
            raise WorkflowError(f"音效预检失败，尚未渲染场景：{exc}") from exc
        renderer_hash = renderer_fingerprint(skill_root)
        # Persist derived audio/frame metadata before taking an immutable snapshot.
        write_json(root / "project.json", project)
        input_snapshot = render_input_snapshot(root, state)
        state_snapshot = digest(root / "state.json")
    state_cache = state.get("render_cache", {}) if isinstance(state.get("render_cache"), dict) else {}
    current_scene_ids = {str(scene["id"]) for scene in project["scenes"]}
    ledger_cache = {
        scene_id: record
        for scene_id, record in read_scene_cache_ledger(root).items()
        if scene_id in current_scene_ids
    }
    next_cache: dict[str, dict[str, Any]] = {}
    rendered: list[Path] = [root / "renders" / f"{scene['id']}.mp4" for scene in project["scenes"]]
    misses: list[dict[str, Any]] = []

    for scene in project["scenes"]:
        scene_id = scene["id"]
        record = state["boards"][scene_id]
        annotation = read_json(root / record["annotation"])
        effective_annotation = effective_annotation_for_plan(annotation, scene_id, animation_plan, fps)
        validate_annotation(effective_annotation, scene)
        effective_annotation_bytes = (
            json.dumps(effective_annotation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
        )
        effective_annotation_sha256 = hashlib.sha256(effective_annotation_bytes).hexdigest()
        scene_frames = frame_by_scene[scene_id]
        scene_plan = plan_by_scene.get(scene_id, {})
        title_card = dict(scene.get("title_card")) if isinstance(scene.get("title_card"), dict) else None
        title_card_cache = copy.deepcopy(title_card)
        if title_card_cache is not None and font_path:
            title_card_cache["font_sha256"] = digest(Path(font_path))
        if int(project.get("version", 1)) >= 3 and not scene_plan:
            raise WorkflowError(f"animation-plan.json 缺少 {scene_id}")
        transition = scene_plan.get("transition") if isinstance(scene_plan, dict) else None
        if isinstance(transition, dict):
            local_clean_end = int(transition.get("cleanCanvasEndMs", 0)) - int(scene_plan.get("sceneStartMs", 0))
            content_budget_ms = round(
                (int(scene_frames["target_frames"]) - int(scene_frames["lead_frames"])) * 1000 / fps
            )
            if local_clean_end < 0 or local_clean_end > content_budget_ms + math.ceil(1000 / fps):
                raise WorkflowError(
                    f"{scene_id} 的板擦结束时间 {local_clean_end}ms 超出内容帧预算 {content_budget_ms}ms"
                )
        output = root / "renders" / f"{scene_id}.mp4"
        profile_json_path = root / "renders" / f"{scene_id}-profile.json"
        key, payload = scene_render_cache_payload(
            root,
            record,
            scene_plan,
            scene_frames,
            renderer_profile,
            fps,
            cap_long_edge,
            selected_hand_mode,
            renderer_hash,
            effective_annotation_sha256,
            title_card_cache,
        )
        cached_entry = next(
            (
                candidate
                for candidate in (state_cache.get(scene_id), ledger_cache.get(scene_id))
                if cache_record_current(root, candidate, key)
            ),
            None,
        )
        if cached_entry is not None:
            next_cache[scene_id] = cached_entry
            ledger_cache[scene_id] = cached_entry
            print(f"CACHE_HIT={scene_id}")
            continue
        prior_entry = state_cache.get(scene_id) or ledger_cache.get(scene_id)
        reason = scene_invalidation_reason(root, prior_entry, payload)
        print(f"CACHE_MISS={scene_id} reason={reason}")
        effective_annotation_path = root / "renders" / ".effective-annotations" / f"{scene_id}-{effective_annotation_sha256}.annotation.json"
        write_json(effective_annotation_path, effective_annotation)
        base_command = [
            str(python), str(skill_root / "scripts" / "render_stream_whiteboard.py"),
            str(root / record["image"]), str(effective_annotation_path),
            str(skill_root / "assets" / "drawing-hand.png"),
            "--ink-path", str(ink_path),
            "--ink-color-mode", str(renderer_profile.get("ink_color_mode", "source")),
            "--stroke-planner", str(stroke_planner),
            "--color-fill", str(color_fill),
            "--draw-mode", str(draw_mode),
            "--color-reserve-ratio", str(color_reserve_ratio),
            "--minimum-color-ms", str(minimum_color_ms),
            "--fps", str(fps), "--cap-long-edge", str(cap_long_edge),
            "--hand-mode", str(selected_hand_mode),
            "--lead-frames", str(scene_frames["lead_frames"]),
            "--target-frames", str(scene_frames["target_frames"]),
            "--total-ms", str(scene_frames["total_ms"]),
            "--profile-json", str(profile_json_path),
        ]
        if animation_plan_path.is_file():
            base_command.extend(["--animation-plan", str(animation_plan_path), "--scene-id", str(scene_id)])
        if presenter_manifest_path:
            base_command.extend(["--asset-manifest", str(presenter_manifest_path)])
        if title_card:
            base_command.extend([
                "--title-card-text", str(title_card["text"]),
                "--title-card-accent", str(title_card.get("accent") or "#356AE6"),
            ])
            if font_path:
                base_command.extend(["--title-card-font", str(font_path)])
        misses.append({
            "scene_id": scene_id,
            "output": output,
            "command": base_command,
            "key": key,
            "payload": payload,
            "frame_plan": scene_frames,
        })

    def render_scene(spec: dict[str, Any]) -> dict[str, Any]:
        scene_id = str(spec["scene_id"])
        output = Path(spec["output"])
        temporary = output.with_name(f".{output.stem}.rendering-{os.getpid()}{output.suffix}")
        temporary.unlink(missing_ok=True)
        command = [*spec["command"][:4], str(temporary), *spec["command"][4:]]
        process_env = os.environ.copy()
        process_env.update(runtime_env)
        process_env["PYTHONUTF8"] = "1"
        process_env["PYTHONIOENCODING"] = "utf-8"
        started = time.perf_counter()
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_env,
        )
        elapsed = time.perf_counter() - started
        if result.returncode != 0 or not temporary.is_file():
            temporary.unlink(missing_ok=True)
            detail = (result.stderr.strip() or result.stdout.strip())[-1600:]
            raise WorkflowError(f"{scene_id} 渲染失败，退出码 {result.returncode}：{detail}")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(output)
        return {
            "scene_id": scene_id,
            "path": str(output.relative_to(root)).replace("\\", "/"),
            "sha256": digest(output),
            "key": spec["key"],
            "target_frames": int(spec["frame_plan"]["target_frames"]),
            "lead_frames": int(spec["frame_plan"]["lead_frames"]),
            "elapsed_sec": round(elapsed, 3),
            "stdout": result.stdout,
        }

    scene_started = time.perf_counter()
    workers = min(jobs, max(1, len(misses)))
    results: list[dict[str, Any]] = []

    def record_scene_result(result: dict[str, Any]) -> None:
        scene_id = str(result["scene_id"])
        spec_payload = next((m["payload"] for m in misses if m["scene_id"] == scene_id), {})
        entry = {
            key: value for key, value in result.items() if key not in {"stdout", "scene_id"}
        }
        if spec_payload:
            entry["payload"] = spec_payload
        next_cache[scene_id] = entry
        ledger_cache[scene_id] = entry
        write_scene_cache_ledger(root, ledger_cache)

    if misses and workers == 1:
        for spec in misses:
            result = render_scene(spec)
            results.append(result)
            record_scene_result(result)
    elif misses:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="whiteboard-scene") as pool:
            futures = {pool.submit(render_scene, spec): spec["scene_id"] for spec in misses}
            failures: list[Exception] = []
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    record_scene_result(result)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise failures[0]
    for result in sorted(results, key=lambda item: item["scene_id"]):
        if result.get("stdout", "").strip():
            print(result["stdout"].rstrip())
        print(f"RENDERED={result['scene_id']} elapsed={result['elapsed_sec']}s")
    scene_wall_ms = round((time.perf_counter() - scene_started) * 1000.0, 2)
    state["render_cache"] = next_cache
    t_merge_start = time.perf_counter()
    silent = root / "renders" / "silent.mp4"
    run_checked([
        str(python), str(skill_root / "scripts" / "merge_scenes.py"),
        "--inputs", *map(str, rendered), "--output", str(silent),
    ], env=runtime_env)
    t_merge_ms = (time.perf_counter() - t_merge_start) * 1000.0

    t_compose_start = time.perf_counter()
    final = root / "deliverables" / "final.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    temporary_final = final.with_name(f".final.rendering-{os.getpid()}.mp4")
    temporary_final.unlink(missing_ok=True)
    capabilities = runtime.get("capabilities", {})
    ffmpeg = runtime.get("path")
    if ffmpeg and capabilities.get("libx264") and capabilities.get("subtitles"):
        duration_sec = target_frames / fps
        subtitle_filter = build_subtitle_filter(duration_sec, font_path, font_style)
        if sfx_track_path is not None:
            audio_filter = (
                f"[1:a]apad=whole_dur={duration_sec:.6f}[voice];"
                f"[2:a]apad=whole_dur={duration_sec:.6f}[effects];"
                f"[voice][effects]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                f"atrim=duration={duration_sec:.6f}[mixed]"
            )
            run_checked([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", "renders/silent.mp4", "-i", audio_record, "-i", "audio/sfx-track.wav",
                "-filter_complex", audio_filter,
                "-map", "0:v:0", "-map", "[mixed]", "-vf", subtitle_filter,
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k",
                "-frames:v", str(target_frames), "-t", f"{duration_sec:.6f}", "-movflags", "+faststart",
                str(temporary_final.relative_to(root)).replace("\\", "/"),
            ], cwd=root, env=runtime_env)
        else:
            run_checked([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", "renders/silent.mp4", "-i", audio_record,
                "-map", "0:v:0", "-map", "1:a:0", "-vf", subtitle_filter,
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-af", "apad",
                "-frames:v", str(target_frames), "-t", f"{duration_sec:.6f}", "-movflags", "+faststart",
                str(temporary_final.relative_to(root)).replace("\\", "/"),
            ], cwd=root, env=runtime_env)
    else:
        print("[warn] FFmpeg 缺少 libx264 或字幕滤镜，最终合成改用 PyAV")
        compose_command = [
            str(python), str(Path(__file__).with_name("compose_final.py")),
            "--video", str(silent), "--audio", str(root / audio_record),
            "--captions", str(root / "audio" / "captions.srt"), "--output", str(temporary_final),
            "--duration-ms", str(target_duration_ms),
        ]
        if sfx_track_path is not None:
            compose_command.extend(["--sfx", str(sfx_track_path)])
        run_checked(compose_command)
    candidate_final = temporary_final
    final_duration_ms = target_duration_ms
    if bookend_spec:
        if not ffmpeg:
            raise WorkflowError("外部固定片段已启用，但当前没有可用 FFmpeg，无法自动接入开场和结尾")
        try:
            main_info = probe_media(ffprobe, temporary_final)
            bookended = final.with_name(f".final.bookended-{os.getpid()}.mp4")
            bookended.unlink(missing_ok=True)
            final_duration_ms = compose_bookends(
                ffmpeg,
                temporary_final,
                bookended,
                bookend_spec,
                main_info,
                root,
            )
        except BookendError as exc:
            raise WorkflowError(str(exc)) from exc
        temporary_final.unlink(missing_ok=True)
        candidate_final = bookended
    if not candidate_final.is_file():
        raise WorkflowError("最终合成未生成临时成片，旧 final.mp4 保持不变")
    with project_lock(root):
        current_project, current_state = load_project(root)
        require_effective_plan(root, current_project, current_state, approved=True)
        if input_snapshot != render_input_snapshot(root, current_state):
            raise WorkflowError("渲染期间生产输入发生变化；临时成片未替换 final.mp4，请登记变更后重试")
        if digest(root / "state.json") != state_snapshot:
            raise WorkflowError("渲染期间项目状态发生变化；保留已保存编辑和旧成片，请重新确认后渲染")
        archived = archive_final(root)
        if archived:
            print(f"ARCHIVE={archived}")
        candidate_final.replace(final)
        t_compose_ms = (time.perf_counter() - t_compose_start) * 1000.0
        state["render_metrics"] = {
            "jobs": workers,
            "cache_hits": len(project["scenes"]) - len(misses),
            "cache_misses": len(misses),
            "scene_wall_ms": scene_wall_ms,
            "merge_ms": round(t_merge_ms, 2),
            "compose_ms": round(t_compose_ms, 2),
            "total_render_pipeline_ms": round(scene_wall_ms + t_merge_ms + t_compose_ms, 2),
            "target_frames": target_frames,
            "target_duration_ms": target_duration_ms,
            "final_duration_ms": final_duration_ms,
            "hand_mode": str(selected_hand_mode),
            "ffmpeg_source": runtime.get("source"),
            "sfx_enabled": sfx_track_path is not None,
            "sfx_events": sfx_event_count,
            "bookends_enabled": bool(bookend_spec),
        }
        state["final_current"] = True
        state["final_sha256"] = digest(final)
        write_json(root / "deliverables" / "render-receipt.json", {
            "version": 1, "created_at": now(), "inputs": input_snapshot,
            "final_sha256": state["final_sha256"], "renderer_sha256": renderer_hash,
            "settings": {"fps": fps, "cap_long_edge": cap_long_edge, "hand_mode": selected_hand_mode,
                         "renderer_profile": renderer_profile, "sfx_enabled_override": sfx_enabled_override,
                         "bookends": bookend_spec or {"enabled": False}},
        })
        state["qa"] = {}
        refresh_stage(root, project, state)
        write_json(root / "project.json", project)
        write_json(root / "state.json", state)
    print(f"FINAL={final}")
    print(f"TARGET_DURATION_MS={target_duration_ms}")
    print(f"TARGET_FRAMES={target_frames}")


def run_final_qa(root: Path) -> dict[str, Any]:
    project, state = load_project(root)
    require_render_receipt(root, state)
    if not state.get("final_current") or not (root / "deliverables" / "final.mp4").is_file():
        raise WorkflowError("没有可验收的当前成片")
    skill_root = renderer_root()
    python = renderer_python(skill_root)
    result = subprocess.run([
        str(python), str(Path(__file__).with_name("qa_final.py")),
        "--project", str(root),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    report_path = root / "deliverables" / "qa-report.json"
    if result.returncode not in (0, 1) or not report_path.is_file():
        raise WorkflowError(f"成片 QA 无法完成，退出码 {result.returncode}；请查看 qa-report.json")
    report = read_json(report_path)
    state["qa"] = {
        "automated_ok": bool(report.get("ok")),
        "manual_approved": False,
        "final_sha256": state.get("final_sha256"),
        "report": str(report_path.relative_to(root)).replace("\\", "/"),
        "report_sha256": digest(report_path),
        "at": now(),
    }
    refresh_stage(root, project, state)
    write_json(root / "state.json", state)
    print(f"QA_REPORT={report_path}")
    print(f"QA_CONTACT_SHEET={report.get('visual_review', {}).get('contact_sheet', '')}")
    return report


def accept_final_qa(root: Path, summary: str) -> None:
    project, state = load_project(root)
    require_render_receipt(root, state)
    stage = refresh_stage(root, project, state)
    if stage != "await-final-qa":
        raise WorkflowError(f"当前不能确认成片 QA，状态是 {stage}")
    report_path = root / "deliverables" / "qa-report.json"
    report = read_json(report_path)
    if report.get("version") != EXPECTED_QA_REPORT_VERSION:
        raise WorkflowError("QA 报告版本已过期，请先重新运行 qa")
    final = root / "deliverables" / "final.mp4"
    final_hash = digest(final)
    if not report.get("ok"):
        raise WorkflowError("自动 QA 未通过，不能接受成片")
    if report.get("final", {}).get("sha256") != final_hash or final_hash != state.get("final_sha256"):
        raise WorkflowError("QA 报告与当前 final.mp4 不匹配")
    if not summary.strip():
        raise WorkflowError("必须记录实际看图后的简短结论")
    report.setdefault("visual_review", {}).update({
        "accepted": True,
        "summary": summary.strip(),
        "accepted_at": now(),
    })
    write_json(report_path, report)
    state["qa"] = {
        "automated_ok": True,
        "manual_approved": True,
        "final_sha256": final_hash,
        "report": str(report_path.relative_to(root)).replace("\\", "/"),
        "report_sha256": digest(report_path),
        "at": now(),
    }
    refresh_stage(root, project, state)
    write_json(root / "state.json", state)


def validate_project(root: Path) -> dict[str, Any]:
    project, state = load_project(root)
    stage = refresh_stage(root, project, state)
    report: dict[str, Any] = {"ok": True, "stage": stage, "errors": [], "warnings": [], "checked_at": now()}
    try:
        verify_artifacts_current(root, state)
        scenes = validate_storyboard(
            read_json(root / "storyboard.json"),
            require_v2=project.get("version", 1) >= 2,
            require_v3=project.get("version", 1) >= 3,
        )
        validate_words(read_json(root / "audio" / "words.json"))
        visual_plan_path = root / "visual-plan.json"
        if visual_plan_path.is_file():
            validate_visual_plan(
                read_json(visual_plan_path),
                read_json(root / "audio" / "words.json").get("words", []),
            )
        for scene in scenes:
            record = state.get("boards", {}).get(scene["id"])
            if not record:
                report["warnings"].append(f"{scene['id']} 尚无整板图")
                continue
            validate_annotation(read_json(root / record["annotation"]), scene)
        animation_path = root / "animation-plan.json"
        if animation_path.is_file():
            require_effective_plan(root, project, state)
            annotation_data = {
                scene["id"]: read_json(root / state["boards"][scene["id"]]["annotation"])
                for scene in scenes
                if scene["id"] in state.get("boards", {})
            }
            report["animation_plan_errors"] = validate_animation_plan(
                read_json(animation_path), annotation_data
            )
            report["errors"].extend(report["animation_plan_errors"])
    except (WorkflowError, VisualPlanError) as exc:
        report["errors"].append(str(exc))

    final = root / "deliverables" / "final.mp4"
    if final.is_file():
        try:
            require_render_receipt(root, state)
        except WorkflowError as exc:
            report["errors"].append(str(exc))
        report["final"] = {"path": str(final), "bytes": final.stat().st_size, "sha256": digest(final)}
        runtime = local_runtime_info()
        ffprobe = (runtime.get("ffprobe") or {}).get("path")
        if ffprobe:
            result = subprocess.run([
                ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height,r_frame_rate",
                "-of", "json", str(final),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode == 0:
                report["ffprobe"] = json.loads(result.stdout)
            else:
                report["errors"].append("ffprobe 无法读取最终视频")
        elif (root / "deliverables" / "qa-report.json").is_file():
            qa_report = read_json(root / "deliverables" / "qa-report.json")
            if qa_report.get("final", {}).get("sha256") == report["final"]["sha256"]:
                report["media"] = qa_report.get("media", {})
            else:
                report["warnings"].append("qa-report.json 不属于当前 final.mp4")
        else:
            try:
                import av
                with av.open(str(final)) as container:
                    report["media"] = {
                        "duration_sec": round(float(container.duration or 0) / 1_000_000, 3),
                        "streams": [
                            {
                                "type": stream.type,
                                "codec": stream.codec_context.name,
                                "width": getattr(stream.codec_context, "width", None),
                                "height": getattr(stream.codec_context, "height", None),
                            }
                            for stream in container.streams
                        ],
                    }
            except Exception as exc:
                report["warnings"].append(f"没有 ffprobe，PyAV 检查也失败：{exc}")
    elif state.get("final_current"):
        report["errors"].append("状态声称成片有效，但 final.mp4 不存在")
    report["ok"] = not report["errors"]
    write_json(root / "deliverables" / "validation.json", report)
    return report


def status(root: Path) -> dict[str, Any]:
    project, state = load_project(root)
    refresh_stage(root, project, state)
    write_json(root / "state.json", state)
    next_actions = {
        "prepare-script": "形成最终口播稿并推荐项目风格",
        "await-script-style-approval": "向用户展示最终口播稿和风格，等待第一次确认",
        "prepare-script-voice": "根据已确认口播稿生成配音、逐词时间、字幕、分镜和视觉编排",
        "await-script-voice-approval": "向用户展示试听、字幕、分镜和视觉编排，等待第二次确认",
        "prepare-boards": "为每幕生成整板图、语义标注和编号预览",
        "await-boards-approval": "向用户展示整板图与绘制顺序，等待第三次确认",
        "ready-to-render": "渲染场景并合成最终视频",
        "fix-required": "根据自动 QA 的失败层修复输入或渲染选项，再增量重渲染",
        "await-final-qa": "生成逐元素抽帧并实际看图，完成内部成片验收",
        "complete": "交付 final.mp4、qa-report.json 和 validation.json",
    }
    next_action = next_actions[state["stage"]]
    if state["stage"] == "prepare-script" and state.get("source_extract"):
        next_action = "审核 source 提取稿，改写最终口播并推荐风格；登记后等待第一次确认"
    allowed_commands = {
        "prepare-script": ["stage-script"],
        "await-script-style-approval": ["confirmation", "approve script-style", "stage-script"],
        "prepare-script-voice": ["tts", "stage-script-voice"],
        "await-script-voice-approval": ["confirmation", "approve script-voice", "stage-script-voice"],
        "prepare-boards": ["annotation-template", "add-board", "rebuild-plan", "panel", "pace-annotations"],
        "await-boards-approval": ["confirmation", "panel", "preview", "approve boards", "pace-annotations"],
        "ready-to-render": ["render"],
        "fix-required": ["render", "panel", "rebuild-plan"],
        "await-final-qa": ["qa", "accept-qa"],
        "complete": ["validate"],
    }
    next_command = {
        "prepare-script": "stage-script",
        "await-script-style-approval": "confirmation",
        "prepare-script-voice": "tts",
        "await-script-voice-approval": "confirmation",
        "prepare-boards": "add-board",
        "await-boards-approval": "confirmation",
        "ready-to-render": "render",
        "fix-required": "status",
        "await-final-qa": "qa",
        "complete": "validate",
    }[state["stage"]]
    if state["stage"] == "prepare-boards":
        missing_boards = [
            str(scene["id"])
            for scene in project.get("scenes", [])
            if str(scene["id"]) not in state.get("boards", {})
        ]
        next_command = f"add-board --scene-id {missing_boards[0]}" if missing_boards else "rebuild-plan"
    elif state["stage"] == "await-final-qa" and state.get("qa", {}).get("automated_ok") is True:
        next_command = "accept-qa"
    return {
        "project": project["title"],
        "stage": state["stage"],
        "next_action": next_action,
        "next_command": next_command,
        "allowed_commands": allowed_commands[state["stage"]],
        "requires_user_confirmation": state["stage"] in {
            "await-script-style-approval", "await-script-voice-approval", "await-boards-approval"
        },
        "style": project.get("style_selection", {"mode": "legacy", "style_id": project.get("style_id")}),
        "voice": project.get("voice_selection", {"provider": "edge", "mode": "default"}),
    }


def confirmation_bundle(root: Path) -> dict[str, Any]:
    """Build the deterministic handoff for the next user confirmation gate."""

    project, state = load_project(root)
    stage = refresh_stage(root, project, state)
    artifacts = state.get("artifacts", {}) if isinstance(state.get("artifacts"), dict) else {}
    style_profile = resolve_style(str(project.get("style_id") or project.get("style") or "warm-pencil"))
    style_registry = load_style_registry()
    reference_relative = str(style_profile.get("reference_image") or "")
    reference_path = Path(__file__).parents[1] / reference_relative if reference_relative else None
    characters_by_id = {
        str(character.get("id")): character
        for character in project.get("characters", [])
        if isinstance(character, dict) and str(character.get("id", "")).strip()
    }

    def character_references(scene: dict[str, Any]) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        for character_id in scene.get("character_ids", []):
            character = characters_by_id.get(str(character_id))
            if not character:
                continue
            declared = str(character.get("reference") or "").strip()
            candidate = Path(declared) if declared else None
            if candidate is not None and not candidate.is_absolute():
                candidate = root / candidate
            references.append({
                "id": str(character_id),
                "name": str(character.get("name") or character_id),
                "reference_image": str(candidate.resolve()) if candidate and candidate.is_file() else None,
                "declared_reference": declared or None,
            })
        return references

    def artifact_path(key: str) -> str | None:
        record = artifacts.get(key)
        if not isinstance(record, dict) or not record.get("path"):
            return None
        return str((root / str(record["path"])).resolve())

    bundle: dict[str, Any] = {
        "project": project.get("title"),
        "project_dir": str(root),
        "stage": stage,
        "requires_user_confirmation": stage in {
            "await-script-style-approval",
            "await-script-voice-approval",
            "await-boards-approval",
        },
    }
    if stage == "await-script-style-approval":
        bundle.update({
            "gate": "script-style",
            "approval_command": "approve script-style",
            "next_stage_after_approval": "prepare-script-voice",
            "instruction": "展示完整口播稿、推荐风格和理由；等待用户明确确认，不生成配音。",
            "files": {"script": artifact_path("script")},
            "style": project.get("style_selection", {
                "style_id": project.get("style_id"),
                "style_name": project.get("style"),
            }),
        })
    elif stage == "await-script-voice-approval":
        visual_path = root / "visual-plan.json"
        visual = read_json(visual_path) if visual_path.is_file() else {"summary": {}, "shots": []}
        visual_by_scene = {
            str(shot.get("section_id")): shot
            for shot in visual.get("shots", [])
            if isinstance(shot, dict) and shot.get("section_id")
        }
        safe_frame = style_registry.get("visual_safe_frame", {})
        generation_requests = []
        for scene in project.get("scenes", []):
            scene_characters = character_references(scene)
            element_labels = [
                str(item.get("label", ""))
                for item in scene.get("elements", [])
                if isinstance(item, dict) and str(item.get("label", "")).strip()
            ]
            reference_images = [
                str(reference_path.resolve())
                for _ in [0]
                if reference_path and reference_path.is_file()
            ] + [
                item["reference_image"]
                for item in scene_characters
                if item.get("reference_image")
            ]
            generation_requests.append({
                "scene_id": scene.get("id"),
                "prompt_sections": [
                    {
                        "kind": "scene-semantics",
                        "narration": scene.get("narration", ""),
                        "visible_elements": element_labels,
                    },
                    {
                        "kind": "composition-and-characters",
                        "composition": scene.get("composition"),
                        "layout_plan": visual_by_scene.get(str(scene.get("id")), {}).get("layout_plan", {}),
                        "character_references": scene_characters,
                    },
                    {
                        "kind": "style-reference-and-contract",
                        "style_id": style_profile.get("id"),
                        "reference_image": str(reference_path.resolve()) if reference_path and reference_path.is_file() else None,
                        "visual_contract": style_profile.get("visual_contract", {}),
                        "style_prompt": style_profile.get("prompt", {}),
                    },
                    {
                        "kind": "safe-frame-and-forbidden-items",
                        "visual_safe_frame": safe_frame,
                        "constraints": [
                            "16:9 single complete board",
                            "each listed semantic object occupies its own complete spatial unit",
                            "fit all content inside safe frame without cropping",
                            "no text, number, logo, or subtitle baked into the board",
                            "no arrows, relationship lines, divider lines, decorative frames, or visible grids",
                        ],
                    },
                ],
                "reference_images": reference_images,
            })
        bundle.update({
            "gate": "script-voice",
            "approval_command": "approve script-voice",
            "next_stage_after_approval": "prepare-boards",
            "instruction": (
                f"展示口播稿、{(project.get('voice_selection') or {}).get('provider', 'edge')} 试听、"
                "字幕、分镜、每幕文字卡片和视觉编排；等待用户确认，不生成整板图。"
            ),
            "voice": project.get("voice_selection", {"provider": "edge"}),
            "files": {
                "script": artifact_path("script"),
                "audio": artifact_path("audio"),
                "captions": artifact_path("captions"),
                "storyboard": artifact_path("storyboard"),
                "visual_plan": artifact_path("visual_plan"),
                "visual_plan_markdown": artifact_path("visual_plan_markdown"),
            },
            "scenes": [
                {
                    "id": scene.get("id"),
                    "title": scene.get("title", ""),
                    "narration": scene.get("narration", ""),
                    "time_ms": [scene.get("start_ms"), scene.get("end_ms")],
                    "composition": scene.get("composition"),
                    "title_card": copy.deepcopy(scene.get("title_card"))
                    if isinstance(scene.get("title_card"), dict)
                    else None,
                    "elements": [item.get("label", "") for item in scene.get("elements", []) if isinstance(item, dict)],
                }
                for scene in project.get("scenes", [])
            ],
            "visual_summary": visual.get("summary", {}),
            "board_generation_style": {
                "style_id": style_profile.get("id"),
                "style_name": style_profile.get("name"),
                "reference_image": str(reference_path.resolve()) if reference_path and reference_path.is_file() else None,
                "prompt": style_profile.get("prompt", {}),
                "visual_contract": style_profile.get("visual_contract", {}),
                "assembly_order": [
                    "scene narration and 1-6 adaptive semantic elements",
                    "scene composition and character reference if present",
                    "style reference image and style visual contract",
                    "safe frame, no text, no crop, and other board constraints",
                ],
            },
            "board_generation_requests": generation_requests,
        })
    elif stage == "await-boards-approval":
        boards = state.get("boards", {}) if isinstance(state.get("boards"), dict) else {}
        ownership_report = check_boards(root)
        ownership_by_scene = {
            str(item.get("scene_id") or item.get("scene")): item
            for item in ownership_report.get("scenes", [])
            if isinstance(item, dict)
        }
        bundle.update({
            "gate": "boards",
            "approval_command": "approve boards",
            "next_stage_after_approval": "ready-to-render",
            "instruction": (
                "必须直接展示每幕整板图、实际绘制预览和风险诊断图；启动工作台后等待用户保存或确认。"
                "用户看到提醒后确认渲染，即接受当前像素分配，视觉提醒不得再次阻止执行。"
            ),
            "decision_contract": {
                "visual_warnings_are_advisory": True,
                "explicit_confirmation_authorizes_render": True,
                "only_technical_impossibility_can_fail_after_confirmation": True,
            },
            "boards": [
                {
                    "scene_id": scene_id,
                    "image": str((root / record["image"]).resolve()),
                    "annotation": str((root / record["annotation"]).resolve()),
                    "preview": str((root / "previews" / f"{scene_id}-preview.png").resolve()),
                    "diagnostic": str((root / "previews" / f"ownership-{scene_id}.png").resolve())
                    if (root / "previews" / f"ownership-{scene_id}.png").is_file() else None,
                    "visual_risks": ownership_by_scene.get(scene_id, {}).get("visual_risks", []),
                    "warnings": ownership_by_scene.get(scene_id, {}).get("warnings", []),
                }
                for scene_id, record in sorted(boards.items())
                if isinstance(record, dict) and record.get("image") and record.get("annotation")
            ],
        })
    else:
        bundle.update({"gate": None, "instruction": status(root)["next_action"]})
    return bundle




@serialized_project()
def apply_panel(root: Path, changes_path: Path, dry_run: bool = False) -> dict[str, Any]:
    """Atomically validate, backup, and apply a panel-change-set.json to the project."""
    changes_file = Path(changes_path).expanduser().resolve()
    if not changes_file.is_file():
        raise WorkflowError(f"变更集文件不存在：{changes_path}")
    changes = read_json(changes_file)

    version = changes.get("version", 1)
    if version != 1:
        raise WorkflowError(f"不支持的变更集版本：{version}")

    files_to_change = changes.get("files")
    if not isinstance(files_to_change, list) or not files_to_change:
        raise WorkflowError("变更集中没有包含要修改的文件列表 (files)")

    project, state = load_project(root)

    # 1. Project Fingerprint verification. The fingerprint is mandatory so a
    # hand-written or stale change set can never bypass project binding.
    storyboard = read_json(root / "storyboard.json") if (root / "storyboard.json").is_file() else {"scenes": []}
    current_fingerprint = project_fingerprint(root, project)

    provided_fingerprint = changes.get("projectFingerprint")
    if not isinstance(provided_fingerprint, str) or not provided_fingerprint.strip():
        raise WorkflowError("变更集缺少有效的 projectFingerprint，无法确认目标项目")
    conflict = provided_fingerprint != current_fingerprint

    # 2. Reject duplicate file entries & Check allowed whitelist paths
    seen_paths: set[str] = set()
    verified_targets: list[dict[str, Any]] = []

    for item in files_to_change:
        rel_path = item.get("path")
        if not rel_path or not isinstance(rel_path, str):
            raise WorkflowError("变更集条目缺少有效的 path 字段")
        norm_rel = rel_path.replace("\\", "/")

        if norm_rel in seen_paths:
            raise WorkflowError(f"变更集包含重复文件条目：{norm_rel}")
        seen_paths.add(norm_rel)

        # The panel may submit its producer-generated storyboard element
        # synchronization, but never script or audio-derived timing edits.
        is_allowed = (
            norm_rel in ("project.json", "storyboard.json", "animation-plan.json")
            or (norm_rel.startswith("annotations/") and norm_rel.endswith(".annotation.json"))
        )
        if not is_allowed:
            raise WorkflowError(f"禁止通过变更集修改受保护或非白名单文件：{norm_rel}")

        target = (root / norm_rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise WorkflowError(f"非法目标路径（超出项目目录）：{norm_rel}")

        expected_sha = item.get("beforeSha256")
        if target.is_file():
            if expected_sha is None or expected_sha == "":
                raise WorkflowError(f"已存在文件缺少 beforeSha256：{norm_rel}")
            actual_sha = digest(target)
            if actual_sha != expected_sha:
                conflict = True
        elif expected_sha is not None:
            conflict = True

        after_content = item.get("afterContent")
        if after_content is None:
            raise WorkflowError(f"变更集条目缺少 afterContent 字段：{norm_rel}")

        verified_targets.append({
            "rel_path": norm_rel,
            "target_path": target,
            "after_content": after_content,
            "summary": item.get("changesSummary", []),
        })

    if conflict:
        # Both versions survive. A conflict copy is a successful save, not an apply.
        conflict_file = root / "panel-conflicts" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        if not dry_run:
            write_json(conflict_file, changes)
        return {"success": True, "conflict": True, "applied_files": [],
                "saved_copy": str(conflict_file.relative_to(root)),
                "status": "编辑已另存冲突副本，未覆盖磁盘新版本"}

    # 3. Semantic & schema validation of modified contents
    words_file = root / "audio" / "words.json"
    words = read_json(words_file) if words_file.is_file() else {}

    style_registry = load_style_registry()
    valid_style_ids = {s["id"] for s in style_registry.get("styles", [])} | {"custom"}

    style_changed = False
    renderer_profile_changed = False
    storyboard_changed = False
    storyboard_semantic_change = False
    storyboard_semantic_change_summary = {
        "changed": False,
        "before_count": 0,
        "after_count": 0,
        "scenes": [],
    }
    annotations_changed: list[str] = []
    animation_plan_changed = False
    animation_scenes_changed: list[str] = []
    current_animation_plan = (
        read_json(root / "animation-plan.json")
        if (root / "animation-plan.json").is_file()
        else {"version": VERSION, "scenes": []}
    )

    prospective_storyboard = next(
        (item["after_content"] for item in verified_targets if item["rel_path"] == "storyboard.json"),
        storyboard,
    )
    if any(item["rel_path"] == "storyboard.json" for item in verified_targets):
        if not isinstance(prospective_storyboard, dict):
            raise WorkflowError("storyboard.json 内容必须是 JSON 对象")
        validate_panel_storyboard(prospective_storyboard, storyboard, project)
        storyboard_changed = json.dumps(prospective_storyboard, ensure_ascii=False, sort_keys=True) != json.dumps(storyboard, ensure_ascii=False, sort_keys=True)
        storyboard_semantic_change_summary = storyboard_semantic_diff(storyboard, prospective_storyboard)
        storyboard_semantic_change = bool(storyboard_semantic_change_summary["changed"])

    for item in verified_targets:
        rel = item["rel_path"]
        content = item["after_content"]

        if rel == "project.json":
            if not isinstance(content, dict):
                raise WorkflowError("project.json 内容必须是 JSON 对象")
            for req_key in ("title", "topic", "style"):
                if req_key not in content:
                    raise WorkflowError(f"project.json 缺少必要字段：{req_key}")
            new_style_id = content.get("style_id")
            if new_style_id and new_style_id not in valid_style_ids:
                raise WorkflowError(f"未知的 style_id：{new_style_id}")
            if content.get("style_id") != project.get("style_id") or content.get("style") != project.get("style"):
                style_changed = True
            if content.get("renderer_profile") != project.get("renderer_profile"):
                renderer_profile_changed = True
            editable = {"title", "topic", "style", "style_id", "renderer_profile"}
            if any(content.get(key) != project.get(key) for key in (set(content) | set(project)) - editable):
                raise WorkflowError("工作台只能修改标题、主题、风格和 renderer_profile；口播时间轴必须通过 stage-script-voice 重建")

        elif rel == "storyboard.json":
            continue

        elif rel.startswith("annotations/") and rel.endswith(".annotation.json"):
            if not isinstance(content, dict):
                raise WorkflowError(f"{rel} 内容必须是 JSON 对象")
            scene_id = Path(rel).stem.replace(".annotation", "")
            scene = next((s for s in prospective_storyboard.get("scenes", []) if s["id"] == scene_id), None)
            if scene is None:
                raise WorkflowError(f"变更集标注不属于当前 storyboard 场景：{scene_id}")
            declared_scene_id = str(content.get("sceneId") or "").strip()
            if declared_scene_id and declared_scene_id != scene_id:
                raise WorkflowError(f"{rel} 的 sceneId 与文件名不一致")
            if not isinstance(content.get("elements"), list):
                raise WorkflowError(f"{rel} 的 elements 必须为列表")
            # Saving preserves geometry and incomplete semantic edits verbatim.
            # Production validation belongs to preview/approval/render.
            annotations_changed.append(scene_id)

        elif rel == "animation-plan.json":
            if not isinstance(content, dict):
                raise WorkflowError("animation-plan.json 内容必须是 JSON 对象")
            animation_plan_changed = True
            animation_scenes_changed = animation_scene_ids_changed(current_animation_plan, content)

    # 4. Dry-run reporting
    if dry_run:
        return {
            "dry_run": True,
            "project": str(root),
            "files_count": len(verified_targets),
            "modified_files": [t["rel_path"] for t in verified_targets],
            "invalidation_predicted": {
                "style_changed": style_changed,
                "renderer_profile_changed": renderer_profile_changed,
                "annotations_changed": annotations_changed,
                "animation_plan_changed": animation_plan_changed,
                "animation_scenes_changed": animation_scenes_changed,
                "storyboard_changed": storyboard_changed,
                "storyboard_semantic_change": storyboard_semantic_change,
                "storyboard_semantic_diff": storyboard_semantic_change_summary,
                "production_next_action": "rebuild-plan",
                "semantic_sync_pending": bool(state.get("semantic_sync_pending")),
                "board_reapproval_required": (
                    style_changed or renderer_profile_changed or storyboard_changed
                    or bool(annotations_changed) or animation_plan_changed
                ),
            },
            "status": "可保存编辑；生产可用性尚未检查",
        }

    # 5. Transactional Staging & Atomic Backup
    import tempfile
    temp_stage = Path(tempfile.mkdtemp(prefix="apply_panel_stage_"))
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = root / "panel-backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    created_targets = [
        item["target_path"] for item in verified_targets if not item["target_path"].exists()
    ]

    try:
        # Write candidate files in staging folder
        for item in verified_targets:
            stage_target = temp_stage / item["rel_path"]
            stage_target.parent.mkdir(parents=True, exist_ok=True)
            content = item["after_content"]
            if isinstance(content, str):
                stage_target.write_text(content, encoding="utf-8")
            else:
                stage_target.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Back up all modified targets + project.json + state.json
        backup_files = [t["target_path"] for t in verified_targets if t["target_path"].is_file()]
        for p in (root / "project.json", root / "state.json"):
            if p.is_file() and p not in backup_files:
                backup_files.append(p)
        if storyboard_changed:
            # Storyboard edits also regenerate the derived visual plan below.
            for p in (root / "visual-plan.json", root / "visual-plan.md"):
                if p.is_file() and p not in backup_files:
                    backup_files.append(p)
        generated_plan_target = root / "animation-plan.json"
        should_refresh_generated_plan = bool(storyboard_changed or annotations_changed or renderer_profile_changed) and not animation_plan_changed
        if should_refresh_generated_plan:
            if generated_plan_target.is_file() and generated_plan_target not in backup_files:
                backup_files.append(generated_plan_target)
            elif generated_plan_target not in created_targets:
                created_targets.append(generated_plan_target)

        for src_file in backup_files:
            rel = src_file.relative_to(root)
            dest = backup_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)

        # 6. Atomic Commit: Copy staged files over targets
        for item in verified_targets:
            staged = temp_stage / item["rel_path"]
            dest = item["target_path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp_dest = dest.with_name(f".{dest.stem}.tmp-{os.getpid()}{dest.suffix}")
            shutil.copy2(staged, temp_dest)
            temp_dest.replace(dest)

        # 7. Accurate State Invalidation
        project, state = load_project(root)

        if style_changed:
            state["approvals"]["script_style"] = {"approved": False, "at": None}
            state["approvals"]["script_voice"] = {"approved": False, "at": None}
            state["approvals"]["boards"] = {"approved": False, "at": None}
            state["render_cache"] = {}
            state["final_current"] = False
            state["final_sha256"] = None
            state["qa"] = {}
        elif renderer_profile_changed:
            state["approvals"]["boards"] = {"approved": False, "at": None}
            state["render_cache"] = {}
            state["final_current"] = False
            state["final_sha256"] = None
            state["qa"] = {}

        if storyboard_changed:
            # Keep the editable storyboard. Synchronize derived production data
            # only when explicitly preparing; incomplete scenes must save.
            state["semantic_sync_pending"] = True
            state["approvals"]["boards"] = {"approved": False, "at": None}
            state["render_cache"] = {}
            state["final_current"] = False
            state["final_sha256"] = None
            state["qa"] = {}

        if annotations_changed:
            state["approvals"]["boards"] = {"approved": False, "at": None}
            for sid in annotations_changed:
                ann_file = root / "annotations" / f"{sid}.annotation.json"
                if sid in state.get("boards", {}):
                    state["boards"][sid]["annotation_sha256"] = digest(ann_file)
                state.setdefault("render_cache", {}).pop(sid, None)
            state["final_current"] = False
            state["final_sha256"] = None
            state["qa"] = {}

        if animation_plan_changed:
            anim_path = root / "animation-plan.json"
            saved_plan = read_json(anim_path)
            saved_plan["inputFingerprint"] = plan_input_fingerprint(root, project, state)
            write_json(anim_path, saved_plan)
            state["approvals"]["boards"] = {"approved": False, "at": None}
            state.setdefault("artifacts", {})["animation_plan"] = {
                "path": "animation-plan.json", "sha256": digest(anim_path),
                "input_fingerprint": saved_plan["inputFingerprint"], "origin": "panel",
            }
            cache = state.setdefault("render_cache", {})
            for scene_id in animation_scenes_changed:
                cache.pop(scene_id, None)
            state["final_current"] = False
            state["final_sha256"] = None
            state["qa"] = {}

        if should_refresh_generated_plan or animation_plan_changed:
            state.setdefault("artifacts", {}).setdefault("animation_plan", {})["stale_reason"] = "编辑已保存，需显式准备并确认执行计划"
        if storyboard_semantic_change:
            state["approvals"]["script_voice"] = {"approved": False, "at": None}
        state["editor_status"] = "saved"
        state["production_status"] = "needs-review"
        state["updated_at"] = now()
        # Do not run production validation inside a persistence transaction.
        new_stage = "await-script-voice-approval" if storyboard_semantic_change else "await-boards-approval"
        state["stage"] = new_stage
        write_json(root / "project.json", project)
        write_json(root / "state.json", state)

    except Exception as exc:
        # Automatic Rollback on any failure
        for backed_file in backup_dir.rglob("*"):
            if backed_file.is_file():
                rel = backed_file.relative_to(backup_dir)
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backed_file, dest)
        # A newly-created target has no backup file, so it must be removed to
        # restore the exact pre-transaction filesystem state.
        for created_target in created_targets:
            if created_target.is_file():
                created_target.unlink()
        raise WorkflowError(f"应用变更集失败，已自动回滚全部文件：{exc}") from exc
    finally:
        shutil.rmtree(temp_stage, ignore_errors=True)

    return {
        "success": True,
        "project": str(root),
        "backup_dir": str(backup_dir.relative_to(root)).replace("\\", "/"),
        "applied_files": [t["rel_path"] for t in verified_targets],
        "stage": new_stage,
        "style_changed": style_changed,
        "storyboard_changed": storyboard_changed,
        "storyboard_semantic_change": storyboard_semantic_change,
        "storyboard_semantic_diff": storyboard_semantic_change_summary,
        "annotations_changed": annotations_changed,
        "animation_scenes_changed": animation_scenes_changed,
        "board_reapproval_required": style_changed or storyboard_changed or bool(annotations_changed),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="手绘讲解视频项目工作流")
    sub = p.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-boards", help="只读检查已登记板图，逐幕汇总像素归属并输出诊断图，不重建计划")
    check.add_argument("--project", required=True)
    init = sub.add_parser("init")
    init.add_argument("--project", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--topic", required=True)
    init.add_argument("--duration-sec", type=int, default=60)
    init.add_argument("--style", default="auto", help="默认 auto 自动推荐；也可显式传入十种 style id")

    styles = sub.add_parser("styles")
    styles.add_argument("--topic", default="", help="可选：根据题材输出自动推荐")
    styles.add_argument("--title", default="")

    tts = sub.add_parser("tts", help="根据第一次确认后的口播稿生成默认或显式选择的配音")
    tts.add_argument("--project", required=True)
    tts.add_argument("--script", required=True)
    tts.add_argument("--display-script")
    tts.add_argument("--out-dir", required=True)
    tts.add_argument(
        "--provider",
        choices=("edge", "piper", "azure", "elevenlabs", "sherpa", "voicestudio"),
        default="edge",
    )
    tts.add_argument("--voice")
    tts.add_argument("--voice-id")
    tts.add_argument("--rate", default="+12%")
    tts.add_argument("--volume", default="+0%")
    tts.add_argument("--pitch", default="+0Hz")
    tts.add_argument("--piper-model")
    tts.add_argument("--piper-data-dir")
    tts.add_argument("--model")
    tts.add_argument("--model-dir")
    tts.add_argument("--output-format", default="mp3_44100_128")
    tts.add_argument("--base-url")
    tts.add_argument("--timeout", type=float, default=1200.0)
    tts.add_argument("--language", default="zh")
    tts.add_argument("--speed", type=float, default=1.0)
    tts.add_argument("--threads", type=int, default=4)
    tts.add_argument("--instruct", default="")
    tts.add_argument("--seed", type=int, default=42)
    tts.add_argument("--max-chars", type=int, default=18)
    tts.add_argument("--max-caption-ms", type=int, default=3600)
    tts.add_argument("--max-line-chars", type=int, default=14)

    align_voice = sub.add_parser(
        "align-voice",
        help="为 Piper、Sherpa 或 VoiceStudio 音频生成脚本对齐的逐词时间和字幕",
    )
    align_voice.add_argument("--project", required=True)
    align_voice.add_argument("--audio", required=True)
    align_voice.add_argument("--script", required=True)
    align_voice.add_argument("--out-dir", required=True)
    align_voice.add_argument("--model", default="small")
    align_voice.add_argument("--language", default="zh")
    align_voice.add_argument("--device", default="cpu")
    align_voice.add_argument("--compute-type", default="int8")
    align_voice.add_argument("--max-chars", type=int, default=18)
    align_voice.add_argument("--max-caption-ms", type=int, default=3600)
    align_voice.add_argument("--max-line-chars", type=int, default=14)
    align_voice.add_argument("--min-script-match", type=float, default=0.78)

    extract = sub.add_parser("extract-source", help="按用户明确选择的范围提取参考文案，或追加完整视频分析")
    extract.add_argument("--project", required=True)
    extract.add_argument("--input", required=True, help="视频链接或本地音视频文件")
    extract.add_argument(
        "--reference-scope",
        required=True,
        choices=("transcript", "full"),
        help="transcript=只参考文案口播；full=同时分析整条视频的镜头、画面、节奏和转场",
    )
    extract.add_argument("--max-keyframes", type=int, default=20, help="完整分析最多保存的关键帧数量（1-60）")
    extract.add_argument(
        "--model",
        default="auto",
        choices=["auto", "tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"],
        help="auto=优先复用已缓存模型；没有缓存时首次使用 small。其他值仅用于用户明确选择的型号",
    )
    extract.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    extract.add_argument(
        "--source-language",
        default="auto",
        help="源音频语言代码，例如 en、zh；auto 会读取字幕元数据或由 ASR 判断",
    )
    extract.add_argument("--force-asr", action="store_true", help="忽略原生字幕，强制本地识别并生成逐词时间")
    extract.add_argument("--asr-python", default="", help="显式指定装有 faster-whisper 的 Python")

    for name in ("status", "validate", "qa"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--project", required=True)

    confirmation = sub.add_parser("confirmation", help="输出当前确认关卡必须展示给用户的完整材料包")
    confirmation.add_argument("--project", required=True)
    review_source = sub.add_parser("review-source", help="登记完整参考视频的实际观看结论")
    review_source.add_argument("--project", required=True)
    review_source.add_argument("--review", required=True)
    template = sub.add_parser("annotation-template", help="按真实图片尺寸生成待填写语义标注草稿")
    template.add_argument("--project", required=True)
    template.add_argument("--scene-id", required=True)
    template.add_argument("--image", required=True)
    migrate = sub.add_parser("migrate-project", help="显式迁移旧身份字段，保留备份并撤销受影响批准")
    migrate.add_argument("--project", required=True)

    rebuild = sub.add_parser("rebuild-plan", help="检查上游后重建派生动画计划并使旧成片失效")
    rebuild.add_argument("--project", required=True)

    pace = sub.add_parser("pace-annotations", help="根据口播区间自适应拉长手绘时长，消除长静止")
    pace.add_argument("--project", required=True)
    pace.add_argument("--ratio", type=float, default=DEFAULT_PACING_RATIO, help="绘制时长占可用窗口的比例（默认 0.72）")
    pace.add_argument("--scene-id", help="可选仅调整指定场景")

    accept_qa = sub.add_parser("accept-qa")
    accept_qa.add_argument("--project", required=True)
    accept_qa.add_argument("--summary", required=True)

    prepare_voice = sub.add_parser("prepare-voice-text", help="由第一次确认后的展示文案和发音覆盖生成 TTS 专用文本")
    prepare_voice.add_argument("--project", required=True)
    prepare_voice.add_argument("--script", required=True)
    prepare_voice.add_argument("--overrides")
    prepare_voice.add_argument("--out", required=True)

    stage_script_parser = sub.add_parser("stage-script", help="登记待用户第一次确认的最终口播稿和风格")
    stage_script_parser.add_argument("--project", required=True)
    stage_script_parser.add_argument("--script", required=True)
    stage_script_parser.add_argument("--style", help="可选：覆盖 init 时的风格推荐")

    stage = sub.add_parser("stage-script-voice")
    stage.add_argument("--project", required=True)
    stage.add_argument("--script", required=True)
    stage.add_argument("--storyboard", required=True)
    stage.add_argument("--audio", required=True)
    stage.add_argument("--words", required=True)
    stage.add_argument("--captions", required=True)
    stage.add_argument("--tts-script", help="实际送入 TTS 的文本；使用发音覆盖时必填")
    stage.add_argument("--pronunciation-overrides", help="用户已确认的 pronunciation-overrides.json")
    stage.add_argument(
        "--provider",
        choices=("edge", "piper", "azure", "elevenlabs", "sherpa", "voicestudio"),
        default="edge",
    )
    stage.add_argument("--voice")
    stage.add_argument("--voice-id")
    stage.add_argument("--model")

    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("--project", required=True)
    approve_parser.add_argument("gate", choices=["script-style", "script-voice", "boards"])

    setup_parser = sub.add_parser("setup", help="按用户选择安装可选配音、提取或 FFmpeg 能力")
    setup_parser.add_argument(
        "--provider",
        choices=["piper", "azure", "elevenlabs", "sherpa", "voicestudio"],
        action="append",
        default=[],
    )
    setup_parser.add_argument("--upgrade", action="store_true", help="明确升级核心及所选可选依赖；不自动升级现有生产环境")
    setup_parser.add_argument("--extraction", action="store_true")
    setup_parser.add_argument(
        "--install-ffmpeg",
        action="store_true",
        help="只在用户明确同意安装 FFmpeg 后使用",
    )

    board = sub.add_parser("add-board")
    board.add_argument("--project", required=True)
    board.add_argument("--scene-id", required=True)
    board.add_argument("--image", required=True)
    board.add_argument("--annotation", required=True)

    preview_parser = sub.add_parser("preview")
    preview_parser.add_argument("--project", required=True)
    preview_parser.add_argument("--scene-id")

    panel_parser = sub.add_parser("panel", help="启动本地编排工作台并自动打开浏览器")
    panel_parser.add_argument("--project", required=True)
    panel_parser.add_argument("--host", default="127.0.0.1")
    panel_parser.add_argument("--port", type=int, default=0, help="默认自动选择可用端口")
    panel_parser.add_argument("--allow-lan", action="store_true", help="明确允许局域网访问工作台")
    panel_parser.add_argument("--no-open", action="store_true", help="仅启动服务，不自动打开浏览器")

    render_parser = sub.add_parser("render")
    render_parser.add_argument("--project", required=True)
    render_parser.add_argument("--fps", type=int, default=30)
    render_parser.add_argument("--cap-long-edge", type=int, default=1920)
    render_parser.add_argument("--jobs", type=int, default=2, help="并行场景数；默认 2，低内存设备可设为 1")
    render_parser.add_argument("--no-sfx", action="store_true", help="本次渲染禁用确定性 CC0 音效")
    hand = render_parser.add_mutually_exclusive_group()
    hand.add_argument("--small-hand", dest="hand_mode", action="store_const", const="small-hand")
    hand.add_argument("--presenter", dest="hand_mode", action="store_const", const="presenter")
    hand.add_argument("--no-hand", dest="hand_mode", action="store_const", const="no-hand")
    hand.add_argument("--full-hand", dest="hand_mode", action="store_const", const="full-hand")
    hand.add_argument("--bare-tip", dest="hand_mode", action="store_const", const="no-hand",
                      help="历史兼容别名：关闭手部覆盖")
    render_parser.set_defaults(hand_mode=None)

    bookend = sub.add_parser("bookend", help="使用 Presenter 资产包生成开场/结尾无声模板片段，不自动拼入成片")
    bookend.add_argument("--project", required=True)
    bookend.add_argument("--kind", choices=["intro", "outro", "both"], default="both")
    bookend.add_argument("--intro-title")
    bookend.add_argument("--outro-message", default="讲完啦，下次见！")
    bookend.add_argument("--fps", type=int, default=30)
    bookend.add_argument("--width", type=int, default=1920)
    bookend.add_argument("--height", type=int, default=1080)

    apply_panel_cmd = sub.add_parser("apply-panel", help="安全应用并备份编排控制台导出的变更集")
    apply_panel_cmd.add_argument("--project", required=True)
    apply_panel_cmd.add_argument("--changes", required=True, help="panel-change-set.json 路径")
    apply_panel_cmd.add_argument("--dry-run", action="store_true", help="演练模式，仅验证不修改文件")
    return p


def main(argv: list[str] | None = None) -> int:
    configure_cli_text_streams()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "styles":
            result = load_style_registry()
            if args.topic or args.title:
                result = {**result, "recommendation": recommend_style(args.topic, args.title)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "tts":
            return run_tts_command(canonical_project(args.project), args)
        if args.command == "align-voice":
            return run_alignment_command(canonical_project(args.project), args)
        if args.command == "setup":
            return prepare_optional_environment(args)
        if args.command == "prepare-voice-text":
            root = canonical_project(args.project)
            require_script_style_approval(root, Path(args.script).resolve())
            result = prepare_voice_text(
                Path(args.script).resolve(),
                Path(args.overrides).resolve() if args.overrides else None,
                Path(args.out).resolve(),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        root = canonical_project(args.project)
        if args.command == "init":
            init_project(root, args.title, args.topic, args.duration_sec, args.style)
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.command == "stage-script":
            stage_script(root, args.script, args.style)
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.command == "extract-source":
            print(json.dumps(extract_source(root, args), ensure_ascii=False, indent=2))
        elif args.command == "status":
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.command == "review-source":
            register_source_review(root, args.review)
            print("SOURCE_REVIEW=registered")
        elif args.command == "annotation-template":
            print(f"ANNOTATION_DRAFT={annotation_template(root, args.scene_id, args.image)}")
        elif args.command == "migrate-project":
            print(json.dumps(migrate_project(root), ensure_ascii=False))
        elif args.command == "rebuild-plan":
            rebuild_plan(root)
            print("ANIMATION_PLAN=rebuilt FINAL_CURRENT=false")
        elif args.command == "pace-annotations":
            report = pace_annotations_command(root, ratio=args.ratio, scene_id=args.scene_id)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        elif args.command == "confirmation":
            print(json.dumps(confirmation_bundle(root), ensure_ascii=False, indent=2))
        elif args.command == "stage-script-voice":
            stage_script_voice(root, args)
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.command == "approve":
            approve(root, args.gate)
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.command == "check-boards":
            result = check_boards(root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        elif args.command == "add-board":
            add_board(root, args.scene_id, args.image, args.annotation)
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.command == "preview":
            preview(root, args.scene_id)
        elif args.command == "panel":
            launch_panel(root, args.host, args.port, args.no_open, args.allow_lan)
        elif args.command == "render":
            render(
                root,
                args.fps,
                args.cap_long_edge,
                args.hand_mode,
                args.jobs,
                False if args.no_sfx else None,
            )
        elif args.command == "bookend":
            render_presenter_bookends(
                root,
                args.kind,
                args.intro_title,
                args.outro_message,
                args.fps,
                args.width,
                args.height,
            )
        elif args.command == "qa":
            report = run_final_qa(root)
            return 0 if report["ok"] else 1
        elif args.command == "accept-qa":
            accept_final_qa(root, args.summary)
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.command == "validate":
            report = validate_project(root)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
        elif args.command == "apply-panel":
            result = apply_panel(root, Path(args.changes), dry_run=args.dry_run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        return 0
    except WorkflowError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
