#!/usr/bin/env python3
"""Deterministic media QA and event contact-sheet generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

import av
import numpy as np
from PIL import Image, ImageDraw

try:
    from board_qa import run as run_board_qa
except ModuleNotFoundError:  # Package import in tests and embedded callers.
    from scripts.board_qa import run as run_board_qa

try:
    from animation_plan import validate_animation_plan
    from text_policy import (
        TextPolicyError,
        apply_pronunciation_overrides,
        caption_semantic_warnings,
        contains_punctuation,
        parse_srt_cues,
    )
except ImportError:  # pragma: no cover - package import for test runners
    from scripts.animation_plan import validate_animation_plan
    from scripts.text_policy import (
        TextPolicyError,
        apply_pronunciation_overrides,
        caption_semantic_warnings,
        contains_punctuation,
        parse_srt_cues,
    )


from pixel_contract import require_ownership, PIXEL_POLICY
from phase_budget import effective_annotation, PHASE_POLICY, MIN_COLOR_SWEEPS, MIN_COLOR_FRAMES

V31_GESTURES = {"gesture-arc", "gesture-underline", "gesture-bracket", "gesture-arrow"}
LEGACY_MECHANICAL_EFFECTS = {"pulse", "pulse/outline", "outline", "hand-drawn-circle", "circle", "focus-zoom"}
MAX_VISUAL_REVIEW_FRAMES = 48
V33_SAFE_EFFECTS = {"focus-push"}
V33_EFFECT_MIN_MS = {
    "focus-push": 1200,
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_srt(path: Path) -> list[dict[str, Any]]:
    try:
        return parse_srt_cues(path.read_text(encoding="utf-8-sig"))
    except TextPolicyError as exc:
        raise ValueError(str(exc)) from exc


def normalized_text(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).casefold()


def caption_semantic_metrics(cues: list[dict[str, Any]], script_text: str) -> dict[str, Any]:
    return {
        "script_text_match": normalized_text("".join(str(cue["text"]) for cue in cues)) == normalized_text(script_text),
        "single_line": all(int(cue.get("line_count", 1)) == 1 for cue in cues),
        "punctuation_free": all(not contains_punctuation(str(cue["text"])) for cue in cues),
        "warnings": caption_semantic_warnings(cues, script_text),
    }


def media_contract_checks(
    project: dict[str, Any],
    words_data: dict[str, Any],
    cues: list[dict[str, Any]],
    media: dict[str, Any],
    audio_duration_ms: int,
    tolerance_ms: int,
) -> dict[str, Any]:
    errors: list[str] = []
    bookends = project.get("bookends") if isinstance(project.get("bookends"), dict) else {}
    prefix_ms = max(0, int(bookends.get("prefix_duration_ms", 0) or 0))
    suffix_ms = max(0, int(bookends.get("suffix_duration_ms", 0) or 0))
    voice_window_ms = max(0, audio_duration_ms - prefix_ms - suffix_ms)
    width, height = media.get("resolution", [0, 0])
    if project.get("aspect_ratio") == "16:9" and (not width or not height or width * 9 != height * 16):
        errors.append(f"成片分辨率 {width}x{height} 不是精确 16:9")

    words = words_data.get("words")
    words_duration = words_data.get("duration_ms")
    last_word_end = 0
    if not isinstance(words, list) or not words:
        errors.append("audio/words.json 缺少非空 words 数组")
    else:
        try:
            last_word_end = max(int(word["end_ms"]) for word in words)
        except (KeyError, TypeError, ValueError):
            errors.append("audio/words.json 包含无效逐词时间")
    if not isinstance(words_duration, int) or words_duration <= 0:
        errors.append("audio/words.json 缺少有效 duration_ms")
        words_duration = 0
    if words_duration and last_word_end > words_duration:
        errors.append("words.json duration_ms 早于最后一个词")

    last_caption_end = max((int(cue["end_ms"]) for cue in cues), default=0)
    if last_word_end + prefix_ms > audio_duration_ms + tolerance_ms:
        errors.append("最后一个词超出最终音轨")
    if last_caption_end + prefix_ms > audio_duration_ms + tolerance_ms:
        errors.append("最后一条字幕超出最终音轨")
    if words_duration and abs(words_duration - voice_window_ms) > max(250, tolerance_ms):
        errors.append("words.json duration_ms 与正文音轨窗口时长不一致")
    if last_word_end and abs(last_caption_end - last_word_end) > max(250, tolerance_ms):
        errors.append("字幕末尾与逐词末尾不一致")
    return {
        "errors": errors,
        "metrics": {
            "words_duration_ms": words_duration,
            "last_word_end_ms": last_word_end,
            "last_caption_end_ms": last_caption_end,
            "audio_duration_ms": audio_duration_ms,
            "voice_window_ms": voice_window_ms,
            "bookend_prefix_ms": prefix_ms,
            "bookend_suffix_ms": suffix_ms,
            "exact_16_9": bool(width and height and width * 9 == height * 16),
        },
    }


def pronunciation_contract_checks(project_root: Path, script_text: str) -> dict[str, Any]:
    errors: list[str] = []
    overrides_path = project_root / "audio" / "pronunciation-overrides.json"
    tts_path = project_root / "audio" / "tts-script.txt"
    if not overrides_path.is_file():
        errors.append("缺少 audio/pronunciation-overrides.json")
        return {"errors": errors, "override_count": 0, "tts_text_match": False, "overrides": []}
    if not tts_path.is_file():
        errors.append("缺少 audio/tts-script.txt")
        return {"errors": errors, "override_count": 0, "tts_text_match": False, "overrides": []}
    try:
        overrides_data = read_json(overrides_path)
        expected_tts, applied = apply_pronunciation_overrides(script_text.strip(), overrides_data)
    except (ValueError, TextPolicyError) as exc:
        errors.append(f"发音覆盖记录无效：{exc}")
        return {"errors": errors, "override_count": 0, "tts_text_match": False, "overrides": []}
    actual_tts = tts_path.read_text(encoding="utf-8").strip()
    matches = actual_tts == expected_tts
    if not matches:
        errors.append("audio/tts-script.txt 与展示文案及发音覆盖推导结果不一致")
    return {
        "errors": errors,
        "override_count": len(applied),
        "tts_text_match": matches,
        "overrides": applied,
        "manual_audio_review_required": bool(applied),
    }


def stream_duration_ms(stream: av.stream.Stream) -> int | None:
    if stream.duration is not None and bool(stream.time_base):
        return round(float(stream.duration * stream.time_base) * 1000)
    return None


def decode_audio_metrics(path: Path) -> dict[str, Any]:
    total_samples = 0
    sum_squares = 0.0
    peak = 0.0
    sample_rate = 0
    channels = 0
    with av.open(str(path)) as container:
        streams = [stream for stream in container.streams if stream.type == "audio"]
        if not streams:
            raise ValueError("最终视频没有音轨")
        stream = streams[0]
        sample_rate = int(stream.codec_context.sample_rate or 0)
        channels = int(getattr(stream.codec_context, "channels", 0) or 0)
        for frame in container.decode(stream):
            array = frame.to_ndarray()
            if np.issubdtype(array.dtype, np.integer):
                maximum = float(max(abs(np.iinfo(array.dtype).min), np.iinfo(array.dtype).max))
                values = array.astype(np.float64) / maximum
            else:
                values = array.astype(np.float64)
            total_samples += values.size
            sum_squares += float(np.square(values).sum())
            if values.size:
                peak = max(peak, float(np.abs(values).max()))
    duration_ms = round(total_samples / max(1, sample_rate * max(1, channels)) * 1000)
    rms = math.sqrt(sum_squares / max(1, total_samples))
    return {
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "channels": channels,
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "non_silent": rms >= 0.0005,
    }


def build_targets(
    project: dict[str, Any],
    root: Path,
    cues: list[dict[str, Any]],
    duration_ms: int,
    prefix_ms: int = 0,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = [
        {"time_ms": 0, "kind": "first", "label": "first-frame"},
    ]
    plan_path = root / "animation-plan.json"
    plan = read_json(plan_path) if plan_path.is_file() else {"scenes": []}
    transition_by_scene = {
        str(item.get("sceneId")): item.get("transition")
        for item in plan.get("scenes", [])
        if isinstance(item, dict) and isinstance(item.get("transition"), dict)
    }
    for scene in project.get("scenes", []):
        scene_start = int(scene["start_ms"])
        scene_end = int(scene["end_ms"])
        annotation_path = root / "annotations" / f"{scene['id']}.annotation.json"
        if annotation_path.is_file():
            annotation = effective_annotation(read_json(annotation_path), str(scene["id"]), plan)
            elements = sorted(annotation.get("elements", []), key=lambda item: int(item.get("sequence", 0)))
            if elements:
                element = elements[-1]
                reveal = element.get("reveal", {})
                midpoint = int(reveal.get("startMs", 0)) + int(reveal.get("durationMs", 0)) // 2
                targets.append({
                    "time_ms": scene_start + midpoint,
                    "kind": "hand-drawn-mid",
                    "label": f"{scene['id']}-{element.get('sequence', 0)}-{element.get('id', 'element')}-mid",
                })
                element_end = int(reveal.get("startMs", 0)) + int(reveal.get("durationMs", 0))
                targets.append({
                    "time_ms": min(scene_end - 1, scene_start + element_end + 40),
                    "kind": "element-end",
                    "label": f"{scene['id']}-{element.get('sequence', 0)}-{element.get('id', 'element')}-complete",
                })
        transition = transition_by_scene.get(str(scene["id"]))
        stable_scene_end = (
            max(scene_start, int(transition.get("eraseStartMs", scene_end)) - 125)
            if transition else max(scene_start, scene_end - 80)
        )
        targets.append({
            "time_ms": stable_scene_end,
            "kind": "scene-end",
            "label": f"{scene['id']}-end",
        })
    if plan_path.is_file():
        representative_events: list[tuple[int, int, int, str]] = []
        for scene in plan.get("scenes", []):
            scene_start = int(scene.get("sceneStartMs", 0))
            for event in scene.get("events", []):
                start = scene_start + int(event.get("startMs", 0))
                end = scene_start + int(event.get("endMs", event.get("startMs", 0)))
                event_id = str(event.get("id", "animation"))
                duration = max(1, end - start)
                representative_events.append((duration, start, end, event_id))
            transition = scene.get("transition")
            if transition:
                erase_start = int(transition.get("eraseStartMs", 0))
                erase_end = int(transition.get("eraseEndMs", erase_start))
                next_word = int(transition.get("nextSceneFirstWordMs", erase_end))
                targets.extend([
                    {"time_ms": erase_start + max(1, (erase_end - erase_start) // 2), "kind": "eraser-mid", "label": f"{scene.get('sceneId', 'scene')}-eraser-mid"},
                    {"time_ms": next_word, "kind": "next-scene-first-word", "label": f"{transition.get('toSceneId', 'next')}-first-word"},
                ])
        if representative_events:
            duration, start, end, event_id = max(representative_events)
            targets.extend([
                {"time_ms": start, "kind": "animation-start", "label": f"{event_id}-start"},
                {"time_ms": start + duration // 2, "kind": "animation-mid", "label": f"{event_id}-mid"},
                {"time_ms": end, "kind": "animation-complete", "label": f"{event_id}-complete"},
            ])
    caption_indexes: list[int] = []
    if cues:
        caption_indexes = sorted({min(len(cues) - 1, len(cues) // 3), min(len(cues) - 1, (2 * len(cues)) // 3)})
    for cue_index in caption_indexes:
        cue = cues[cue_index]
        index = cue_index + 1
        targets.append({
            "time_ms": (cue["start_ms"] + cue["end_ms"]) // 2,
            "kind": "caption-mid",
            "label": f"caption-{index:02d}",
            "cue": index,
        })
    targets.append({"time_ms": max(0, duration_ms - 40), "kind": "last", "label": "last-frame"})
    if prefix_ms:
        for item in targets:
            item["time_ms"] = int(item["time_ms"]) + int(prefix_ms)
    ordered = sorted(targets, key=lambda item: (item["time_ms"], item["kind"], item["label"]))
    deduplicated: list[dict[str, Any]] = []
    for target in ordered:
        target["time_ms"] = max(0, min(duration_ms - 1, int(target["time_ms"])))
        if deduplicated and abs(target["time_ms"] - deduplicated[-1]["time_ms"]) < 35:
            continue
        deduplicated.append(target)
    if len(deduplicated) > MAX_VISUAL_REVIEW_FRAMES:
        anchors = [item for item in deduplicated if item["kind"] in {"first", "last", "caption-mid"}]
        optional = [item for item in deduplicated if item not in anchors]
        budget = max(0, MAX_VISUAL_REVIEW_FRAMES - len(anchors))
        if budget and len(optional) > budget:
            step = (len(optional) - 1) / max(1, budget - 1)
            indexes = sorted({round(index * step) for index in range(budget)})
            optional = [optional[index] for index in indexes]
        else:
            optional = optional[:budget]
        deduplicated = sorted(anchors + optional, key=lambda item: (item["time_ms"], item["kind"], item["label"]))
    return deduplicated


def contrast_pixels(array: np.ndarray, region: tuple[int, int, int, int]) -> int:
    height, width = array.shape[:2]
    x0, y0, x1, y1 = region
    roi = array[max(0, y0):min(height, y1), max(0, x0):min(width, x1)].astype(np.int16)
    if roi.size == 0:
        return 0
    corner = max(8, min(height, width) // 40)
    samples = np.concatenate([
        array[:corner, :corner].reshape(-1, 3),
        array[:corner, -corner:].reshape(-1, 3),
    ])
    background = np.median(samples.astype(np.int16), axis=0)
    distance = np.linalg.norm(roi - background, axis=2)
    return int(np.count_nonzero(distance > 42))


def extract_frames(video: Path, targets: list[dict[str, Any]], out_dir: Path) -> tuple[list[dict[str, Any]], Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("event-*.jpg"):
        old.unlink()
    contact_sheet = out_dir / "contact-sheet.jpg"
    if contact_sheet.exists():
        contact_sheet.unlink()

    captured: list[dict[str, Any]] = []
    target_index = 0
    last_array: np.ndarray | None = None
    last_ms = 0

    def capture_target(target: dict[str, Any], index: int, array: np.ndarray, captured_ms: int) -> None:
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", target["label"]).strip("-")[:64]
        path = out_dir / f"event-{index + 1:02d}-{target['time_ms']:06d}ms-{safe_label}.jpg"
        Image.fromarray(array).save(path, quality=90)
        target.update({
            "captured_ms": captured_ms,
            "path": str(path),
            "bottom_contrast_pixels": contrast_pixels(
                array,
                (round(array.shape[1] * 0.05), round(array.shape[0] * 0.72), round(array.shape[1] * 0.95), array.shape[0]),
            ),
        })
        captured.append(target)

    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            current_ms = round(float(frame.time or 0) * 1000)
            last_ms = current_ms
            last_array = frame.to_ndarray(format="rgb24")
            while target_index < len(targets) and current_ms >= targets[target_index]["time_ms"]:
                target = dict(targets[target_index])
                capture_target(target, target_index, last_array, current_ms)
                target_index += 1
            if target_index >= len(targets):
                break
    # Container timestamps can end a fraction of a frame before the requested
    # duration-derived target. Reuse the last decoded frame only for a target
    # within one frame; this keeps QA deterministic without inventing a frame
    # for a genuinely missing late event.
    if target_index < len(targets) and last_array is not None:
        frame_tolerance = 1000 / 24
        while target_index < len(targets) and targets[target_index]["time_ms"] <= last_ms + frame_tolerance:
            target = dict(targets[target_index])
            capture_target(target, target_index, last_array, last_ms)
            target_index += 1
    if len(captured) != len(targets):
        raise ValueError(f"抽帧不完整：需要 {len(targets)}，得到 {len(captured)}")

    columns = 4
    thumb_w, thumb_h, label_h = 480, 270, 28
    rows = math.ceil(len(captured) / columns)
    sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + label_h)), "#202020")
    draw = ImageDraw.Draw(sheet)
    for index, item in enumerate(captured):
        image = Image.open(item["path"]).convert("RGB").resize((thumb_w, thumb_h))
        x = (index % columns) * thumb_w
        y = (index // columns) * (thumb_h + label_h)
        sheet.paste(image, (x, y))
        draw.text((x + 8, y + thumb_h + 7), f"{item['time_ms'] / 1000:.2f}s {item['label']}", fill="white")
    sheet.save(contact_sheet, quality=92)
    return captured, contact_sheet


def _phase_bounds(event: dict[str, Any], name: str) -> tuple[int, int] | None:
    value = (event.get("phase") or {}).get(name)
    if not isinstance(value, dict):
        return None
    start = value.get("startMs")
    end = value.get("endMs")
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    try:
        start_ms = int(start)
        end_ms = int(end)
    except (TypeError, ValueError):
        return None
    if start_ms < 0 or end_ms <= start_ms:
        return None
    return start_ms, end_ms


def _animation_plan_checks_v33(
    project_root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    plan: dict[str, Any],
    annotations: dict[str, dict[str, Any]],
    hand_mode: str,
) -> dict[str, Any]:
    """Run V3.3 semantic-effect gates against the actual project files."""

    result: dict[str, Any] = {
        "present": True,
        "plan_version": plan.get("planVersion"),
        "hand_mode": hand_mode,
        "ok": True,
        "errors": [],
        "events": [],
        "contracts": {
            "default_effects": sorted(V33_SAFE_EFFECTS),
            "max_semantic_effects_per_scene": 1,
            "default_overlay_paths": False,
            "invent_geometry": False,
            "real_foreground_mask_required": True,
            "fallback": "stable-hold",
            "post_action_stable_ms": 250,
            "subtitle_layer": "final-composite-only",
        },
    }
    errors: list[str] = result["errors"]
    errors.extend(validate_animation_plan(plan, annotations))
    rules = plan.get("rules") or {}
    if rules.get("subtitle_layer") != "final-composite-only":
        errors.append("V3.3 动画计划没有声明字幕最终合成层")
    if rules.get("default_overlay_paths") is not False:
        errors.append("V3.3 没有明确关闭自动 overlay 路径")
    if rules.get("invent_geometry") is not False:
        errors.append("V3.3 没有明确禁止凭空生成几何图形")
    if rules.get("max_semantic_effects_per_scene") != 1:
        errors.append("V3.3 没有声明每幕最多一个语义效果")

    for scene in plan.get("scenes", []):
        scene_id = str(scene.get("sceneId", ""))
        events = list(scene.get("events", []))
        if len(events) > 1:
            errors.append(f"{scene_id} 超过每幕一个语义效果")
        for skipped in scene.get("skipped", []):
            fallback = skipped.get("fallback") or {}
            if fallback.get("to") != "skip":
                errors.append(f"{scene_id}/{skipped.get('targetElementId', '')} 没有安全降级为稳定画面")
        for event in events:
            effect = str(event.get("effect", ""))
            target_id = str(event.get("targetElementId") or event.get("targetElement") or "")
            start = int(event.get("startMs", -1))
            end = int(event.get("endMs", -1))
            target = event.get("focusTarget") or {}
            entry = {
                "scene_id": scene_id,
                "event_id": event.get("id"),
                "effect": effect,
                "target_element_id": target_id,
                "start_ms": start,
                "end_ms": end,
                "time_budget_ms": int(event.get("timeBudgetMs", end - start)),
                "hand_mode": hand_mode,
                "checks": {
                    "safe_effect": effect in V33_SAFE_EFFECTS,
                    "no_overlay_path": not bool(event.get("gesturePath") or event.get("path")),
                    "real_foreground_target": target.get("source") == "foreground-pixels",
                    "semantic_contract": all(bool(event.get(key)) for key in ("semanticIntent", "semanticGoal", "startState", "action", "endState")),
                    "stable_fallback": event.get("fallbackPolicy") == "stable-hold",
                    "no_invented_geometry": (event.get("effectOptions") or {}).get("inventGeometry") is False,
                },
            }
            result["events"].append(entry)
            if not all(entry["checks"].values()):
                errors.append(f"{scene_id}/{target_id} 未满足 V3.3 语义、安全遮罩或降级契约")
            minimum = V33_EFFECT_MIN_MS.get(effect, 10**9)
            if end - start < minimum:
                errors.append(f"{scene_id}/{target_id} {effect} 真实窗口低于 {minimum}ms")
            hand = event.get("handBehavior") or {}
            if effect != "focus-push" and hand.get("showHandForEffect") is not False:
                errors.append(f"{scene_id}/{target_id} 安全后动画不得伪装成手绘动作")
    result["ok"] = not errors
    return result
    try:
        return int(value.get("startMs", 0)), int(value.get("endMs", 0))
    except (TypeError, ValueError):
        return None


def _animation_plan_checks_v32(
    project_root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    plan: dict[str, Any],
    annotations: dict[str, dict[str, Any]],
    hand_mode: str,
) -> dict[str, Any]:
    """Run the V3.2 default gates without weakening V3.1 legacy QA."""

    result: dict[str, Any] = {
        "present": True,
        "plan_version": plan.get("planVersion"),
        "hand_mode": hand_mode,
        "ok": True,
        "errors": [],
        "events": [],
        "contracts": {
            "default_effects": ["focus-push"],
            "legacy_effects_not_generated": sorted(V31_GESTURES | LEGACY_MECHANICAL_EFFECTS),
            "max_focus_push_events_per_scene": 1,
            "min_focus_push_ms": 1200,
            "focus_push_confidence_min": 0.75,
            "focus_push_camera_ms": [400, 650],
            "focus_push_hold_ms": 400,
            "focus_push_max": 0.06,
            "default_overlay_paths": False,
            "stable_scene_without_event": True,
            "post_action_stable_ms": 250,
            "subtitle_layer": "final-composite-only",
        },
    }
    errors: list[str] = result["errors"]
    errors.extend(validate_animation_plan(plan, annotations))
    rules = plan.get("rules") or {}
    if rules.get("subtitle_layer") != "final-composite-only":
        errors.append("V3.2 动画计划没有声明字幕最终合成层")
    if rules.get("default_overlay_paths") is not False:
        errors.append("V3.2 默认计划没有明确关闭自动 overlay 路径")
    if rules.get("max_focus_push_events_per_scene") != 1:
        errors.append("V3.2 动画计划没有声明每幕最多一个 focus-push")

    for scene in plan.get("scenes", []):
        scene_id = str(scene.get("sceneId", ""))
        annotation = annotations.get(scene_id, {})
        canvas = annotation.get("canvas") or {}
        width = int(canvas.get("width", 0) or 0)
        height = int(canvas.get("height", 0) or 0)
        duration = int(scene.get("sceneDurationMs", 0) or 0)
        transition = scene.get("transition") or {}
        erase_start = int(transition.get("eraseStartMs", int(scene.get("sceneStartMs", 0)) + duration))
        events = list(scene.get("events", []))
        if len(events) > 1:
            errors.append(f"{scene_id} 超过每幕一个 focus-push")
        for skipped in scene.get("skipped", []):
            fallback = skipped.get("fallback") or {}
            if fallback.get("to") != "skip":
                errors.append(f"{scene_id}/{skipped.get('targetElementId', '')} 未以稳定画面跳过默认强调")
        previous_end = -1
        for event in sorted(events, key=lambda item: int(item.get("startMs", 0))):
            effect = str(event.get("effect", ""))
            target_id = str(event.get("targetElementId") or event.get("targetElement") or "")
            start = int(event.get("startMs", -1))
            end = int(event.get("endMs", -1))
            budget = int(event.get("timeBudgetMs", end - start))
            entry: dict[str, Any] = {
                "scene_id": scene_id,
                "event_id": event.get("id"),
                "effect": effect,
                "target_element_id": target_id,
                "start_ms": start,
                "end_ms": end,
                "time_budget_ms": budget,
                "hand_mode": hand_mode,
                "checks": {},
            }
            result["events"].append(entry)
            no_legacy_effect = effect == "focus-push"
            no_overlay_path = not bool(event.get("gesturePath") or event.get("path"))
            entry["checks"]["default_effect_only"] = no_legacy_effect
            entry["checks"]["no_automatic_overlay_path"] = no_overlay_path
            if not no_legacy_effect:
                errors.append(f"{scene_id}/{target_id} 新计划仍包含旧手势/机械效果 {effect}")
            if not no_overlay_path:
                errors.append(f"{scene_id}/{target_id} 新计划包含自动 overlay 路径")
            duration_ok = start >= 0 and end > start and end <= duration and budget == end - start and budget >= 1200
            entry["checks"]["duration_threshold"] = duration_ok
            if not duration_ok:
                errors.append(f"{scene_id}/{target_id} focus-push 时间窗口必须是至少 1200ms 的真实窗口")

            target = event.get("focusTarget") or {}
            target_ok = str(target.get("source", "")) == "foreground-pixels"
            try:
                tx, ty = int(target["x"]), int(target["y"])
                tw, th = int(target["width"]), int(target["height"])
                safe_top = int(target.get("subtitleSafeTopPx", round(height * 0.84)))
            except (KeyError, TypeError, ValueError):
                target_ok = False
                tx = ty = tw = th = safe_top = 0
            if float(target.get("confidence", 0.0) or 0.0) < 0.75:
                target_ok = False
            if width and height and (tx < 0 or ty < 0 or tw <= 0 or th <= 0 or tx + tw > width or ty + th > height or ty + th > safe_top):
                target_ok = False
            entry["checks"]["high_confidence_focus_target"] = target_ok
            if not target_ok:
                errors.append(f"{scene_id}/{target_id} focusTarget 不是高置信度前景目标或越过字幕安全区")

            prepare = _phase_bounds(event, "prepare")
            camera = _phase_bounds(event, "camera")
            hold = _phase_bounds(event, "hold")
            leave = _phase_bounds(event, "leave")
            phase_ranges = [item for item in (prepare, camera, hold, leave) if item is not None]
            phase_ok = len(phase_ranges) == 4 and not any(left[1] > right[0] for left, right in zip(phase_ranges, phase_ranges[1:]))
            if prepare and not 80 <= prepare[1] - prepare[0] <= 120:
                phase_ok = False
            if not camera or not 400 <= camera[1] - camera[0] <= 650:
                phase_ok = False
            if not hold or hold[1] - hold[0] < 400:
                phase_ok = False
            if any(boundary < start or boundary > end for pair in phase_ranges for boundary in pair):
                phase_ok = False
            if "draw" in (event.get("phase") or {}):
                phase_ok = False
            entry["checks"]["camera_and_hold_timing"] = phase_ok
            if not phase_ok:
                errors.append(f"{scene_id}/{target_id} focus-push 必须无 draw 阶段，推近 400–650ms 且停留至少 400ms")
            intensity = float(event.get("intensity", 0.0) or 0.0)
            intensity_ok = 0.04 <= intensity <= 0.06 and str(rules.get("focus_push_return")) == "disabled-by-default"
            entry["checks"]["scale_and_no_return"] = intensity_ok
            if not intensity_ok:
                errors.append(f"{scene_id}/{target_id} focus-push 缩放或回弹策略不符合默认门禁")
            hand = event.get("handBehavior") or {}
            hand_ok = hand.get("hideDuringCamera") is True
            entry["checks"]["hand_hidden_during_camera"] = hand_ok
            if not hand_ok:
                errors.append(f"{scene_id}/{target_id} 镜头推近期间必须隐藏手部")
            persist = int(event.get("persistUntilMs", end))
            stable_ok = persist - end >= 250 and end + 250 <= erase_start
            entry["checks"]["stable_before_eraser"] = stable_ok
            if not stable_ok:
                errors.append(f"{scene_id}/{target_id} 板擦前稳定画面不足 250ms")
            style = event.get("style") or {}
            style_ok = str(style.get("colorSource", "")).startswith("style-registry:")
            entry["checks"]["style_registry_color"] = style_ok
            if not style_ok:
                errors.append(f"{scene_id}/{target_id} 强调色没有回指 style registry")
            if previous_end >= 0 and start < previous_end:
                errors.append(f"{scene_id}/{target_id} 与相邻动画重叠")
            previous_end = max(previous_end, end)
    result["ok"] = not errors
    return result


def _actual_hand_mode(project: dict[str, Any], state: dict[str, Any]) -> str:
    render_metrics = state.get("render_metrics") if isinstance(state.get("render_metrics"), dict) else {}
    profile = project.get("renderer_profile") if isinstance(project.get("renderer_profile"), dict) else {}
    hand_mode = str(render_metrics.get("hand_mode") or profile.get("hand_mode") or state.get("hand_mode") or "small-hand")
    return "no-hand" if hand_mode == "bare-tip" else hand_mode


def _animation_plan_checks(project_root: Path, project: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Validate V3.3 defaults or V3.2/V3.1 compatibility plans."""

    path = project_root / "animation-plan.json"
    hand_mode = _actual_hand_mode(project, state)
    result: dict[str, Any] = {
        "present": path.is_file(),
        "plan_version": None,
        "hand_mode": hand_mode,
        "ok": True,
        "errors": [],
        "events": [],
        "contracts": {
            "min_gesture_ms": 700,
            "min_focus_push_ms": 1200,
            "post_action_stable_ms": 250,
            "focus_push_max": 0.06,
            "subtitle_layer": "final-composite-only",
        },
    }
    errors: list[str] = result["errors"]
    if not path.is_file():
        errors.append("缺少 animation-plan.json，无法执行动画门禁")
        result["ok"] = False
        return result
    plan = read_json(path)
    result["plan_version"] = plan.get("planVersion")
    annotations: dict[str, dict[str, Any]] = {}
    for scene in project.get("scenes", []):
        scene_id = str(scene.get("id"))
        annotation_path = project_root / "annotations" / f"{scene_id}.annotation.json"
        if annotation_path.is_file():
            annotations[scene_id] = read_json(annotation_path)
    if str(plan.get("planVersion", "")) == "3.3":
        return _animation_plan_checks_v33(project_root, project, state, plan, annotations, hand_mode)
    if str(plan.get("planVersion", "")) == "3.2":
        return _animation_plan_checks_v32(project_root, project, state, plan, annotations, hand_mode)
    errors.extend(validate_animation_plan(plan, annotations))
    if str(plan.get("planVersion", "")) != "3.1":
        errors.append(f"动画计划版本不是 3.1：{plan.get('planVersion')}")
    rules = plan.get("rules") or {}
    if rules.get("subtitle_layer") != "final-composite-only":
        errors.append("动画计划没有声明字幕最终合成层")

    for scene in plan.get("scenes", []):
        scene_id = str(scene.get("sceneId", ""))
        annotation = annotations.get(scene_id, {})
        canvas = annotation.get("canvas") or {}
        width = int(canvas.get("width", 0) or 0)
        height = int(canvas.get("height", 0) or 0)
        safe_top_default = round(height * 0.84)
        duration = int(scene.get("sceneDurationMs", 0) or 0)
        transition = scene.get("transition") or {}
        erase_start = int(transition.get("eraseStartMs", duration)) if transition else duration
        events = list(scene.get("events", []))
        if len(events) > 2:
            errors.append(f"{scene_id} 超过每幕 2 个显著强调")
        previous_end = -1
        for event in sorted(events, key=lambda item: int(item.get("startMs", 0))):
            effect = str(event.get("effect", ""))
            target_id = str(event.get("targetElementId") or event.get("targetElement") or "")
            start = int(event.get("startMs", -1))
            end = int(event.get("endMs", -1))
            budget = int(event.get("timeBudgetMs", end - start))
            entry: dict[str, Any] = {
                "scene_id": scene_id,
                "event_id": event.get("id"),
                "effect": effect,
                "target_element_id": target_id,
                "start_ms": start,
                "end_ms": end,
                "time_budget_ms": budget,
                "hand_mode": hand_mode,
                "checks": {},
            }
            result["events"].append(entry)

            if effect in LEGACY_MECHANICAL_EFFECTS:
                errors.append(f"{scene_id}/{target_id} 新计划仍使用遗留机械效果 {effect}")
                entry["checks"]["no_default_mechanical_effect"] = False
            else:
                entry["checks"]["no_default_mechanical_effect"] = True
            if effect not in V31_GESTURES and effect != "focus-push":
                errors.append(f"{scene_id}/{target_id} 不是 V3.1 语义效果：{effect}")
            if start < 0 or end <= start or end > duration:
                errors.append(f"{scene_id}/{target_id} 动画时间越界 {start}-{end}/{duration}")
            if budget != end - start:
                errors.append(f"{scene_id}/{target_id} timeBudgetMs 与真实窗口不一致")
            if effect in V31_GESTURES and budget < 700:
                errors.append(f"{scene_id}/{target_id} 手绘动作低于 700ms")
            if effect == "focus-push" and budget < 1200:
                errors.append(f"{scene_id}/{target_id} focus-push 低于 1200ms")
            entry["checks"]["duration_threshold"] = budget >= (1200 if effect == "focus-push" else 700)

            target = event.get("focusTarget") or {}
            target_ok = True
            try:
                tx, ty = int(target["x"]), int(target["y"])
                tw, th = int(target["width"]), int(target["height"])
                safe_top = int(target.get("subtitleSafeTopPx", safe_top_default))
            except (KeyError, TypeError, ValueError):
                target_ok = False
                tx = ty = tw = th = safe_top = 0
            if not target_ok or width <= 0 or height <= 0 or tx < 0 or ty < 0 or tw <= 0 or th <= 0 or tx + tw > width or ty + th > height or ty + th > safe_top:
                errors.append(f"{scene_id}/{target_id} focusTarget 边界或字幕安全区失败")
                target_ok = False
            if float(target.get("confidence", 1.0) or 0.0) < 0.18:
                errors.append(f"{scene_id}/{target_id} focusTarget 置信度低于门槛")
                target_ok = False
            entry["checks"]["focus_target_bounds"] = target_ok

            raw_path = event.get("gesturePath") or event.get("path") or []
            path_ok = len(raw_path) >= 2
            previous_point: list[int] | None = None
            for point in raw_path:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    path_ok = False
                    continue
                try:
                    px, py = float(point[0]), float(point[1])
                except (TypeError, ValueError):
                    path_ok = False
                    continue
                if width and height and (px < 0 or py < 0 or px > width or py > height or py > safe_top - 2):
                    path_ok = False
                if previous_point is not None and px == previous_point[0] and py == previous_point[1] and len(raw_path) == 2:
                    path_ok = False
                previous_point = [px, py]
            if effect in V31_GESTURES or effect == "focus-push":
                if not path_ok:
                    errors.append(f"{scene_id}/{target_id} gesturePath 边界或有效点失败")
            entry["checks"]["gesture_path_bounds"] = path_ok

            phases = event.get("phase") or {}
            required = ("prepare", "draw", "hold", "leave")
            if effect == "focus-push":
                required = ("prepare", "draw", "camera", "hold", "leave")
            phase_ok = True
            phase_ranges: list[tuple[int, int]] = []
            for phase_name in required:
                bounds = _phase_bounds(event, phase_name)
                if bounds is None:
                    phase_ok = False
                    continue
                phase_start, phase_end = bounds
                phase_ranges.append(bounds)
                if phase_end < phase_start or phase_start < start or phase_end > end:
                    phase_ok = False
            if phase_ranges and any(left[1] > right[0] for left, right in zip(phase_ranges, phase_ranges[1:])):
                phase_ok = False
            prepare = _phase_bounds(event, "prepare")
            draw = _phase_bounds(event, "draw")
            hold = _phase_bounds(event, "hold")
            leave = _phase_bounds(event, "leave")
            if prepare and not 80 <= prepare[1] - prepare[0] <= 120:
                phase_ok = False
            if draw and draw[1] <= draw[0]:
                phase_ok = False
            if hold and hold[1] - hold[0] < (400 if effect == "focus-push" else 250):
                phase_ok = False
            if leave and leave[1] - leave[0] < 80:
                phase_ok = False
            camera = _phase_bounds(event, "camera")
            if effect == "focus-push":
                if not camera or camera[1] - camera[0] < 400 or camera[1] - camera[0] > 650 or not draw or camera[0] < draw[1]:
                    phase_ok = False
                if float(event.get("intensity", 0.0) or 0.0) > 0.06:
                    errors.append(f"{scene_id}/{target_id} focus-push 缩放超过 6%")
                    phase_ok = False
            if not phase_ok:
                errors.append(f"{scene_id}/{target_id} phase timings 不满足抬笔、绘制、停留、收手契约")
            entry["checks"]["phase_timings"] = phase_ok

            persist = int(event.get("persistUntilMs", end))
            stable_ms = persist - end
            stable_ok = stable_ms >= 250 and end + 250 <= erase_start
            if not stable_ok:
                errors.append(f"{scene_id}/{target_id} 动作结束到板擦前不足 250ms")
            entry["checks"]["ink_persistence_before_eraser"] = stable_ok

            style = event.get("style") or {}
            style_ok = str(style.get("colorSource", "")).startswith("style-registry:") and bool(style.get("colorHex"))
            if not style_ok:
                errors.append(f"{scene_id}/{target_id} 强调色未来自 style registry")
            entry["checks"]["style_registry_color"] = style_ok

            hand = event.get("handBehavior") or {}
            if hand_mode in {"small-hand", "presenter"}:
                hand_ok = hand.get("mode") == hand_mode and hand.get("coordinateSpace") == "annotation-pixels" and bool(hand.get("tipAnchorSource"))
                if not hand_ok:
                    errors.append(f"{scene_id}/{target_id} {hand_mode} 缺少笔尖锚点来源或坐标契约")
                entry["checks"][f"{hand_mode.replace('-', '_')}_anchor_contract"] = hand_ok
            else:
                entry["checks"]["no_hand_progressive_path_contract"] = bool(draw and path_ok)

            if previous_end >= 0 and start < previous_end:
                errors.append(f"{scene_id}/{target_id} 与相邻动画重叠")
            previous_end = max(previous_end, end)
        if events and any(int(item.get("endMs", 0)) + 250 > erase_start for item in events):
            errors.append(f"{scene_id} 动画与板擦时间窗重叠")

    result["ok"] = not errors
    return result


