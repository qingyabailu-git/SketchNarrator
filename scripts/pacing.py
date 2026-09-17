#!/usr/bin/env python3
"""Adaptive duration pacing solver for whiteboard animations.

Calculates natural drawing durations for elements based on available voiceover
windows, preventing rushed drawing and long frozen pauses.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_PACING_RATIO = 0.92
DEFAULT_MIN_DURATION_MS = 1500
DEFAULT_MAX_DURATION_MS = 25000
DEFAULT_MIN_HOLD_MS = 250


def calculate_adaptive_durations(
    elements: list[dict[str, Any]],
    scene_duration_ms: int,
    ratio: float = DEFAULT_PACING_RATIO,
    min_ms: int = DEFAULT_MIN_DURATION_MS,
    max_ms: int = DEFAULT_MAX_DURATION_MS,
    min_hold_ms: int = DEFAULT_MIN_HOLD_MS,
) -> list[dict[str, Any]]:
    """Return a deepcopy of elements with reveal.durationMs adapted to voiceover windows."""
    if not elements:
        return []

    result = copy.deepcopy(elements)
    n = len(result)

    for el in result:
        el.setdefault("reveal", {})

    valid_starts = all(
        isinstance(el["reveal"].get("startMs"), (int, float)) for el in result
    )

    if not valid_starts:
        step = max(500, (scene_duration_ms - 200) // n)
        for i, el in enumerate(result):
            el["reveal"]["startMs"] = 100 + i * step

    for i in range(n):
        el = result[i]
        st = int(el["reveal"].get("startMs", 0) or 0)

        if i < n - 1:
            next_st = int(result[i + 1]["reveal"].get("startMs", scene_duration_ms) or scene_duration_ms)
            available = max(0, next_st - st)
            target = int(round(available * ratio))
            dur = max(min_ms, min(target, max_ms))

            if available > min_ms + min_hold_ms:
                dur = min(dur, available - min_hold_ms)
            elif available > 350:
                dur = max(300, available - 50)
            else:
                dur = max(100, available)
        else:
            available = max(0, scene_duration_ms - st)
            target = int(round(available * max(0.60, ratio - 0.02)))
            dur = max(min_ms, min(target, max_ms))

            end_reserve = max(min_hold_ms * 2, 450)
            if available > min_ms + end_reserve:
                dur = min(dur, available - end_reserve)
            elif available > min_ms + min_hold_ms:
                dur = min(dur, available - min_hold_ms)
            elif available > 350:
                dur = max(300, available - min_hold_ms)
            else:
                dur = max(100, available)

        el["reveal"]["durationMs"] = int(dur)

    return result


def pace_annotation_dict(
    annotation: dict[str, Any],
    ratio: float = DEFAULT_PACING_RATIO,
    min_ms: int = DEFAULT_MIN_DURATION_MS,
    max_ms: int = DEFAULT_MAX_DURATION_MS,
    min_hold_ms: int = DEFAULT_MIN_HOLD_MS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pace an annotation document and return (updated_doc, changes_summary)."""
    doc = copy.deepcopy(annotation)
    scene_dur = int(doc.get("sceneDurationMs", 0) or 0)
    elements = doc.get("elements", [])
    if not elements or scene_dur <= 0:
        return doc, []

    updated = calculate_adaptive_durations(
        elements,
        scene_dur,
        ratio=ratio,
        min_ms=min_ms,
        max_ms=max_ms,
        min_hold_ms=min_hold_ms,
    )

    changes: list[dict[str, Any]] = []
    for orig, new in zip(elements, updated):
        old_dur = orig.get("reveal", {}).get("durationMs")
        new_dur = new.get("reveal", {}).get("durationMs")
        if old_dur != new_dur:
            changes.append({
                "id": new.get("id"),
                "startMs": new.get("reveal", {}).get("startMs"),
                "old_durationMs": old_dur,
                "new_durationMs": new_dur,
            })

    doc["elements"] = updated
    return doc, changes


def pace_project_annotations(
    root: Path,
    ratio: float = DEFAULT_PACING_RATIO,
    min_ms: int = DEFAULT_MIN_DURATION_MS,
    max_ms: int = DEFAULT_MAX_DURATION_MS,
    min_hold_ms: int = DEFAULT_MIN_HOLD_MS,
    scene_id: str | None = None,
) -> dict[str, Any]:
    """Pace all annotations in a project or a single scene."""
    project_path = root / "project.json"
    if not project_path.is_file():
        raise FileNotFoundError(f"project.json not found in {root}")

    project = json.loads(project_path.read_text(encoding="utf-8"))
    state_path = root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}

    scenes = project.get("scenes", [])
    if scene_id:
        scenes = [s for s in scenes if str(s.get("id")) == str(scene_id)]
        if not scenes:
            raise ValueError(f"Scene not found: {scene_id}")

    results: dict[str, Any] = {}
    total_changed = 0

    for s in scenes:
        sid = str(s["id"])
        board_record = (state.get("boards") or {}).get(sid)
        anno_rel = board_record.get("annotation") if isinstance(board_record, dict) else None
        if anno_rel:
            anno_path = root / str(anno_rel)
        else:
            anno_path = root / "annotations" / f"{sid}.annotation.json"

        if not anno_path.is_file():
            continue

        raw = json.loads(anno_path.read_text(encoding="utf-8"))
        if not raw.get("sceneDurationMs"):
            raw["sceneDurationMs"] = int(s.get("end_ms", 0)) - int(s.get("start_ms", 0))

        updated_doc, changes = pace_annotation_dict(
            raw,
            ratio=ratio,
            min_ms=min_ms,
            max_ms=max_ms,
            min_hold_ms=min_hold_ms,
        )

        if changes:
            anno_path.write_text(json.dumps(updated_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            total_changed += len(changes)

        results[sid] = {
            "path": str(anno_path.relative_to(root)).replace("\\", "/"),
            "element_count": len(updated_doc.get("elements", [])),
            "changes": changes,
        }

    return {
        "ok": True,
        "ratio": ratio,
        "total_elements_paced": total_changed,
        "scenes": results,
    }
