#!/usr/bin/env python3
"""Deterministic sound-effect planning and PCM track rendering."""

from __future__ import annotations

import json
import math
import copy
import wave
from pathlib import Path
from typing import Any

import numpy as np

from local_defaults import load_local_settings


class SfxError(ValueError):
    pass


DEFAULT_PRESET = "classic-light"
REQUIRED_EFFECTS = {"writing", "eraser", "transition", "emphasis", "conclusion"}
OPTIONAL_EFFECTS = {"question", "reveal", "warning", "success"}
SUPPORTED_EFFECTS = REQUIRED_EFFECTS | OPTIONAL_EFFECTS

QUESTION_CUES = (
    "疑问", "问题", "困惑", "为什么", "为何", "怎么", "是否", "能不能",
    "question", "curious", "doubt",
)

VISUAL_CUE_TERMS = {
    "reveal": (
        "答案", "揭晓", "揭示", "真相", "破解", "猜对", "正确答案",
        "answer", "reveal", "discovery",
    ),
    "warning": (
        "警示", "警告", "风险", "危险", "攻击", "漏洞", "失败", "不对",
        "warning", "risk", "danger", "caution", "error",
    ),
    "success": (
        "成功", "解决", "完成", "防护", "修复", "success", "solved", "solution",
    ),
    "conclusion": (
        "结论", "总结", "归纳", "conclusion", "summary", "ending",
    ),
    "question": QUESTION_CUES,
}