def object_color_errors(metrics: dict, expected_ids: set[str]) -> list[str]:
    """Do not let one object's extra sweeps conceal another object's deficit."""
    records = metrics.get("object_records")
    if not isinstance(records, list):
        return ["缺少逐对象填色记录，旧总量不能证明逐对象完成；需重新渲染后核对"]
    errors, seen = [], set()
    for record in records:
        if not isinstance(record, dict):
            errors.append("逐对象填色记录格式错误")
            continue
        identity = str(record.get("elementId", ""))
        if identity not in expected_ids or identity in seen:
            errors.append(f"填色对象 ID 未知或重复：{identity}")
        seen.add(identity)
        values = [record.get(k) for k in ("colorPixels", "sweeps", "baseColorFrames")]
        if any(type(v) is not int or v < 0 for v in values):
            errors.append(f"{identity} 填色记录不完整或无效")
            continue
        pixels, sweeps, frames = values
        if pixels > 0 and (sweeps < MIN_COLOR_SWEEPS or frames < MIN_COLOR_FRAMES):
            errors.append(f"{identity} 基础色未完成：{sweeps} 次描绘、{frames} 帧")
        elif pixels == 0 and sweeps != 0:
            errors.append(f"{identity} 无待填色像素却记录了填色次数")
    if seen != expected_ids:
        errors.append("逐对象填色记录未完整对应本幕对象")
    return errors


