"""Shared integer-frame contract. Only texture and final polish are expendable."""
from __future__ import annotations
import copy

PHASE_POLICY = "protected-phases-v3"
MIN_COLOR_SWEEPS = 3
MIN_COLOR_FRAMES = 8
HAND_RELEASE_MS = 180
PROTECTED_PHASES = ("recognition", "base_color")


def frame_span(start_ms: int, duration_ms: int, fps: int) -> int:
    if fps <= 0 or duration_ms <= 0:
        raise ValueError("fps 与绘制时长必须大于零")
    return round((start_ms + duration_ms) * fps / 1000) - round(start_ms * fps / 1000)


def phase_frames(total_frames: int, original_frames: int | None = None, has_texture: bool = True, fps: int = 30) -> dict[str, int]:
    total = int(total_frames)
    original = total if original_frames is None else int(original_frames)
    if total < 1 or original < total:
        raise ValueError("绘制帧预算无效；不能扩展已批准的原始绘制窗口")
    previous_base = 1 if original < 8 else max(3, round(original * 0.20))
    base = max(MIN_COLOR_FRAMES, previous_base)
    release_frames = max(2, round(fps * HAND_RELEASE_MS / 1000))
    finalize = max(release_frames, round(original * 0.04))
    texture = round(original * 0.15) if has_texture else 0
    recognition = original - previous_base - finalize - texture
    extra_color = base - previous_base
    take = min(extra_color, finalize - release_frames)
    finalize -= take
    extra_color -= take
    take = min(extra_color, texture)
    texture -= take
    extra_color -= take
    if recognition < 1 or extra_color:
        raise ValueError("对象窗口不足以完成识别与基础色（至少 8 帧，三次描绘及两次换行）；请减少可选效果或重新规划，不能挤占保护阶段")
    phases = {"recognition": recognition, "base_color": base, "texture": texture, "finalize": finalize}
    reduction = original - total
    for phase, minimum in (("finalize", release_frames), ("texture", 0)):
        take = min(reduction, phases[phase] - minimum)
        phases[phase] -= take
        reduction -= take
    if reduction:
        raise ValueError("后动画预留侵占识别或基础色帧预算；必须取消预留并重新确认计划")
    return phases


def compile_budget(start_ms: int, duration_ms: int, original_ms: int, fps: int) -> dict:
    total = frame_span(start_ms, duration_ms, fps)
    original = frame_span(start_ms, original_ms, fps)
    return {"policy": PHASE_POLICY, "fps": fps, "startFrame": round(start_ms * fps / 1000),
            "totalFrames": total, "originalFrames": original,
            "minimumFinalizeFrames": max(2, round(fps * HAND_RELEASE_MS / 1000)),
            "baseline": phase_frames(original, fps=fps), "phases": phase_frames(total, original, fps=fps)}


def effective_annotation(annotation: dict, scene_id: str, plan: dict | None, fps: int | None = None) -> dict:
    """Apply reservations once, validating the original baseline on every path."""
    result = copy.deepcopy(annotation)
    masks = (plan or {}).get("objectMasks", {}).get(scene_id)
    if masks is not None:
        if set(masks) != {str(e.get("id")) for e in result.get("elements", [])}:
            raise ValueError("计划掩码与语义对象不一致")
        for element in result["elements"]:
            element["pixelMask"] = copy.deepcopy(masks[str(element["id"])])
    reservations = (plan or {}).get("timingReservations", [])
    if not isinstance(reservations, list):
        raise ValueError("timingReservations 必须为数组")
    elements = {str(e.get("id")): e for e in result.get("elements", [])}
    seen = set()
    for reservation in reservations:
        if not isinstance(reservation, dict):
            raise ValueError("timingReservations 条目必须为对象")
        if str(reservation.get("sceneId")) != scene_id:
            continue
        key = str(reservation.get("targetElementId") or "")
        if key not in elements or key in seen:
            raise ValueError(f"{scene_id}/{key} 的预留目标不存在或重复")
        seen.add(key)
        if reservation.get("phaseBudgetPolicy") != PHASE_POLICY:
            raise ValueError("旧时长预留缺少可执行帧预算；请显式 rebuild-plan 后重新确认")
        reveal = elements[key]["reveal"]
        original = int(reservation.get("originalDurationMs", 0))
        revised = int(reservation.get("durationMs", 0))
        current = int(reveal.get("durationMs", 0))
        marker = reveal.get("animationTimingReserve", {})
        already_applied = (current == revised and marker.get("originalDurationMs") == original
                           and marker.get("phaseBudgetPolicy") == PHASE_POLICY)
        if current != original and not already_applied:
            raise ValueError(f"{scene_id}/{key} 预留所依据的原始时长已变化")
        if not 0 < revised < original:
            raise ValueError(f"{scene_id}/{key} 预留时长无效")
        # Validate the formal rate here; a requested preview compiles its own rate.
        for rate in [fps if fps is not None else int((plan or {}).get("renderSettings", {}).get("fps", 30))]:
            compile_budget(int(reveal["startMs"]), revised, original, rate)
        reveal["durationMs"] = revised
        reveal["animationTimingReserve"] = {
            "phaseBudgetPolicy": PHASE_POLICY, "originalDurationMs": original,
            "compressedMs": original - revised, "reservedMs": reservation.get("reservedMs"),
            "effect": reservation.get("effect"), "compressedPhases": ["texture", "finalize"],
            "protectedPhases": list(PROTECTED_PHASES), "audioExtendedMs": 0,
        }
    for key, element in elements.items():
        reveal = element["reveal"]
        if reveal.get("animationTimingReserve") and key not in seen:
            raise ValueError(f"{scene_id}/{key} 标注带有计划之外的时长预留")
        if fps is not None:
            original = int(reveal.get("animationTimingReserve", {}).get("originalDurationMs", reveal["durationMs"]))
            reveal["frameBudget"] = compile_budget(int(reveal["startMs"]), int(reveal["durationMs"]), original, fps)
    return result


def plan_budgets(annotations: dict[str, dict], plan: dict) -> dict:
    """Formal-output budgets for plan review; preview rates use this same compiler."""
    budgets = {}
    fps = int(plan.get("renderSettings", {}).get("fps", 30))
    if not 8 <= fps <= 60:
        raise ValueError("正式帧率必须在 8–60 范围内")
    for sid, annotation in annotations.items():
        effective = effective_annotation(annotation, sid, plan, fps)
        budgets[sid] = {}
        for element in effective["elements"]:
            reveal = element["reveal"]
            budgets[sid][str(element["id"])] = {
                "startMs": reveal["startMs"], "durationMs": reveal["durationMs"],
                "originalDurationMs": reveal.get("animationTimingReserve", {}).get("originalDurationMs", reveal["durationMs"]),
                "frameBudget": reveal["frameBudget"],
            }
    return budgets