INTENT_EFFECTS = {
    "question": "question",
    "curiosity": "question",
    "doubt": "question",
    "answer": "reveal",
    "reveal": "reveal",
    "discovery": "reveal",
    "warning": "warning",
    "risk": "warning",
    "danger": "warning",
    "caution": "warning",
    "error": "warning",
    "misconception": "warning",
    "success": "success",
    "correct": "success",
    "solved": "success",
    "solution": "success",
    "conclusion": "conclusion",
    "summary": "conclusion",
    "ending": "conclusion",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SfxError(f"无法读取音效配置：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise SfxError(f"音效配置顶层必须是对象：{path}")
    return value


def load_manifest(
    skill_root: Path,
    preset: str = DEFAULT_PRESET,
    manifest_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    external = manifest_path is not None
    if external:
        manifest_path = manifest_path.resolve()
        preset_root = manifest_path.parent
    else:
        preset_root = skill_root / "assets" / "sfx" / preset
        manifest_path = preset_root / "manifest.json"
    manifest = read_json(manifest_path)
    if not external and manifest.get("id") != preset:
        raise SfxError(f"音效预设 id 不一致：{manifest.get('id')} != {preset}")
    if external and not str(manifest.get("id", "")).strip():
        raise SfxError("外部音效预设缺少 id")
    if int(manifest.get("sample_rate", 0)) != 48000:
        raise SfxError("公开音效预设必须统一为 48000Hz PCM")
    effects = manifest.get("effects")
    if not isinstance(effects, dict):
        raise SfxError("音效预设缺少 effects")
    for name in REQUIRED_EFFECTS:
        record = effects.get(name)
        if not isinstance(record, dict) or not record.get("file"):
            raise SfxError(f"音效预设缺少 {name}")
    for name, record in effects.items():
        if not isinstance(record, dict) or not record.get("file"):
            raise SfxError(f"音效预设中的 {name} 缺少文件")
        asset = (preset_root / str(record["file"])).resolve()
        if not asset.is_relative_to(preset_root.resolve()):
            raise SfxError(f"音效文件必须位于清单目录内：{record['file']}")
        if not asset.is_file():
            raise SfxError(f"音效文件不存在：{asset}")
    return preset_root, manifest


def _local_sfx_settings(skill_root: Path) -> tuple[Path | None, dict[str, Any]]:
    settings_path, payload = load_local_settings(skill_root)
    if settings_path is None:
        return None, {}
    settings = payload.get("sfx")
    # A machine-local file may configure only a font, presenter, or bookends.
    # Missing SFX settings mean "use the public project/bundled default".
    if settings is None:
        return settings_path, {}
    if not isinstance(settings, dict):
        raise SfxError(f"本机设置中的 sfx 必须是对象：{settings_path}")
    return settings_path, settings


def _manifest_from_local_settings(
    skill_root: Path,
    profile: dict[str, Any],
) -> tuple[Path | None, dict[str, Any], str | None]:
    if profile.get("use_local_default") is False:
        return None, {}, None
    settings_path, settings = _local_sfx_settings(skill_root)
    value = settings.get("default_manifest")
    if not settings_path or not str(value or "").strip():
        return None, {}, None
    manifest_path = Path(str(value)).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = settings_path.parent / manifest_path
    return manifest_path, settings, "machine-local"


def _apply_gain_overrides(
    manifest: dict[str, Any],
    overrides: Any,
) -> dict[str, Any]:
    if overrides in (None, {}):
        return manifest
    if not isinstance(overrides, dict):
        raise SfxError("gain_db_overrides 必须是对象")
    result = copy.deepcopy(manifest)
    effects = result["effects"]
    for effect, value in overrides.items():
        if effect not in SUPPORTED_EFFECTS:
            raise SfxError(f"未知音效增益覆盖：{effect}")
        if effect not in effects:
            continue
        try:
            gain_db = float(value)
        except (TypeError, ValueError) as exc:
            raise SfxError(f"{effect} 的增益必须是数字") from exc
        if not -60.0 <= gain_db <= 12.0:
            raise SfxError(f"{effect} 的增益必须位于 -60dB 到 +12dB")
        effects[effect]["gain_db"] = gain_db
    return result


def _resolve_semantic_effect(intent: str, manifest: dict[str, Any]) -> str:
    """Map a semantic cue to an available sound, preserving old presets."""
    normalized = str(intent or "").strip().casefold()
    effect = INTENT_EFFECTS.get(normalized, "emphasis")
    effects = manifest["effects"]
    if effect in effects:
        return effect
    if effect in {"success", "conclusion"}:
        return "conclusion"
    return "emphasis"


def _is_question_element(element: dict[str, Any]) -> bool:
    """Use structural board metadata, not narration punctuation, for question cues."""
    structural_text = " ".join(
        str(element.get(key, ""))
        for key in ("id", "type", "label", "narrativeRole")
    ).casefold()
    return any(cue in structural_text for cue in QUESTION_CUES)


def _explicit_visual_cue(item: dict[str, Any]) -> str | None:
    value = str(item.get("sfx_cue") or item.get("sfxCue") or "").strip().casefold()
    if not value:
        return None
    if value not in INTENT_EFFECTS:
        raise SfxError(f"不支持的结构化音效语义：{value}")
    return INTENT_EFFECTS[value]


def _infer_visual_cue(shot: dict[str, Any], beat: dict[str, Any] | None = None) -> str | None:
    item = beat or shot
    explicit = _explicit_visual_cue(item)
    if explicit:
        return explicit
    if beat is None and str(shot.get("template", "")).strip().casefold() == "question":
        return "question"
    structural_text = " ".join(
        str(item.get(key, ""))
        for key in ("semantic_goal", "semanticGoal", "motion_role", "motionRole", "target", "trigger_text")
    ).casefold()
    # Reveal wins over warning when the same result beat contains both a failed
    # branch and the actual answer (for example, "失败……猜对了").
    for effect in ("reveal", "warning", "success", "conclusion", "question"):
        if any(term.casefold() in structural_text for term in VISUAL_CUE_TERMS[effect]):
            return effect
    return None


def _visual_shots_by_scene(project_root: Path) -> dict[str, list[dict[str, Any]]]:
    visual_path = project_root / "visual-plan.json"
    if not visual_path.is_file():
        return {}
    visual = read_json(visual_path)
    result: dict[str, list[dict[str, Any]]] = {}
    for shot in visual.get("shots", []):
        if not isinstance(shot, dict):
            continue
        scene_id = str(shot.get("section_id") or "").strip()
        if scene_id:
            result.setdefault(scene_id, []).append(shot)
    return result


def _normalized_target(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _target_reveal_end_ms(
    elements: list[dict[str, Any]],
    target: Any,
    scene_start_ms: int,
) -> int | None:
    """Resolve a visual-plan target to the moment its board element is fully visible."""
    needle = _normalized_target(target)
    if not needle:
        return None
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for element in elements:
        candidates = [
            _normalized_target(element.get(key))
            for key in ("id", "label", "subtitle", "narrativeRole")
        ]
        if needle in candidates:
            exact.append(element)
        elif len(needle) >= 2 and any(
            candidate and (needle in candidate or candidate in needle)
            for candidate in candidates
        ):
            partial.append(element)
    matches = exact or partial
    if len(matches) != 1:
        return None
    reveal = matches[0].get("reveal") if isinstance(matches[0].get("reveal"), dict) else {}
    try:
        start = int(reveal.get("startMs", 0))
        duration = int(reveal.get("durationMs", 0))
    except (TypeError, ValueError):
        return None
    return scene_start_ms + start + max(0, duration)


def _one_shot_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getframerate() != 48000:
            raise SfxError(f"音效必须是 48000Hz PCM16 WAV：{path}")
        return max(1, round(stream.getnframes() * 1000 / stream.getframerate()))


def _event(
    event_id: str,
    effect: str,
    start_ms: int,
    end_ms: int,
    scene_id: str,
    target_id: str | None,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    record = manifest["effects"][effect]
    return {
        "id": event_id,
        "effect": effect,
        "asset": str(record["file"]),
        "scene_id": scene_id,
        "target_id": target_id,
        "start_ms": int(start_ms),
        "end_ms": int(end_ms),
        "gain_db": float(record.get("gain_db", -24.0)),
        "loop": bool(record.get("loop", False)),
        "fade_ms": max(0, int(record.get("fade_ms", 20))),
    }


def build_plan(
    project_root: Path,
    project: dict[str, Any],
    state: dict[str, Any],
    animation_plan: dict[str, Any],
    duration_ms: int,
    skill_root: Path,
    enabled_override: bool | None = None,
) -> tuple[dict[str, Any], Path | None]:
    profile = project.get("sfx_profile") if isinstance(project.get("sfx_profile"), dict) else {}
    enabled = bool(profile.get("enabled", False)) if enabled_override is None else bool(enabled_override)
    preset = str(profile.get("preset") or DEFAULT_PRESET)
    base = {
        "version": 1,
        "enabled": enabled,
        "preset": preset,
        "timebase": "absolute-audio-ms",
        "duration_ms": int(duration_ms),
        "events": [],
    }
    # An explicit render override is authoritative and must not touch optional
    # machine-local SFX configuration at all.
    if not enabled:
        return base, None
    external_manifest: Path | None = None
    source = "bundled"
    local_settings: dict[str, Any] = {}
    if profile.get("manifest_path"):
        external_manifest = Path(str(profile["manifest_path"])).expanduser()
        if not external_manifest.is_absolute():
            external_manifest = project_root / external_manifest
        source = "external-local"
    else:
        external_manifest, local_settings, local_source = _manifest_from_local_settings(
            skill_root, profile
        )
        if local_source:
            source = local_source
    preset_root, manifest = load_manifest(skill_root, preset, external_manifest)
    gain_overrides = profile.get("gain_db_overrides")
    if gain_overrides is None and source == "machine-local":
        gain_overrides = local_settings.get("gain_db_overrides")
    manifest = _apply_gain_overrides(manifest, gain_overrides)
    if external_manifest is not None:
        preset = str(manifest["id"])
        base["preset"] = preset
    base["source"] = source
    scene_plan = {
        str(item.get("sceneId")): item
        for item in animation_plan.get("scenes", [])
        if isinstance(item, dict) and item.get("sceneId")
    }
    visual_shots = _visual_shots_by_scene(project_root)
    events: list[dict[str, Any]] = []
    for scene in project.get("scenes", []):
        scene_id = str(scene.get("id", ""))
        if not scene_id:
            continue
        scene_meta = scene_plan.get(scene_id, {})
        scene_start = int(scene_meta.get("sceneStartMs", scene.get("start_ms", 0)))
        scene_end = min(duration_ms, int(scene_meta.get("sceneEndMs", scene.get("end_ms", duration_ms))))
        board = (state.get("boards") or {}).get(scene_id)
        elements: list[dict[str, Any]] = []
        if isinstance(board, dict) and board.get("annotation"):
            annotation_path = project_root / str(board["annotation"])
            annotation = read_json(annotation_path)
            elements = sorted(
                [item for item in annotation.get("elements", []) if isinstance(item, dict)],
                key=lambda item: int(item.get("sequence", 0)),
            )
        semantic_added: set[str] = set()

        def add_semantic_cue(
            effect: str,
            cue_start: int,
            cue_id: str,
            target_id: str | None = None,
            align_after_ms: int | None = None,
        ) -> None:
            resolved = _resolve_semantic_effect(effect, manifest)
            if resolved in semantic_added:
                return
            asset = preset_root / str(manifest["effects"][resolved]["file"])
            requested_start = max(scene_start, int(cue_start))
            cue_start_clipped = max(requested_start, int(align_after_ms or scene_start))
            cue_end = min(scene_end, cue_start_clipped + _one_shot_duration_ms(asset))
            if cue_end <= cue_start_clipped:
                return
            event = _event(
                f"{scene_id}-{resolved}-{cue_id}", resolved,
                cue_start_clipped, cue_end, scene_id, target_id, manifest,
            )
            event["timing_source"] = (
                "target-reveal-end" if cue_start_clipped > requested_start else "semantic-anchor"
            )
            if cue_start_clipped > requested_start:
                event["requested_start_ms"] = requested_start
            events.append(event)
            semantic_added.add(resolved)

        for shot_index, shot in enumerate(visual_shots.get(scene_id, []), start=1):
            shot_cue_start = int(shot.get("start_ms", scene_start))
            shot_cue = _infer_visual_cue(shot)
            if shot_cue:
                add_semantic_cue(
                    shot_cue,
                    shot_cue_start,
                    f"shot-{shot_index}",
                    str(shot.get("shot_id") or "") or None,
                )
            for beat_index, beat in enumerate(shot.get("beats", []), start=1):
                if not isinstance(beat, dict):
                    continue
                beat_start = int(beat.get("start_ms", shot_cue_start))
                if (
                    shot_cue
                    and _explicit_visual_cue(beat) is None
                    and beat_start - shot_cue_start < 1000
                ):
                    # The opening beat commonly repeats the question hook text.
                    # Do not stack an inferred reveal/warning over the shot cue.
                    continue
                beat_cue = _infer_visual_cue(shot, beat)
                if beat_cue:
                    target = str(beat.get("target") or "").strip()
                    align_after = None
                    if beat_cue != "question":
                        align_after = _target_reveal_end_ms(elements, target, scene_start)
                    add_semantic_cue(
                        beat_cue,
                        beat_start,
                        f"shot-{shot_index}-beat-{beat_index}",
                        target or None,
                        align_after,
                    )

        if elements:
            for index, element in enumerate(elements, start=1):
                reveal = element.get("reveal") if isinstance(element.get("reveal"), dict) else {}
                start = max(scene_start, scene_start + int(reveal.get("startMs", 0)))
                end = min(scene_end, start + max(0, int(reveal.get("durationMs", 0))))
                if end - start < 120:
                    continue
                target_id = str(element.get("id") or f"element-{index}")
                events.append(_event(
                    f"{scene_id}-writing-{target_id}", "writing", start, end,
                    scene_id, target_id, manifest,
                ))
                if _is_question_element(element):
                    effect = _resolve_semantic_effect("question", manifest)
                    asset = preset_root / str(manifest["effects"][effect]["file"])
                    cue_start = max(start, end - _one_shot_duration_ms(asset))
                    add_semantic_cue("question", cue_start, target_id, target_id)

        for index, animation in enumerate(scene_meta.get("events", []), start=1):
            if not isinstance(animation, dict) or animation.get("effect") in {None, "skipped"}:
                continue
            start = scene_start + int(animation.get("startMs", 0))
            intent = str(animation.get("semanticIntent", "")).strip().casefold()
            if intent not in INTENT_EFFECTS:
                action = str(animation.get("action", "")).strip().casefold()
                intent = (
                    action
                    if not visual_shots.get(scene_id) and action in INTENT_EFFECTS
                    else "emphasis"
                )
            effect = _resolve_semantic_effect(intent, manifest)
            if effect in semantic_added and effect != "emphasis":
                continue
            asset = preset_root / str(manifest["effects"][effect]["file"])
            end = min(duration_ms, start + _one_shot_duration_ms(asset))
            if end > start:
                events.append(_event(
                    f"{scene_id}-{effect}-{index}", effect, start, end, scene_id,
                    str(animation.get("targetElementId") or "") or None, manifest,
                ))
                if effect != "emphasis":
                    semantic_added.add(effect)

    for transition in animation_plan.get("transitions", []):
        if not isinstance(transition, dict) or transition.get("status") not in {None, "ready"}:
            continue
        scene_id = str(transition.get("fromSceneId", "transition"))
        start = max(0, int(transition.get("eraseStartMs", 0)))
        erase_end = min(duration_ms, int(transition.get("eraseEndMs", start)))
        clean_end = min(duration_ms, int(transition.get("cleanCanvasEndMs", erase_end)))
        if erase_end > start:
            events.append(_event(
                f"{scene_id}-eraser", "eraser", start, erase_end,
                scene_id, None, manifest,
            ))
        transition_asset = preset_root / str(manifest["effects"]["transition"]["file"])
        whoosh_end = min(clean_end, start + _one_shot_duration_ms(transition_asset))
        if whoosh_end > start:
            events.append(_event(
                f"{scene_id}-transition", "transition", start, whoosh_end,
                scene_id, None, manifest,
            ))

    base["events"] = sorted(events, key=lambda item: (item["start_ms"], item["id"]))
    base["license"] = manifest.get("license", "CC0-1.0")
    return base, preset_root


def _load_pcm_mono(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as stream:
        rate = stream.getframerate()
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        frames = stream.getnframes()
        raw = stream.readframes(frames)
    if rate != 48000 or width != 2 or channels not in {1, 2}:
        raise SfxError(f"音效必须是 48kHz PCM16 mono/stereo：{path}")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    return rate, samples


def _loop_segment(source: np.ndarray, count: int) -> np.ndarray:
    """Use the most audible deterministic window when a looped event is short."""
    if count <= 0 or source.size == 0:
        return np.zeros(max(0, count), dtype=np.float32)
    if source.size <= count:
        return np.resize(source, count).astype(np.float32, copy=False)
    squared = np.square(source.astype(np.float64, copy=False))
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    energies = cumulative[count:] - cumulative[:-count]
    start = int(np.argmax(energies))
    return source[start:start + count].astype(np.float32, copy=True)


def render_track(plan: dict[str, Any], preset_root: Path, output: Path) -> dict[str, Any]:
    duration_ms = max(1, int(plan.get("duration_ms", 0)))
    sample_rate = 48000
    target_samples = max(1, math.ceil(duration_ms * sample_rate / 1000))
    track = np.zeros(target_samples, dtype=np.float32)
    cache: dict[str, np.ndarray] = {}
    applied = 0
    for event in plan.get("events", []):
        if not isinstance(event, dict):
            continue
        asset_name = str(event.get("asset", ""))
        asset_path = preset_root / asset_name
        if asset_name not in cache:
            _, cache[asset_name] = _load_pcm_mono(asset_path)
        source = cache[asset_name]
        start = max(0, round(int(event.get("start_ms", 0)) * sample_rate / 1000))
        end = min(target_samples, round(int(event.get("end_ms", 0)) * sample_rate / 1000))
        count = end - start
        if count <= 0 or source.size == 0:
            continue
        if bool(event.get("loop", False)):
            segment = _loop_segment(source, count)
        else:
            segment = np.zeros(count, dtype=np.float32)
            copied = min(count, source.size)
            segment[:copied] = source[:copied]
        gain = 10.0 ** (float(event.get("gain_db", -24.0)) / 20.0)
        segment = segment * gain
        fade_samples = min(count // 2, round(max(0, int(event.get("fade_ms", 0))) * sample_rate / 1000))
        if fade_samples > 1:
            ramp = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            segment[:fade_samples] *= ramp
            segment[-fade_samples:] *= ramp[::-1]
        track[start:end] += segment
        applied += 1

    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak > 0.82:
        track *= 0.82 / peak
        peak = 0.82
    pcm = np.round(np.clip(track, -1.0, 1.0) * 32767.0).astype("<i2")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with wave.open(str(temporary), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm.tobytes())
    temporary.replace(output)
    return {
        "path": str(output),
        "duration_ms": duration_ms,
        "events": applied,
        "peak": round(peak, 6),
        "sample_rate": sample_rate,
    }