def _stroke_art_quality_checks(
    project_root: Path,
    project: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify that semantic motion profiles were actually used by every rendered scene."""
    renderer_profile = project.get("renderer_profile") if isinstance(project.get("renderer_profile"), dict) else {}
    expected_planner = str(renderer_profile.get("stroke_planner", "semantic-v2"))
    expected_color = str(renderer_profile.get("color_fill", "local-brush"))
    expected_pixel_ownership = PIXEL_POLICY
    actual_hand_mode = _actual_hand_mode(project, state or {})
    result: dict[str, Any] = {
        "required": expected_planner == "semantic-v2" or expected_color == "local-brush",
        "expected_planner": expected_planner,
        "expected_color_fill": expected_color,
        "expected_pixel_ownership": expected_pixel_ownership,
        "hand_mode": actual_hand_mode,
        "ok": True,
        "errors": [],
        "warnings": [],
        "scenes": [],
    }
    if not result["required"]:
        return result
    for scene in project.get("scenes", []):
        scene_id = str(scene.get("id"))
        profile_path = project_root / "renders" / f"{scene_id}-profile.json"
        if not profile_path.is_file():
            result["errors"].append(f"{scene_id} 缺少笔画渲染 profile，无法确认语义笔画规划")
            continue
        profile = read_json(profile_path)
        entry = {
            "scene_id": scene_id,
            "stroke_planner": profile.get("stroke_planner"),
            "scene_plan_sha256": profile.get("scene_plan_sha256"),
            "stroke_strategy": profile.get("stroke_strategy"),
            "color_fill": profile.get("color_fill"),
            "color_schedule": profile.get("color_schedule"),
            "pixel_ownership": profile.get("pixel_ownership"),
            "ownership_metrics": profile.get("ownership_metrics") or {},
            "phase_budget_metrics": profile.get("phase_budget_metrics") or [],
            "stroke_metrics": profile.get("stroke_metrics") or [],
            "color_metrics": profile.get("color_metrics") or {},
            "motion_plans": profile.get("motion_plans") or [],
            "hand_motion_qa": profile.get("hand_motion_qa") or {},
        }
        result["scenes"].append(entry)
        if expected_planner == "semantic-v2" and entry["stroke_planner"] != "semantic-v2":
            result["errors"].append(f"{scene_id} 没有使用 semantic-v2 笔画规划")
        if expected_planner == "semantic-v2" and entry["stroke_strategy"] != "fast-semantic-trace-v1":
            result["errors"].append(f"{scene_id} 没有使用完整语义笔画快速描绘策略")
        if expected_color == "local-brush" and entry["color_fill"] != "local-brush":
            result["errors"].append(f"{scene_id} 没有使用 local-brush 局部上色")
        if (
            expected_planner == "semantic-v2"
            and expected_color == "local-brush"
            and entry["color_schedule"] != "object-progressive-v1"
        ):
            result["errors"].append(f"{scene_id} 没有使用逐对象渐进上色时序")
        ownership_metrics = entry["ownership_metrics"]
        if entry["pixel_ownership"] != PIXEL_POLICY or ownership_metrics.get("mode") != PIXEL_POLICY:
            result["errors"].append(f"{scene_id} 缺少统一的语义像素归属契约")
        try:
            record = (state or {}).get("boards", {})[scene_id]
            annotation = read_json(project_root / record["annotation"])
            rgb = np.asarray(Image.open(project_root / record["image"]).convert("RGB"))
            saved_plan = read_json(project_root / "animation-plan.json")
            ownership_annotation = effective_annotation(annotation, scene_id, saved_plan)
            _, _, expected_ownership = require_ownership(rgb, ownership_annotation)
            if ownership_metrics.get("map_sha256") != expected_ownership["map_sha256"]:
                result["errors"].append(f"{scene_id} 执行的像素归属与当前标注不一致")
            if expected_planner == "semantic-v2" and expected_color == "local-brush":
                fps = int(project.get("render_fps", 30))
                plan = read_json(project_root / "animation-plan.json")
                planned_scene = next((item for item in plan.get("scenes", []) if str(item.get("sceneId")) == scene_id), {})
                expected_plan_hash = hashlib.sha256(json.dumps(planned_scene, ensure_ascii=False, sort_keys=True,
                                                               separators=(",", ":")).encode("utf-8")).hexdigest()
                if entry["scene_plan_sha256"] != expected_plan_hash:
                    result["errors"].append(f"{scene_id} 实际执行的场景计划与批准版本不一致")
                effective = effective_annotation(annotation, scene_id, plan, fps)
                expected_budgets = {str(e["id"]): e["reveal"]["frameBudget"] for e in effective["elements"]}
                actual_budgets = entry["phase_budget_metrics"]
                if len(actual_budgets) != len(expected_budgets) or {str(b.get("elementId")) for b in actual_budgets} != set(expected_budgets):
                    result["errors"].append(f"{scene_id} 缺少逐对象实际帧预算记录")
                for actual in actual_budgets:
                    expected = expected_budgets.get(str(actual.get("elementId")), {})
                    if (not expected or any(actual.get(k) != v for k, v in expected.items())
                            or actual.get("actualFrames") != expected.get("totalFrames")
                            or actual.get("actualPhases") != expected.get("phases")):
                        result["errors"].append(f"{scene_id}/{actual.get('elementId')} 实际阶段帧数与批准预算不一致")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result["errors"].append(f"{scene_id} 无法核对执行契约：{exc}")
        metrics = entry["stroke_metrics"]
        if expected_planner == "semantic-v2" and not metrics:
            result["errors"].append(f"{scene_id} 没有输出语义笔画分层指标")
        malformed_stages = [
            metric for metric in metrics
            if int(metric.get("detail_strokes", 0)) > 0
            and int(metric.get("identity_strokes", 0)) + int(metric.get("support_strokes", 0))
            != int(metric.get("detail_strokes", 0))
        ]
        if malformed_stages:
            result["errors"].append(f"{scene_id} 对象内部识别笔画与支撑笔画统计不完整")
        dense_single_direction = [
            metric for metric in metrics
            if int(metric.get("total_strokes", 0)) >= 12 and int(metric.get("direction_bins", 0)) < 2
        ]
        if dense_single_direction:
            result["warnings"].append(f"{scene_id} 有密集区域仅包含单一主方向，需在 contact sheet 中人工复核")
        incomplete_trace = [
            metric for metric in metrics
            if int(metric.get("traced_hand_strokes", -1)) != int(metric.get("total_strokes", 0))
            or int(metric.get("deferred_strokes", -1)) != 0
        ]
        if expected_planner == "semantic-v2" and incomplete_trace:
            result["errors"].append(f"{scene_id} 仍有语义笔画未由手部实际描绘")
        hand_motion_qa = entry["hand_motion_qa"]
        if actual_hand_mode != "no-hand":
            if not hand_motion_qa:
                result["warnings"].append(
                    f"{scene_id} 是旧渲染 profile，尚无逐帧手部坐标 QA；下次重渲染会自动补齐"
                )
            elif hand_motion_qa.get("version") != "hand-motion-qa-v1":
                result["errors"].append(f"{scene_id} 手部逐帧 QA 版本不受支持")
            elif hand_motion_qa.get("status") != "passed":
                violation_count = int(hand_motion_qa.get("violation_count", 0))
                max_step = float(hand_motion_qa.get("max_step_short_edges", 0.0))
                result["errors"].append(
                    f"{scene_id} 手部逐帧运动不连续：{violation_count} 个异常，"
                    f"最大单帧位移 {max_step:.3f} 个画面短边"
                )
        color_metrics = entry["color_metrics"]
        if expected_color == "local-brush":
            expected_ids = {str(item.get("id")) for item in scene.get("elements", [])}
            result["errors"].extend(f"{scene_id} {message}" for message in object_color_errors(color_metrics, expected_ids))
        identity_ratio = float(color_metrics.get("max_identity_ready_ratio", 1.0))
        if int(color_metrics.get("texture_frames", 0)) > 0 and identity_ratio > 0.90:
            result["errors"].append(
                f"{scene_id} 对象最晚到绘制时段 {identity_ratio:.1%} 才具备线稿与基础色（按原始绘制预算核对），识别信息出现过晚"
            )
        residual_ratio = float(color_metrics.get("max_finalize_residual_ratio", 0.0))
        if residual_ratio > 0.08:
            result["warnings"].append(
                f"{scene_id} 最终补齐前仍有 {residual_ratio:.1%} 前景像素未完成，需检查是否出现末帧跳变"
            )
    result["ok"] = not result["errors"]
    return result


def run(project_root: Path) -> dict[str, Any]:
    t_qa_start = time.perf_counter()
    project = read_json(project_root / "project.json")
    state = read_json(project_root / "state.json")
    final = project_root / "deliverables" / "final.mp4"
    if not final.is_file():
        raise ValueError("缺少 deliverables/final.mp4")
    cues = read_srt(project_root / "audio" / "captions.srt")
    script_text = (project_root / "script" / "narration.md").read_text(encoding="utf-8")
    caption_semantics = caption_semantic_metrics(cues, script_text)
    words_path = project_root / "audio" / "words.json"
    words_data = read_json(words_path) if words_path.is_file() else {"words": []}
    pronunciation_checks = pronunciation_contract_checks(project_root, script_text)
    board_checks = run_board_qa(project_root)

    with av.open(str(final)) as container:
        video_streams = [stream for stream in container.streams if stream.type == "video"]
        audio_streams = [stream for stream in container.streams if stream.type == "audio"]
        if not video_streams:
            raise ValueError("最终视频没有画面轨")
        if not audio_streams:
            raise ValueError("最终视频没有音轨")
        video_stream = video_streams[0]
        video_duration_ms = stream_duration_ms(video_stream) or round(float(container.duration or 0) / 1000)
        fps = float(video_stream.average_rate or 30)
        media = {
            "container_duration_ms": round(float(container.duration or 0) / 1000),
            "video_duration_ms": video_duration_ms,
            "resolution": [video_stream.width, video_stream.height],
            "fps": round(fps, 6),
            "video_frames": int(video_stream.frames or 0),
            "video_codec": video_stream.codec_context.name,
            "audio_codec": audio_streams[0].codec_context.name,
        }
    t_audio_start = time.perf_counter()
    audio = decode_audio_metrics(final)
    t_audio_ms = (time.perf_counter() - t_audio_start) * 1000.0

    media["audio"] = audio
    media["av_delta_ms"] = abs(media["video_duration_ms"] - audio["duration_ms"])
    tolerance_ms = max(40, math.ceil(1000 / max(1.0, media["fps"])))
    contract_checks = media_contract_checks(
        project, words_data, cues, media, int(audio["duration_ms"]), tolerance_ms
    )
    animation_checks = _animation_plan_checks(project_root, project, state)
    stroke_art_checks = _stroke_art_quality_checks(project_root, project, state)

    bookends = project.get("bookends") if isinstance(project.get("bookends"), dict) else {}
    prefix_ms = max(0, int(bookends.get("prefix_duration_ms", 0) or 0))
    targets = build_targets(project, project_root, cues, media["video_duration_ms"], prefix_ms)
    t_extract_start = time.perf_counter()
    captured, contact_sheet = extract_frames(
        final,
        targets,
        project_root / "deliverables" / "qa-frames",
    )
    t_extract_ms = (time.perf_counter() - t_extract_start) * 1000.0
    cue_checks = [
        {
            "cue": item.get("cue"),
            "time_ms": item["time_ms"],
            "bottom_contrast_pixels": item["bottom_contrast_pixels"],
            "present": item["bottom_contrast_pixels"] >= 500,
        }
        for item in captured
        if item["kind"] == "caption-mid"
    ]
    errors: list[str] = []
    warnings: list[str] = []
    board_messages = [f"板图 QA：{message}" for message in board_checks.get("errors", [])]
    if (state.get("approvals", {}).get("boards", {}).get("visual_quality_authority") == "user-confirmed-plan"):
        warnings.extend(board_messages)
    else:
        errors.extend(board_messages)
    warnings.extend(f"板图提示：{message}" for message in board_checks.get("warnings", []))
    errors.extend(animation_checks["errors"])
    errors.extend(stroke_art_checks["errors"])
    errors.extend(contract_checks["errors"])
    errors.extend(pronunciation_checks["errors"])
    warnings.extend(caption_semantics["warnings"])
    if media["av_delta_ms"] > tolerance_ms:
        errors.append(f"音画轨时长差 {media['av_delta_ms']}ms 超过容差 {tolerance_ms}ms")
    if not audio["non_silent"]:
        errors.append("最终音轨疑似静音")
    missing_cues = [check["cue"] for check in cue_checks if not check["present"]]
    if missing_cues:
        errors.append("这些字幕抽检未发现可见文字：" + ", ".join(map(str, missing_cues)))
    if not caption_semantics["script_text_match"]:
        errors.append("字幕正文与锁定口播稿不一致")
    if not caption_semantics["single_line"]:
        errors.append("最终字幕存在多行条目")
    if not caption_semantics["punctuation_free"]:
        errors.append("最终字幕含有标点")
    final_hash = sha256(final)
    state_hash = state.get("final_sha256")
    if state_hash and state_hash != final_hash:
        errors.append("final.mp4 与 state.json 记录的哈希不一致")

    archived_finals = sorted((project_root / "deliverables" / "archive").glob("final-before-render-*.mp4"))
    baseline_media: dict[str, Any] | None = None
    if archived_finals:
        baseline_path = archived_finals[-1]
        try:
            with av.open(str(baseline_path)) as baseline_container:
                baseline_stream = next(stream for stream in baseline_container.streams if stream.type == "video")
                baseline_duration = stream_duration_ms(baseline_stream) or round(float(baseline_container.duration or 0) / 1000)
            baseline_media = {"path": str(baseline_path), "duration_ms": baseline_duration, "sha256": sha256(baseline_path)}
            if media["video_duration_ms"] > baseline_duration + tolerance_ms:
                errors.append(f"新成片时长 {media['video_duration_ms']}ms 超过归档基线 {baseline_duration}ms")
        except Exception as exc:
            errors.append(f"无法读取归档基线时长：{exc}")

    total_elements = sum(len(scene.get("elements", [])) for scene in project.get("scenes", []))
    target_counts: dict[str, int] = {}
    for item in captured:
        target_counts[item["kind"]] = target_counts.get(item["kind"], 0) + 1
    report = {
        "version": 6,
        "ok": not errors,
        "timings_ms": {
            "audio_decode_ms": round(t_audio_ms, 2) if 't_audio_ms' in locals() else 0.0,
            "frame_extract_ms": round(t_extract_ms, 2) if 't_extract_ms' in locals() else 0.0,
            "total_qa_ms": round((time.perf_counter() - t_qa_start) * 1000.0, 2) if 't_qa_start' in locals() else 0.0,
        },
        "checked_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "errors": errors,
        "warnings": warnings,
        "final": {"path": str(final), "bytes": final.stat().st_size, "sha256": final_hash},
        "media": media,
        "baseline": baseline_media,
        "timeline": {
            "scene_count": len(project.get("scenes", [])),
            "element_count": total_elements,
            "subtitle_count": len(cues),
            "visual_change_interval_sec": round(media["video_duration_ms"] / max(1, total_elements) / 1000, 3),
            "tolerance_ms": tolerance_ms,
            "qa_target_counts": target_counts,
            **contract_checks["metrics"],
        },
        "subtitle_checks": cue_checks,
        "subtitle_semantics": caption_semantics,
        "pronunciation_checks": pronunciation_checks,
        "animation_checks": animation_checks,
        "board_checks": board_checks,
        "stroke_art_checks": stroke_art_checks,
        "visual_review": {
            "required": True,
            "accepted": False,
            "summary": None,
            "contact_sheet": str(contact_sheet),
            "sample_count": len(captured),
            "samples": captured,
            "checklist": [
                "相邻场景构图不重复，人物身份和画风连续",
                "未开始元素没有提前出现；每个 element-end 抽帧都完整，人物头顶、四肢和道具没有等到整幕末尾才突然补出",
                "语义主体先于装饰碎线出现；区域内部按轮廓、结构、细节、纹理推进，不退化成全画布水平或垂直刮开",
                "局部上色由手部逐对象完成，颜色没有在最后一帧整区突然补出",
                "手绘中段和收笔抽帧中笔尖贴近当前笔迹；默认小手高度约为画面短边 12%–16%，没有大面积遮挡主体",
                "关键动画开始、25%、50%、75%、完成、停留中点和收手抽帧中，small-hand 与笔迹同步；no-hand 只要求路径渐进",
                "线条完成后持续保留到稳定窗口，focus-push 只缓慢推近并保持，不立即回弹",
                "板擦开始/中点/擦完抽帧中板擦手与掩码同步，擦完后有短暂干净画布；下一幕首词抽帧已进入新幕",
                "字幕清楚且不遮住核心画面，末帧完整干净",
                "如 pronunciation_checks 标记需要人工复核，按展示文案的语义确认实际音频读音，不以 Whisper 转写作为读音证明",
            ]
        }
    }
    write_json(project_root / "deliverables" / "qa-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="检查最终媒体并生成逐元素抽帧总览")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    try:
        report = run(Path(args.project).resolve())
    except Exception as exc:
        print(f"[err] QA 失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
