#!/usr/bin/env python3
"""
SRT 白板动画 - 整合渲染器（mask 编排 + stream 画法）

把一张线稿图 + 同名 annotation.json 渲染成白板手绘动画：
  - 编排沿用 whiteboard-mask-animation：按 sequence/startMs 顺序逐区域揭示。
    源图上的语义对象必须独占前景，QA、动画和绘制共用同一归属图；
    重叠、遗漏或被切开的对象必须先修正，不能靠顺序或距离分配像素。
  - 默认 layered 画法：每个区域按视觉对象依次完成结构锚点、身份细节、
    基础色和局部润色；笔尖跟随真实骨架/网格路径，长结构线较快，闭合与
    中心识别细节较慢。legacy 保留旧逐区域 ink → color 兼容。

与 mask 的矩形擦除揭示不同：这里是「笔尖沿线滑行、边走边落墨」的连贯笔迹。
输出末行打印 OUTPUT=<路径>，便于上层捕获。

用法：
  <ENV_PY> render_stream_whiteboard.py <图片> <标注json> <输出mp4> [手部素材png]
  可选参数见 --help（--ink-path / --color-fill / --pause / --total-ms 等）。
  --total-ms 缺省时用标注里的 sceneDurationMs。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageFont

# 复用 stream 渲染器的全部构件（同目录）
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
import stream_render as sr  # noqa: E402
from animation_overlay import AnimationOverlay  # noqa: E402
from presenter_runtime import resolve_presenter_manifest  # noqa: E402
from pixel_contract import require_ownership, PIXEL_POLICY, region_mask
from phase_budget import MIN_COLOR_FRAMES, MIN_COLOR_SWEEPS, HAND_RELEASE_MS, phase_frames, compile_budget, frame_span, effective_annotation

_PUBLIC_SCRIPTS_DIR = _SCRIPT_DIR.parents[1] / "scripts"
if str(_PUBLIC_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_PUBLIC_SCRIPTS_DIR))
from title_card import render_concept_card  # noqa: E402

DEFAULT_HAND = _SCRIPT_DIR.parent / "assets" / "drawing-hand.png"
DEFAULT_ERASER = _SCRIPT_DIR.parent / "assets" / "eraser-hand.png"
DEFAULT_MANIFEST = _SCRIPT_DIR.parent / "assets" / "hand-assets.json"
SMALL_HAND_RATIO = 0.14
PRESENTER_HAND_RATIO = 0.22
FULL_HAND_REFERENCE_HEIGHT = 493
HAND_FADE_IN_MS = 120
HAND_FADE_OUT_MS = HAND_RELEASE_MS
HAND_DRAW_MAX_STEP_SHORT_EDGE_RATIO = 0.018
HAND_TRAVEL_MAX_STEP_SHORT_EDGE_RATIO = 0.022
HAND_COLOR_MAX_STEP_SHORT_EDGE_RATIO = 0.022
HAND_QA_HOLD_STEP_SHORT_EDGE_RATIO = 0.0015
HAND_QA_JUMP_STEP_SHORT_EDGE_RATIO = 0.012
HAND_QA_MAX_STEP_DELTA_SHORT_EDGE_RATIO = 0.0055
HAND_PLANNER_STEP_DELTA_SHORT_EDGE_RATIO = 0.003


@dataclass(frozen=True)
class MotionFrame:
    x: float
    y: float
    sample_index: int
    pen_down: bool
    state: str


class HandMotionQATracker:
    """Compute frame-by-frame hand kinematics without storing a full trace."""

    def __init__(self, fps: int, short_edge: int) -> None:
        self.fps = max(1, int(fps))
        self.short_edge = max(1, int(short_edge))
        self.total_frames = 0
        self.visible_frames = 0
        self.displacements: list[float] = []
        self.step_deltas: list[float] = []
        self.previous: dict | None = None
        self.previous_step = 0.0
        self.hidden_run = 0
        self.hidden_after_visible: dict | None = None
        self.abrupt_disappearances = 0
        self.abrupt_events: list[dict] = []
        self.single_frame_gaps = 0
        self.hold_then_jump = 0
        self.violations: list[dict] = []

    @staticmethod
    def _limit_for_state(state: str) -> float:
        if state == "draw":
            return HAND_DRAW_MAX_STEP_SHORT_EDGE_RATIO
        if state in {"travel", "color"}:
            return HAND_TRAVEL_MAX_STEP_SHORT_EDGE_RATIO
        return HAND_COLOR_MAX_STEP_SHORT_EDGE_RATIO

    def add(self, frame_index: int, hand: dict | None) -> None:
        self.total_frames += 1
        current = hand if isinstance(hand, dict) else {}
        visible = bool(current.get("visible")) and float(current.get("opacity", 0.0)) > 0.0
        if not visible:
            if self.previous is not None:
                self.hidden_run += 1
                if self.hidden_run == 1:
                    self.hidden_after_visible = self.previous
                    if float(self.previous.get("opacity", 0.0)) > 0.35:
                        self.abrupt_disappearances += 1
                        self.abrupt_events.append({
                            "frame": int(frame_index),
                            "time_ms": round(frame_index * 1000 / self.fps),
                            "kind": "abrupt-disappearance",
                            "previous_frame": int(self.previous.get("frame", frame_index - 1)),
                            "previous_opacity": round(float(self.previous.get("opacity", 0.0)), 5),
                            "previous_x": round(float(self.previous.get("x", 0.0)), 2),
                            "previous_y": round(float(self.previous.get("y", 0.0)), 2),
                        })
            return

        self.visible_frames += 1
        current = {
            "frame": int(frame_index),
            "x": float(current["x"]),
            "y": float(current["y"]),
            "opacity": float(current.get("opacity", 1.0)),
            "state": str(current.get("state") or "draw"),
            "pen_down": bool(current.get("pen_down")),
        }
        if self.hidden_run == 1 and self.hidden_after_visible is not None:
            if float(self.hidden_after_visible.get("opacity", 0.0)) > 0.35 and current["opacity"] > 0.35:
                self.single_frame_gaps += 1
                self.violations.append({
                    "frame": int(frame_index),
                    "time_ms": round(frame_index * 1000 / self.fps),
                    "kind": "single-frame-gap",
                })
        if self.hidden_run:
            self.previous = None
            self.previous_step = 0.0
        self.hidden_run = 0
        self.hidden_after_visible = None

        if self.previous is not None:
            distance = math.hypot(current["x"] - self.previous["x"], current["y"] - self.previous["y"])
            step = distance / self.short_edge
            self.displacements.append(step)
            if current["state"] in {"fast-draw", "fast-color"} or self.previous.get("state") in {
                "fast-draw", "fast-color"
            }:
                self.previous_step = step
                self.previous = current
                return
            limit = self._limit_for_state(current["state"])
            if step > limit + 1e-9:
                self.violations.append({
                    "frame": int(frame_index),
                    "time_ms": round(frame_index * 1000 / self.fps),
                    "kind": "max-step",
                    "state": current["state"],
                    "value": round(step, 5),
                    "limit": round(limit, 5),
                })
            if self.previous_step <= HAND_QA_HOLD_STEP_SHORT_EDGE_RATIO and step > HAND_QA_JUMP_STEP_SHORT_EDGE_RATIO:
                self.hold_then_jump += 1
                self.violations.append({
                    "frame": int(frame_index),
                    "time_ms": round(frame_index * 1000 / self.fps),
                    "kind": "hold-then-jump",
                    "value": round(step, 5),
                    "limit": HAND_QA_JUMP_STEP_SHORT_EDGE_RATIO,
                })
            step_delta = abs(step - self.previous_step)
            self.step_deltas.append(step_delta)
            if step_delta > HAND_QA_MAX_STEP_DELTA_SHORT_EDGE_RATIO:
                self.violations.append({
                    "frame": int(frame_index),
                    "time_ms": round(frame_index * 1000 / self.fps),
                    "kind": "step-delta",
                    "value": round(step_delta, 5),
                    "limit": HAND_QA_MAX_STEP_DELTA_SHORT_EDGE_RATIO,
                    "step": round(step, 5),
                    "previous_step": round(self.previous_step, 5),
                    "state": current["state"],
                })
            self.previous_step = step
        else:
            self.previous_step = 0.0
        self.previous = current

    def summary(self) -> dict:
        values = np.asarray(self.displacements, dtype=np.float64)
        maximum = float(values.max()) if values.size else 0.0
        p95 = float(np.percentile(values, 95)) if values.size else 0.0
        deltas = np.asarray(self.step_deltas, dtype=np.float64)
        max_delta = float(deltas.max()) if deltas.size else 0.0
        failures = len(self.violations) + self.abrupt_disappearances
        worst = sorted(
            [*self.violations, *self.abrupt_events],
            key=lambda item: float(item.get("value", 0.0)),
            reverse=True,
        )[:5]
        return {
            "version": "hand-motion-qa-v1",
            "status": "passed" if failures == 0 else "failed",
            "frames": self.total_frames,
            "visible_frames": self.visible_frames,
            "max_step_short_edges": round(maximum, 5),
            "p95_step_short_edges": round(p95, 5),
            "max_step_px": round(maximum * self.short_edge, 2),
            "max_step_delta_short_edges": round(max_delta, 5),
            "hold_then_jump_count": self.hold_then_jump,
            "single_frame_gap_count": self.single_frame_gaps,
            "abrupt_disappearance_count": self.abrupt_disappearances,
            "violation_count": failures,
            "thresholds": {
                "draw_max_step_short_edges": HAND_DRAW_MAX_STEP_SHORT_EDGE_RATIO,
                "travel_max_step_short_edges": HAND_TRAVEL_MAX_STEP_SHORT_EDGE_RATIO,
                "hold_then_jump_short_edges": HAND_QA_JUMP_STEP_SHORT_EDGE_RATIO,
                "max_step_delta_short_edges": HAND_QA_MAX_STEP_DELTA_SHORT_EDGE_RATIO,
            },
            "worst_events": worst,
        }


class FrameCountWriter:
    """Keep renderer output on an exact frame budget without changing draw logic."""

    def __init__(
        self,
        writer,
        target_frames: int | None = None,
        *,
        fps: int = 30,
        short_edge: int = 1080,
        frame_overlay: tuple[np.ndarray, int, int] | None = None,
    ) -> None:
        self.writer = writer
        self.target_frames = target_frames
        self.frames_written = 0
        self.frames_attempted = 0
        self.last_frame: np.ndarray | None = None
        self.last_hand: dict | None = None
        self.hand_qa = HandMotionQATracker(fps, short_edge)
        self.frame_overlay = frame_overlay

    def isOpened(self) -> bool:
        return bool(self.writer.isOpened())

    def write(self, frame: np.ndarray, hand: dict | None = None) -> None:
        self.frames_attempted += 1
        frame = _composite_frame_overlay(frame, self.frame_overlay)
        self.last_frame = frame
        self.last_hand = hand
        if self.target_frames is not None and self.frames_written >= self.target_frames:
            return
        self.writer.write(frame)
        self.hand_qa.add(self.frames_written, hand)
        self.frames_written += 1

    def release(self) -> None:
        if self.target_frames is not None and self.last_frame is not None:
            while self.frames_written < self.target_frames:
                self.writer.write(self.last_frame)
                self.hand_qa.add(self.frames_written, self.last_hand)
                self.frames_written += 1
        self.writer.release()


def _composite_frame_overlay(
    frame_bgr: np.ndarray,
    overlay: tuple[np.ndarray, int, int] | None,
) -> np.ndarray:
    if overlay is None:
        return frame_bgr
    rgba, x, y = overlay
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("文字卡片覆盖层必须是 RGBA 图片")
    frame = frame_bgr.copy()
    height, width = rgba.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.shape[1], x + width), min(frame.shape[0], y + height)
    if x1 <= x0 or y1 <= y0:
        return frame
    source = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    alpha = source[:, :, 3:4].astype(np.float32) / 255.0
    source_bgr = source[:, :, :3][:, :, ::-1].astype(np.float32)
    target = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = np.rint(source_bgr * alpha + target * (1.0 - alpha)).astype(np.uint8)
    return frame


def _build_title_card_overlay(
    text: str | None,
    accent: str,
    font_path: str | None,
    out_w: int,
    out_h: int,
) -> tuple[np.ndarray, int, int] | None:
    content = " ".join(str(text or "").split())
    if not content:
        return None
    short_edge = min(out_w, out_h)
    font_size = max(22, round(short_edge * 0.032))
    font = ImageFont.truetype(font_path, font_size) if font_path else None
    card = render_concept_card(
        content,
        font_size=font_size,
        font=font,
        border_width=max(2, round(short_edge * 0.0022)),
        corner_radius=max(8, round(short_edge * 0.009)),
        padding_x=max(14, round(short_edge * 0.018)),
        padding_y=max(8, round(short_edge * 0.008)),
        accent_bar_color=accent,
    )
    return np.asarray(card, dtype=np.uint8), round(out_w * 0.025), round(out_h * 0.028)


def _read_asset_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _manifest_asset(manifest: dict, kind: str, fallback: Path) -> tuple[Path, float, float]:
    record = manifest.get("assets", {}).get(kind, {})
    filename = record.get("runtime_file") if isinstance(record, dict) else None
    anchor = record.get("anchor", {}) if isinstance(record, dict) else {}
    path = fallback.parent / str(filename) if filename else fallback
    return path, float(anchor.get("x", 0.0)), float(anchor.get("y", 0.0))


# ──────────────────────────────────────────────────────────────
# 区域几何：把标注画布坐标缩放到输出尺寸
# ──────────────────────────────────────────────────────────────
def _scaled_rect(region: dict, sx: float, sy: float, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    x0 = int(round(region["x"] * sx))
    y0 = int(round(region["y"] * sy))
    x1 = int(round((region["x"] + region["width"]) * sx))
    y1 = int(round((region["y"] + region["height"]) * sy))
    x0 = max(0, min(out_w, x0))
    x1 = max(0, min(out_w, x1))
    y0 = max(0, min(out_h, y0))
    y1 = max(0, min(out_h, y1))
    return x0, y0, x1, y1


def _frame_progress_indices(n_steps: int, target_frames: int) -> list[int]:
    """把 n_steps 个笔尖位置均匀映射到 target_frames 帧。"""
    if n_steps == 0 or target_frames <= 0:
        return []
    if target_frames == 1:
        return [n_steps - 1]
    return [round(f * (n_steps - 1) / (target_frames - 1)) for f in range(target_frames)]


def _fast_trace_schedule(
    sample_count: int,
    pen_lifts: set[int],
    target_frames: int,
) -> list[int]:
    """Give each semantic stroke screen time, then distribute spare frames by length."""
    if sample_count <= 0 or target_frames <= 0:
        return []
    starts = [0, *sorted(index for index in pen_lifts if 0 < index < sample_count)]
    ranges = [(start, starts[index + 1] if index + 1 < len(starts) else sample_count) for index, start in enumerate(starts)]
    if target_frames < len(ranges):
        return _frame_progress_indices(sample_count, target_frames)

    allocations = [1] * len(ranges)
    spare = target_frames - len(ranges)
    weights = [max(1, end - start - 1) for start, end in ranges]
    if spare > 0:
        total_weight = sum(weights)
        raw = [spare * weight / total_weight for weight in weights]
        additions = [int(math.floor(value)) for value in raw]
        remainder = spare - sum(additions)
        order = sorted(range(len(raw)), key=lambda index: raw[index] - additions[index], reverse=True)
        for index in order[:remainder]:
            additions[index] += 1
        allocations = [base + addition for base, addition in zip(allocations, additions)]

    schedule: list[int] = []
    for (start, end), frames in zip(ranges, allocations):
        local = _frame_progress_indices(end - start, frames)
        schedule.extend(start + index for index in local)
    return schedule[:target_frames]


@dataclass(frozen=True)
class MotionEvent:
    start_index: int
    end_index: int
    pen_down: bool
    state: str
    length: float
    cumulative: tuple[float, ...]
    timing_length: float
    timing_cumulative: tuple[float, ...]
    min_timing_weight: float


def _smootherstep(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * value * (value * (value * 6.0 - 15.0) + 10.0)


def _motion_events(
    samples: list[tuple[int, int]],
    pen_lifts: set[int],
) -> list[MotionEvent]:
    if len(samples) < 2:
        return []
    events: list[MotionEvent] = []
    start = 0
    current_pen_down = 1 not in pen_lifts
    for index in range(2, len(samples)):
        pen_down = index not in pen_lifts
        if pen_down != current_pen_down:
            points = samples[start:index]
            cumulative = tuple(_stroke_cumulative_length_int(points))
            timing_cumulative, min_weight = _stroke_timing_cumulative(points)
            events.append(MotionEvent(
                start_index=start,
                end_index=index - 1,
                pen_down=current_pen_down,
                state="draw" if current_pen_down else "travel",
                length=cumulative[-1],
                cumulative=cumulative,
                timing_length=timing_cumulative[-1],
                timing_cumulative=tuple(timing_cumulative),
                min_timing_weight=min_weight,
            ))
            start = index - 1
            current_pen_down = pen_down
    points = samples[start:]
    cumulative = tuple(_stroke_cumulative_length_int(points))
    timing_cumulative, min_weight = _stroke_timing_cumulative(points)
    events.append(MotionEvent(
        start_index=start,
        end_index=len(samples) - 1,
        pen_down=current_pen_down,
        state="draw" if current_pen_down else "travel",
        length=cumulative[-1],
        cumulative=cumulative,
        timing_length=timing_cumulative[-1],
        timing_cumulative=tuple(timing_cumulative),
        min_timing_weight=min_weight,
    ))
    return [event for event in events if event.length > 1e-6]


def _stroke_cumulative_length_int(points: list[tuple[int, int]]) -> list[float]:
    cumulative = [0.0]
    for first, second in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + math.hypot(second[0] - first[0], second[1] - first[1]))
    return cumulative


def _stroke_timing_cumulative(points: list[tuple[int, int]]) -> tuple[list[float], float]:
    """Build a gently smoothed curvature cost without abrupt semantic scales."""
    if len(points) < 2:
        return [0.0], 1.0
    lengths = [
        max(1e-6, math.hypot(second[0] - first[0], second[1] - first[1]))
        for first, second in zip(points, points[1:])
    ]
    curvature = [0.0] * len(lengths)
    for index in range(1, len(points) - 1):
        first = points[index - 1]
        middle = points[index]
        last = points[index + 1]
        ax, ay = middle[0] - first[0], middle[1] - first[1]
        bx, by = last[0] - middle[0], last[1] - middle[1]
        denom = math.hypot(ax, ay) * math.hypot(bx, by)
        cosine = (ax * bx + ay * by) / denom if denom > 1e-9 else 1.0
        turn = math.acos(max(-1.0, min(1.0, cosine))) / math.pi
        curvature[index - 1] = max(curvature[index - 1], turn)
        curvature[index] = max(curvature[index], turn)
    raw_weights = [1.0 + 0.40 * value for value in curvature]
    weights: list[float] = []
    for index, value in enumerate(raw_weights):
        previous = raw_weights[index - 1] if index > 0 else value
        following = raw_weights[index + 1] if index + 1 < len(raw_weights) else value
        weights.append((previous + 2.0 * value + following) / 4.0)
    cumulative = [0.0]
    for length, weight in zip(lengths, weights):
        cumulative.append(cumulative[-1] + length * weight)
    return cumulative, min(weights)


def _event_min_intervals(
    event: MotionEvent,
    short_edge: int,
    draw_max_step_ratio: float,
    travel_max_step_ratio: float,
) -> int:
    ratio = draw_max_step_ratio if event.pen_down else travel_max_step_ratio
    max_step = max(1.0, short_edge * max(0.001, ratio))
    # Leave room for integer-pixel raster rounding before the QA threshold.
    max_step_delta = max(0.5, short_edge * HAND_PLANNER_STEP_DELTA_SHORT_EDGE_RATIO)
    peak_distance_per_progress = event.timing_length / max(1e-6, event.min_timing_weight)
    # Solve the discrete easing envelope directly. This constrains both the
    # largest step and the change between adjacent steps, including the start
    # from rest and the final settle back to rest.
    for intervals in range(2, 601):
        progress = [_smootherstep(index / intervals) for index in range(intervals + 1)]
        normalized_steps = [b - a for a, b in zip(progress, progress[1:])]
        largest_step = max(normalized_steps) * peak_distance_per_progress
        with_rest = [0.0, *normalized_steps, 0.0]
        largest_delta = max(abs(b - a) for a, b in zip(with_rest, with_rest[1:])) * peak_distance_per_progress
        if largest_step <= max_step + 1e-9 and largest_delta <= max_step_delta + 1e-9:
            return intervals
    return 600


def _event_position(
    event: MotionEvent,
    samples: list[tuple[int, int]],
    progress: float,
) -> MotionFrame:
    timing_distance = _smootherstep(progress) * event.timing_length
    local_segment = min(
        len(event.timing_cumulative) - 2,
        max(0, int(np.searchsorted(event.timing_cumulative, timing_distance, side="right")) - 1),
    )
    start_index = event.start_index + local_segment
    end_index = min(event.end_index, start_index + 1)
    segment_start = event.timing_cumulative[local_segment]
    segment_end = event.timing_cumulative[local_segment + 1]
    span = max(1e-9, segment_end - segment_start)
    amount = max(0.0, min(1.0, (timing_distance - segment_start) / span))
    first = samples[start_index]
    second = samples[end_index]
    return MotionFrame(
        x=first[0] + (second[0] - first[0]) * amount,
        y=first[1] + (second[1] - first[1]) * amount,
        sample_index=end_index,
        pen_down=event.pen_down,
        state=event.state,
    )


def _plan_continuous_motion(
    samples: list[tuple[int, int]],
    target_frames: int,
    pen_lifts: set[int],
    *,
    fps: int,
    short_edge: int,
    draw_max_step_ratio: float = HAND_DRAW_MAX_STEP_SHORT_EDGE_RATIO,
    travel_max_step_ratio: float = HAND_TRAVEL_MAX_STEP_SHORT_EDGE_RATIO,
) -> tuple[list[MotionFrame], dict]:
    """Plan eased whole-stroke motion without compressing an infeasible path."""
    del fps  # Ratios are per output frame; fps remains explicit at call sites.
    if target_frames <= 0 or not samples:
        return [], {
            "completed": not samples,
            "motion_frames": 0,
            "hold_frames": 0,
            "completed_sample_index": -1,
        }
    if target_frames == 1 or len(samples) == 1:
        point = samples[0]
        return [MotionFrame(float(point[0]), float(point[1]), 0, False, "hold")], {
            "completed": len(samples) <= 1,
            "motion_frames": 1,
            "hold_frames": 0,
            "completed_sample_index": 0,
        }

    events = _motion_events(samples, pen_lifts)
    pending_air: list[MotionEvent] = []
    groups: list[list[MotionEvent]] = []
    for event in events:
        if event.pen_down:
            groups.append([*pending_air, event])
            pending_air = []
        else:
            pending_air.append(event)

    available_intervals = target_frames - 1
    selected: list[MotionEvent] = []
    required_intervals: list[int] = []
    selected_group_count = 0
    for group in groups:
        group_required = [
            _event_min_intervals(
                event,
                short_edge,
                draw_max_step_ratio,
                travel_max_step_ratio,
            )
            for event in group
        ]
        if sum(required_intervals) + sum(group_required) > available_intervals:
            break
        selected.extend(group)
        required_intervals.extend(group_required)
        selected_group_count += 1

    completed = selected_group_count == len(groups) and not pending_air
    used_intervals = sum(required_intervals)
    allocated = list(required_intervals)
    if completed and selected and used_intervals < available_intervals:
        extra = available_intervals - used_intervals
        total_weight = max(1, sum(required_intervals))
        raw = [extra * value / total_weight for value in required_intervals]
        additions = [int(math.floor(value)) for value in raw]
        remaining = extra - sum(additions)
        order = sorted(range(len(raw)), key=lambda index: raw[index] - additions[index], reverse=True)
        for index in order[:remaining]:
            additions[index] += 1
        allocated = [base + addition for base, addition in zip(required_intervals, additions)]

    first = samples[0]
    frames = [MotionFrame(float(first[0]), float(first[1]), 0, bool(selected and selected[0].pen_down), "draw" if selected and selected[0].pen_down else "hold")]
    for event, intervals in zip(selected, allocated):
        for step in range(1, intervals + 1):
            frames.append(_event_position(event, samples, step / intervals))

    motion_frame_count = len(frames)
    last = frames[-1]
    while len(frames) < target_frames:
        frames.append(MotionFrame(last.x, last.y, last.sample_index, False, "hold"))

    max_step = 0.0
    for previous, current in zip(frames, frames[1:]):
        max_step = max(max_step, math.hypot(current.x - previous.x, current.y - previous.y) / max(1, short_edge))
    return frames, {
        "completed": completed,
        "motion_frames": motion_frame_count,
        "hold_frames": max(0, target_frames - motion_frame_count),
        "completed_sample_index": last.sample_index,
        "selected_events": len(selected),
        "total_events": len(events),
        "max_planned_step_short_edges": round(max_step, 5),
    }


def _motion_progress_indices(
    samples: list[tuple[int, int]],
    target_frames: int,
    pen_lifts: set[int],
    effort_scales: list[float] | None = None,
) -> list[int]:
    """Compatibility wrapper around the continuous planner.

    Semantic stage scales are intentionally ignored: abrupt per-stage speed
    multipliers caused local acceleration spikes even when average speed passed.
    """
    del effort_scales
    if not samples or target_frames <= 0:
        return []
    xs = [point[0] for point in samples]
    ys = [point[1] for point in samples]
    short_edge = max(1, min(max(xs) - min(xs) + 1, max(ys) - min(ys) + 1))
    frames, _ = _plan_continuous_motion(
        samples,
        target_frames,
        pen_lifts,
        fps=30,
        short_edge=short_edge,
        draw_max_step_ratio=1.0,
        travel_max_step_ratio=1.0,
    )
    return [frame.sample_index for frame in frames]


def _expand_pen_lift_travel(
    samples: list[tuple[int, int]],
    pen_lifts: set[int],
    max_step_px: float,
    effort_scales: list[float] | None = None,
) -> tuple[list[tuple[int, int]], set[int], list[float] | None]:
    """Insert visible air-travel points between disconnected strokes.

    Every inserted point remains a pen lift, so it moves the hand without
    drawing a false connector. This replaces one-frame hiding and teleporting.
    """
    if not samples:
        return [], set(), [] if effort_scales is not None else None
    max_step = max(4.0, float(max_step_px))
    expanded = [samples[0]]
    expanded_lifts: set[int] = set()
    expanded_scales = [float(effort_scales[0])] if effort_scales else None
    for index in range(1, len(samples)):
        current = samples[index]
        if index in pen_lifts:
            previous = samples[index - 1]
            distance = math.hypot(current[0] - previous[0], current[1] - previous[1])
            steps = max(1, int(math.ceil(distance / max_step)))
            for step in range(1, steps + 1):
                point = (
                    round(previous[0] + (current[0] - previous[0]) * step / steps),
                    round(previous[1] + (current[1] - previous[1]) * step / steps),
                )
                expanded_lifts.add(len(expanded))
                expanded.append(point)
                if expanded_scales is not None:
                    expanded_scales.append(1.0)
        else:
            expanded.append(current)
            if expanded_scales is not None:
                value = effort_scales[index] if index < len(effort_scales) else 1.0
                expanded_scales.append(float(value))
    return expanded, expanded_lifts, expanded_scales


def _fade_opacities(frames: int, fps: int, duration_ms: int, *, fade_in: bool) -> list[float]:
    """Return a short opacity envelope without changing the frame budget."""
    if frames <= 0:
        return []
    fade_frames = min(frames, max(1, round(max(1, fps) * duration_ms / 1000)))
    values: list[float] = []
    for index in range(frames):
        if index >= fade_frames:
            values.append(1.0 if fade_in else 0.0)
            continue
        progress = (index + 1) / fade_frames
        values.append(progress if fade_in else 1.0 - progress)
    return values


def _stroke_length(stroke: list[tuple[int, int]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(stroke, stroke[1:]))


def _progressive_phase_frames(
    total_frames: int,
    color_ratio: float,
    has_texture: bool,
    original_total_frames: int | None = None,
    fps: int = 30,
) -> dict[str, int]:
    """Use the same protected baseline as the upstream reservation planner."""
    return phase_frames(total_frames, original_total_frames, has_texture, fps=fps)


def _prepare_style_ink_maps(
    color_img: np.ndarray,
    cfg: sr.Config,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool, np.ndarray]:
    """Build ink maps while preserving the existing semantic planner.

    Four-corner sampling decides whether a board is dark. Light boards keep
    adaptive thresholding; dark boards use local contrast and bright-edge
    extraction so pale chalk strokes become drawable ink.
    """

    height, width = color_img.shape[:2]
    edge = max(8, min(height, width) // 35)
    corners = np.concatenate([
        color_img[:edge, :edge].reshape(-1, 3),
        color_img[:edge, -edge:].reshape(-1, 3),
        color_img[-edge:, :edge].reshape(-1, 3),
        color_img[-edge:, -edge:].reshape(-1, 3),
    ])
    background_bgr = np.median(corners.astype(np.float32), axis=0).astype(np.uint8)
    background_luma = float(
        background_bgr[0] * 0.114
        + background_bgr[1] * 0.587
        + background_bgr[2] * 0.299
    )
    dark_mode = background_luma < 96.0
    gray = cv2.cvtColor(color_img, cv2.COLOR_BGR2GRAY)
    if dark_mode:
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        edges = cv2.Canny(enhanced, 28, 90, L2gradient=True)
        edges = cv2.morphologyEx(
            edges,
            cv2.MORPH_CLOSE,
            np.ones((3, 3), dtype=np.uint8),
        )
        thresh_map = np.where(edges > 0, 0, 255).astype(np.uint8)
        ink_paint = color_img.astype(np.float32)
    else:
        thresh_map = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 10
        )
        ink_paint = np.repeat(thresh_map[:, :, None], 3, axis=2).astype(np.float32)
    ink_pixels = thresh_map < cfg.ink_threshold
    return thresh_map, ink_pixels, ink_paint, dark_mode, background_bgr


# ──────────────────────────────────────────────────────────────
# 每区域的 stream 笔迹渲染，写入共享持久画布
# ──────────────────────────────────────────────────────────────
class RegionStreamRenderer:
    """持有整段渲染的共享状态；逐区域把 stream 笔迹画进同一张画布。"""

    def __init__(
        self,
        image_bgr: np.ndarray,
        annotation: dict,
        cfg: sr.Config,
        hand_png: Path | None,
        hand_mode: str,
        eraser_png: Path | None,
        animation_plan: dict | None,
        scene_id: str | None,
        transition: dict | None,
        asset_manifest: Path | None,
        draw_mode: str,
        stroke_planner: str,
        color_reserve_ratio: float,
        minimum_color_ms: int,
    ) -> None:
        if hand_mode not in {"small-hand", "presenter", "no-hand", "full-hand"}:
            raise ValueError(f"不支持的 hand mode：{hand_mode}")
        self.hand_mode = hand_mode
        self.ann = annotation
        self.transition = transition
        drawing_plan = annotation.get("drawingPlan", {}) if isinstance(annotation.get("drawingPlan"), dict) else {}
        requested_mode = str(drawing_plan.get("mode") or draw_mode or "layered")
        self.draw_mode = requested_mode if requested_mode in {"layered", "legacy"} else "layered"
        requested_planner = str(drawing_plan.get("strokePlanner") or stroke_planner or "semantic-v2")
        self.stroke_planner = requested_planner if requested_planner in {"semantic-v2", "reading-bands-v1"} else "semantic-v2"
        self.color_reserve_ratio = float(
            drawing_plan.get("colorReserveRatio", color_reserve_ratio)
        )
        self.color_reserve_ratio = max(0.18, min(0.45, self.color_reserve_ratio))
        self.minimum_color_ms = max(240, int(drawing_plan.get("minimumColorMs", minimum_color_ms)))
        requested_color_schedule = str(drawing_plan.get("colorSchedule") or "object-progressive-v1")
        self.color_schedule = requested_color_schedule if requested_color_schedule in {
            "object-progressive-v1", "scene-final-v1"
        } else "object-progressive-v1"
        # Legacy field spellings do not authorize guessing semantic ownership.
        self.pixel_ownership = PIXEL_POLICY
        self.source_owner, self.source_foreground, self.source_ownership_metrics = require_ownership(
            cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB), annotation
        )
        self.canvas_bgr = sr._hex_to_bgr(cfg.canvas_hex)

        # 输出尺寸：长边限到 cap，对齐到 grid_edge 的偶数倍（编码要求偶数）
        h0, w0 = image_bgr.shape[:2]
        scale = cfg.cap_long_edge / max(h0, w0)
        align = cfg.grid_edge if cfg.grid_edge % 2 == 0 else cfg.grid_edge * 2
        w = max(align, (int(round(w0 * scale)) // align) * align)
        h = max(align, (int(round(h0 * scale)) // align) * align)
        self.out_w, self.out_h = w, h
        short_edge = min(self.out_w, self.out_h)
        manifest_path = asset_manifest or DEFAULT_MANIFEST
        if hand_mode == "presenter" and manifest_path == DEFAULT_MANIFEST:
            manifest_path = resolve_presenter_manifest()
        manifest = _read_asset_manifest(manifest_path)
        self.presenter_min_visible_ratio = float(
            manifest.get("clipping_policy", {}).get("minimum_visible_ratio", 0.97)
        )
        if hand_mode == "small-hand":
            target_hand_height = max(1, round(short_edge * SMALL_HAND_RATIO))
        elif hand_mode == "presenter":
            ratio = float(manifest.get("height_policy", {}).get("ratio_of_output_short_edge", PRESENTER_HAND_RATIO))
            target_hand_height = max(1, round(short_edge * ratio))
        else:
            target_hand_height = max(1, round(FULL_HAND_REFERENCE_HEIGHT * short_edge / 1080))
        cfg = replace(cfg, target_hand_height=target_hand_height)
        self.cfg = cfg

        # 标注画布坐标 → 输出坐标的缩放比
        cw = annotation["canvas"]["width"]
        ch = annotation["canvas"]["height"]
        self.sx = self.out_w / cw
        self.sy = self.out_h / ch

        t_mask_start = time.perf_counter()
        self.color_img = cv2.resize(image_bgr, (self.out_w, self.out_h), interpolation=cv2.INTER_AREA)
        (
            self.thresh_map,
            self.ink_pixels,
            self.ink_paint,
            self.dark_mode,
            detected_background_bgr,
        ) = _prepare_style_ink_maps(
            self.color_img,
            cfg,
        )
        if self.dark_mode:
            self.canvas_bgr = detected_background_bgr
        self.grid_blocks = sr._to_grid_blocks(self.thresh_map, cfg.grid_edge)
        self.active_all = sr._active_mask(self.thresh_map, cfg.grid_edge, cfg.ink_threshold)

        # 背景染成画布底色，让上色阶段背景与起笔一致（不碰墨迹）
        if cfg.match_bg and not self.dark_mode:
            self._match_original_background()

        self.output_owner = cv2.resize(self.source_owner, (self.out_w, self.out_h), interpolation=cv2.INTER_NEAREST)
        self.foreground_pixels = self.output_owner > 0
        self.ink_pixels &= self.foreground_pixels
        self.color_img[~self.foreground_pixels] = self.canvas_bgr
        self.phase_budget_metrics: list[dict] = []
        self.stroke_metrics: list[dict] = []
        self._last_recognition_effort_scales: list[float] | None = None
        self._last_hand_position: tuple[int, int] | None = None
        self._last_snapshot_hand_visible = False
        self.hand_motion_qa: dict = {}
        self.motion_plans: list[dict] = []
        self.ownership_metrics: dict = {
            "mode": self.pixel_ownership,
            "overlap_candidate_pixels": 0,
            "overlap_foreground_pixels": 0,
            "elements": [],
        }
        self.color_metrics: dict = {
            "mode": cfg.color_fill,
            "schedule": self.color_schedule if cfg.color_fill == "local-brush" else "legacy",
            "passes": 0,
            "pen_lifts": 0,
            "objects_colored": 0,
            "object_records": [],
            "color_sweeps": 0,
            "base_color_frames": 0,
            "texture_frames": 0,
            "max_identity_ready_ratio": 0.0,
            "max_finalize_residual_ratio": 0.0,
        }

        # 共享持久画布（float32 保留笔刷叠加的渐变精度）
        self.drawn = np.empty((self.out_h, self.out_w, 3), dtype=np.float32)
        self.drawn[...] = self.canvas_bgr.astype(np.float32)
        self.mask_grid_init_ms = (time.perf_counter() - t_mask_start) * 1000.0

        t_assets_start = time.perf_counter()
        self.animation = AnimationOverlay(
            annotation,
            animation_plan,
            scene_id,
            self.sx,
            self.sy,
            self.out_w,
            self.out_h,
            self.canvas_bgr,
        )

        self.animation.owner_masks = {
            str(element["id"]): self.output_owner == index
            for index, element in enumerate(annotation["elements"], 1)
        }

        # 笔尖覆盖；small-hand 是默认，no-hand 完全关闭，full-hand 保留兼容模式。
        self.tip: sr.TipOverlay | None = None
        if hand_mode != "no-hand":
            hand_path, ax, ay = _manifest_asset(manifest, "drawing", hand_png or DEFAULT_HAND)
            if hand_png and hand_png != DEFAULT_HAND:
                hand_path = hand_png
            hand_data = sr._load_hand(hand_path, cfg.target_hand_height)
            if hand_data is None:
                hand_data = sr._procedural_tip(cfg.target_hand_height)
                ax, ay = 0.5, 0.70
            self.tip = sr.TipOverlay(hand_data[0], hand_data[1], tip_anchor_x=ax, tip_anchor_y=ay)

        self.eraser: sr.TipOverlay | None = None
        if hand_mode != "no-hand" and eraser_png:
            eraser_path, ex, ey = _manifest_asset(manifest, "eraser", eraser_png)
            eraser_data = sr._load_hand(eraser_path, cfg.target_hand_height)
            if eraser_data is not None:
                self.eraser = sr.TipOverlay(eraser_data[0], eraser_data[1], tip_anchor_x=ex, tip_anchor_y=ey)
        self.manifest_hand_assets_ms = (time.perf_counter() - t_assets_start) * 1000.0

    # 采样原图四角，把接近背景色的像素替换为画布底色
    def _match_original_background(self) -> None:
        img = self.color_img
        h, w = img.shape[:2]
        margin = max(3, min(h, w) // 50)
        samples = [img[:margin, :margin], img[:margin, -margin:],
                   img[-margin:, :margin], img[-margin:, -margin:]]
        bg = np.median(np.concatenate([s.reshape(-1, 3) for s in samples]), axis=0)
        diff = np.abs(img.astype(np.int16) - bg.astype(np.int16)).sum(axis=2)
        img[diff < self.cfg.match_bg_threshold] = self.canvas_bgr

    def _cell_center(self, cell: tuple[int, int]) -> tuple[int, int]:
        r, c = cell
        e = self.cfg.grid_edge
        return (c * e + e // 2, r * e + e // 2)

    def _snapshot_with_tip(
        self,
        px: int,
        py: int,
        show_tip: bool = True,
        opacity: float = 1.0,
    ) -> np.ndarray:
        snap = self.drawn.astype(np.uint8)
        self._last_snapshot_hand_visible = False
        if show_tip and opacity > 0.0:
            if self._stamp_overlay(self.tip, snap, px, py, opacity):
                self._last_hand_position = (px, py)
                self._last_snapshot_hand_visible = True
        return snap

    def _write_tip_frame(
        self,
        writer: FrameCountWriter,
        px: float,
        py: float,
        *,
        opacity: float = 1.0,
        state: str = "draw",
        pen_down: bool = True,
    ) -> None:
        x, y = int(round(px)), int(round(py))
        frame = self._snapshot_with_tip(x, y, opacity=opacity)
        writer.write(frame, hand={
            "x": x,
            "y": y,
            "opacity": float(opacity),
            "visible": self._last_snapshot_hand_visible,
            "state": state,
            "pen_down": pen_down,
        })

    def _write_idle_hand_frames(self, writer: FrameCountWriter, frames: int) -> None:
        """Keep the last visible hand stable across an empty drawing phase."""
        if frames <= 0:
            return
        if self._last_hand_position is None:
            for _ in range(frames):
                writer.write(self.drawn.astype(np.uint8))
            return
        for _ in range(frames):
            self._write_tip_frame(
                writer,
                *self._last_hand_position,
                state="hold",
                pen_down=False,
            )

    def _reveal_motion_step(
        self,
        previous: MotionFrame | None,
        current: MotionFrame,
        samples: list[tuple[int, int]],
        pen_lifts: set[int],
        allowed: np.ndarray,
    ) -> None:
        current_point = (int(round(current.x)), int(round(current.y)))
        if previous is None:
            if current.pen_down:
                self._reveal_ink_segment(current_point, current_point, allowed)
            return
        previous_point = (int(round(previous.x)), int(round(previous.y)))
        if current.sample_index < previous.sample_index:
            return
        segment_index = max(1, previous.sample_index)
        cursor = previous_point
        while segment_index <= current.sample_index:
            endpoint = current_point if segment_index == current.sample_index else samples[segment_index]
            if segment_index not in pen_lifts:
                self._reveal_ink_segment(cursor, endpoint, allowed)
            cursor = endpoint
            segment_index += 1

    def _stamp_overlay(
        self,
        overlay: sr.TipOverlay | None,
        frame: np.ndarray,
        x: int,
        y: int,
        opacity: float = 1.0,
    ) -> bool:
        if overlay is None:
            return False
        # Presenter 是完整角色，不允许像普通小手一样被画布边缘截成半个头或半截身体。
        # 极端边缘位置宁可暂时隐藏整个覆盖层，真实笔迹和擦除掩码仍照常推进。
        if self.hand_mode == "presenter" and overlay.visible_ratio(frame.shape, x, y) < self.presenter_min_visible_ratio:
            return False
        overlay.stamp(frame, x, y, opacity=opacity)
        return True

    def _animated_snapshot(self, time_ms: float) -> np.ndarray:
        frame = self.drawn.astype(np.uint8)
        frame = self.animation.apply(frame, time_ms)
        # Semantic gestures own their own hand lifecycle. The hand is stamped
        # after camera transforms so the pen tip uses the same output-space
        # coordinates as the progressively revealed path. During focus-push
        # the hand is hidden by contract.
        if self.tip is not None and not self.animation.camera_active(time_ms):
            hand_position = self.animation.hand_position(time_ms)
            if hand_position is not None:
                self._stamp_overlay(self.tip, frame, hand_position[0], hand_position[1])
        return frame

    def _eraser_position(self, progress: float) -> tuple[int, int]:
        """Return a serpentine horizontal wipe position across the full board."""
        passes = 8
        scaled = max(0.0, min(0.999999, progress)) * passes
        pass_index = min(passes - 1, int(scaled))
        local = scaled - pass_index
        margin = max(32, round(min(self.out_w, self.out_h) * 0.04))
        left = -margin
        right = self.out_w + margin
        x = left + (right - left) * (local if pass_index % 2 == 0 else 1.0 - local)
        y = round((pass_index + 0.5) * self.out_h / passes)
        return round(x), max(0, min(self.out_h - 1, y))

    def _render_eraser_transition(self, writer, frames: int) -> None:
        if frames <= 0:
            return
        if self.eraser is None and self.hand_mode != "no-hand":
            raise RuntimeError("动画计划要求板擦转场，但没有可用的 eraser-hand.png")
        previous: tuple[int, int] | None = None
        thickness = max(20, round(self.out_h / 8.0) + 8)
        for index in range(frames):
            progress = 1.0 if frames == 1 else index / (frames - 1)
            current = self._eraser_position(progress)
            mask = np.zeros((self.out_h, self.out_w), dtype=np.uint8)
            if previous is not None:
                cv2.line(mask, previous, current, 255, thickness=thickness, lineType=cv2.LINE_AA)
            cv2.circle(mask, current, max(10, thickness // 2), 255, thickness=-1, lineType=cv2.LINE_AA)
            self.drawn[mask > 0] = self.canvas_bgr.astype(np.float32)
            if index == frames - 1:
                # The endpoint frame is the visual boundary of the erase window.
                # Do not leave partially uncovered pixels visible until the next
                # static frame; the contract requires a genuinely clean canvas.
                self.drawn[...] = self.canvas_bgr.astype(np.float32)
            frame = self.drawn.astype(np.uint8)
            self._stamp_overlay(self.eraser, frame, current[0], current[1])
            writer.write(frame)
            previous = current
        self.drawn[...] = self.canvas_bgr.astype(np.float32)

    # ── 区域候选掩码与互斥像素所有权 ──
    def _ownership_masks(self, elements: list[dict]) -> list[np.ndarray]:
        by_id = {str(element["id"]): index for index, element in enumerate(self.ann["elements"], 1)}
        self.ownership_metrics = self.source_ownership_metrics
        return [self.output_owner == by_id[str(element["id"])] for element in elements]

    def _region_grid_path(self, allowed: np.ndarray) -> list[tuple[int, int]]:
        """网格模式：把区域内含墨的格聚类并串成连续格路径。"""
        allowed_u8 = allowed.astype(np.uint8)
        allowed_cell = sr._to_grid_blocks(allowed_u8, self.cfg.grid_edge).any(axis=(2, 3))
        active = self.active_all & allowed_cell
        if not active.any():
            return []
        streams = sr.cluster_ink_streams(active)
        return sr.flatten_streams(streams)

    def _extract_region_skeleton_strokes(self, allowed: np.ndarray) -> list[list[tuple[int, int]]]:
        """骨架模式：区域内墨迹细化 + 8 邻接追踪 + 重采样平滑。"""
        cfg = self.cfg
        region_ink = self.ink_pixels & allowed
        if not region_ink.any():
            return []
        ys, xs = np.where(region_ink)
        min_y, max_y = int(ys.min()), int(ys.max())
        min_x, max_x = int(xs.min()), int(xs.max())
        crop = region_ink[min_y:max_y + 1, min_x:max_x + 1]
        skel_crop = sr._zhang_suen_skeleton(crop, max_iterations=160)
        raw = sr.trace_8connected(skel_crop, min_points=cfg.skeleton_min_points)
        if not raw:
            return []
        spacing = cfg.skeleton_resample_spacing
        out: list[list[tuple[int, int]]] = []
        for stroke in raw:
            pts = [(float(x + min_x), float(y + min_y)) for x, y in stroke]
            pts = sr._resample_stroke_points(pts, spacing)
            pts = sr._chaikin_smooth(pts, iterations=1)
            pts = sr._resample_stroke_points(pts, spacing)
            if len(pts) >= 2 and sr._stroke_cumulative_length(pts)[-1] > 2.0:
                out.append([(int(round(x)), int(round(y))) for x, y in pts])
        return out

    def _semantic_plan(self, strokes: list[list[tuple[int, int]]], allowed: np.ndarray) -> list[dict]:
        if self.stroke_planner == "semantic-v2":
            return sr._semantic_stroke_plan(strokes, self.ink_pixels, allowed)
        ordered = sr._order_skeleton_strokes(strokes)
        return [
            {"points": stroke, "component": 0, "componentRank": 0, "role": "legacy", "length": _stroke_length(stroke)}
            for stroke in ordered
        ]

    def _region_skeleton_strokes(self, allowed: np.ndarray) -> list[list[tuple[int, int]]]:
        strokes = self._extract_region_skeleton_strokes(allowed)
        return [record["points"] for record in self._semantic_plan(strokes, allowed)]

    def _split_stroke_layers(
        self,
        strokes: list[list[tuple[int, int]]],
        allowed: np.ndarray,
    ) -> tuple[list[list[tuple[int, int]]], list[list[tuple[int, int]]], list[list[tuple[int, int]]]]:
        """Split long structural strokes, secondary details, and short texture marks."""
        if not strokes:
            return [], [], []
        ys, xs = np.where(allowed)
        diagonal = math.hypot(float(xs.max() - xs.min() + 1), float(ys.max() - ys.min() + 1)) if xs.size else 1.0
        texture_limit = max(10.0, diagonal * 0.025)
        measured = [(stroke, _stroke_length(stroke)) for stroke in strokes]
        texture = [stroke for stroke, length in measured if length < texture_limit]
        structural = [(stroke, length) for stroke, length in measured if length >= texture_limit]
        if not structural:
            longest = max(measured, key=lambda item: item[1])[0]
            return [longest], [], [stroke for stroke, _ in measured if stroke is not longest]
        structural.sort(key=lambda item: item[1], reverse=True)
        target = sum(length for _, length in structural) * 0.62
        outline: list[list[tuple[int, int]]] = []
        detail: list[list[tuple[int, int]]] = []
        accumulated = 0.0
        for index, (stroke, length) in enumerate(structural):
            if accumulated < target or index == 0:
                outline.append(stroke)
                accumulated += length
            else:
                detail.append(stroke)
        return outline, detail, texture

    @staticmethod
    def _flatten_layer(
        strokes: list[list[tuple[int, int]]],
    ) -> tuple[list[tuple[int, int]], set[int]]:
        samples: list[tuple[int, int]] = []
        pen_lifts: set[int] = set()
        for stroke in strokes:
            if not stroke:
                continue
            if samples:
                pen_lifts.add(len(samples))
            samples.extend(stroke)
        return samples, pen_lifts

    def _layered_paths(
        self,
        allowed: np.ndarray,
        visible_frames: int | None = None,
    ) -> tuple[tuple[list[tuple[int, int]], set[int]], tuple[list[tuple[int, int]], set[int]], list[tuple[int, int]]]:
        strokes = self._extract_region_skeleton_strokes(allowed)
        if strokes:
            if self.stroke_planner == "semantic-v2":
                plan = self._semantic_plan(strokes, allowed)
                # Keep each component's outline and identity-bearing details together.
                # Grouping all outlines before all details made several objects unreadable
                # until the final colour pass.
                all_recognition_records = [record for record in plan if record["role"] in {"outline", "detail"}]
                recognition_records = all_recognition_records
                motion_stats: dict[str, float | int] = {
                    "visible_hand_strokes": len(all_recognition_records),
                    "traced_hand_strokes": len(plan),
                    "deferred_strokes": 0,
                    "visible_path_length_px": round(sum(float(record.get("length", 0.0)) for record in all_recognition_records), 2),
                    "recognition_duration_ms": 0,
                    "estimated_hand_strokes_per_sec": 0.0,
                    "estimated_hand_path_short_edges_per_sec": 0.0,
                }
                del visible_frames
                recognition = [record["points"] for record in recognition_records]
                outline = [record["points"] for record in plan if record["role"] == "outline"]
                detail = [record["points"] for record in plan if record["role"] == "detail"]
                texture = [record["points"] for record in plan if record["role"] == "texture"]
                directions = [
                    math.atan2(stroke[-1][1] - stroke[0][1], stroke[-1][0] - stroke[0][0])
                    for stroke in (outline + detail + texture)
                    if len(stroke) >= 2
                ]
                direction_bins = {int(((angle + math.pi) / (math.pi / 4.0))) % 8 for angle in directions}
                self.stroke_metrics.append({
                    "planner": self.stroke_planner,
                    "components": len({record["component"] for record in plan}),
                    "outline_strokes": len(outline),
                    "detail_strokes": len(detail),
                    "texture_strokes": len(texture),
                    "identity_strokes": sum(record.get("semanticStage") == "identity" for record in plan),
                    "support_strokes": sum(record.get("semanticStage") == "support" for record in plan),
                    "stage_order": [record.get("semanticStage", record["role"]) for record in plan],
                    "direction_bins": len(direction_bins),
                    "total_strokes": len(outline) + len(detail) + len(texture),
                    **motion_stats,
                })
            else:
                outline, detail, texture = self._split_stroke_layers(self._region_skeleton_strokes(allowed), allowed)
            if self.stroke_planner == "semantic-v2":
                outline_samples, outline_lifts = self._flatten_layer(recognition)
                # Stage identity affects which whole strokes are retained, not
                # instantaneous hand speed. Discrete multipliers previously
                # produced visible acceleration at stage boundaries.
                self._last_recognition_effort_scales = [1.0] * len(outline_samples)
                detail_samples, detail_lifts = [], set()
            else:
                self._last_recognition_effort_scales = None
                outline_samples, outline_lifts = self._flatten_layer(outline)
                detail_samples, detail_lifts = self._flatten_layer(detail)
            texture_samples, texture_lifts = self._flatten_layer(texture)
            color_centers = outline_samples + detail_samples + texture_samples
            return (
                (outline_samples, outline_lifts),
                (detail_samples, detail_lifts),
                (texture_samples, texture_lifts),
                color_centers,
            )
        path = self._region_grid_path(allowed)
        samples, lifts, _ = self._grid_plan(path) if path else ([], set(), [])
        return (samples, lifts), ([], set()), ([], set()), [self._cell_center(cell) for cell in path]

    # ── 落墨（限制在 allowed 内）──
    def _reveal_ink_segment(self, a: tuple[int, int], b: tuple[int, int], allowed: np.ndarray) -> None:
        thick = max(1, self.cfg.ink_reveal_radius * 2 + 1)
        margin = thick + 2
        x0 = max(0, min(a[0], b[0]) - margin)
        y0 = max(0, min(a[1], b[1]) - margin)
        x1 = min(self.out_w, max(a[0], b[0]) + margin + 1)
        y1 = min(self.out_h, max(a[1], b[1]) + margin + 1)
        if x1 <= x0 or y1 <= y0:
            return
        local_a = (a[0] - x0, a[1] - y0)
        local_b = (b[0] - x0, b[1] - y0)
        seg_local = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
        cv2.line(seg_local, local_a, local_b, 255, thickness=thick, lineType=cv2.LINE_AA)
        rev_local = (seg_local > 0) & self.ink_pixels[y0:y1, x0:x1] & allowed[y0:y1, x0:x1]
        target_crop = self.drawn[y0:y1, x0:x1]
        paint_crop = self.ink_paint[y0:y1, x0:x1]
        target_crop[rev_local] = paint_crop[rev_local]

    def _ink_stamp_cell(self, cell: tuple[int, int], allowed: np.ndarray) -> None:
        r, c = cell
        e = self.cfg.grid_edge
        block = self.grid_blocks[r, c]
        allow_block = allowed[r * e:r * e + e, c * e:c * e + e]
        ink_region = (block < self.cfg.ink_threshold) & allow_block
        paint = np.repeat(block[:, :, None], 3, axis=2)
        target = self.drawn[r * e:r * e + e, c * e:c * e + e]
        target[ink_region] = paint[ink_region]

    def _color_stamp(self, px: int, py: int, disk: np.ndarray, allowed: np.ndarray) -> None:
        radius = self.cfg.brush_radius
        h, w = self.out_h, self.out_w
        y0, y1 = max(0, py - radius), min(h, py + radius + 1)
        x0, x1 = max(0, px - radius), min(w, px + radius + 1)
        if y1 <= y0 or x1 <= x0:
            return
        by0, by1 = y0 - (py - radius), disk.shape[0] - ((py + radius + 1) - y1)
        bx0, bx1 = x0 - (px - radius), disk.shape[1] - ((px + radius + 1) - x1)
        m = (disk[by0:by1, bx0:bx1] * allowed[y0:y1, x0:x1])[:, :, None]
        inv = 1.0 - m
        target = self.drawn[y0:y1, x0:x1]
        source = self.color_img[y0:y1, x0:x1].astype(np.float32)
        target[...] = target * inv + source * m

    # ── 起笔段（骨架模式）：沿笔迹逐段揭原图墨迹，无块填充 ──
    def _lay_ink(self, writer, frames: int, samples: list[tuple[int, int]],
                 pen_lifts: set[int], allowed: np.ndarray,
                 effort_scales: list[float] | None = None) -> None:
        if frames <= 0:
            return
        n = len(samples)
        if n == 0:
            self._write_idle_hand_frames(writer, frames)
            return
        del effort_scales
        schedule = _fast_trace_schedule(n, pen_lifts, frames)
        self.motion_plans.append({
            "phase": "ink",
            "strategy": "fast-semantic-trace-v1",
            "completed": True,
            "frames": frames,
            "samples": n,
            "strokes": len(pen_lifts) + 1,
        })
        fade_in = _fade_opacities(frames, int(self.cfg.fps), HAND_FADE_IN_MS, fade_in=True)
        previous_index: int | None = None
        for frame_index, current_index in enumerate(schedule):
            current_index = min(n - 1, max(0, current_index))
            if previous_index is None:
                point = samples[current_index]
                self._reveal_ink_segment(point, point, allowed)
            else:
                cursor = samples[previous_index]
                for sample_index in range(previous_index + 1, current_index + 1):
                    endpoint = samples[sample_index]
                    if sample_index not in pen_lifts:
                        self._reveal_ink_segment(cursor, endpoint, allowed)
                    cursor = endpoint
            current = samples[current_index]
            opacity = fade_in[frame_index]
            if opacity > 0.0:
                self._write_tip_frame(
                    writer,
                    current[0],
                    current[1],
                    opacity=opacity,
                    state="fast-draw",
                    pen_down=current_index not in pen_lifts,
                )
            else:
                writer.write(self.drawn.astype(np.uint8))
            previous_index = current_index

    # ── 添彩段：brush 或 contour-wipe，限制在 allowed 内 ──
    def _wash_brush(self, writer, frames: int, centers: list[tuple[int, int]], allowed: np.ndarray) -> None:
        if frames <= 0:
            return
        n = len(centers)
        if n == 0:
            self._write_idle_hand_frames(writer, frames)
            return
        disk = sr._feathered_disk(self.cfg.brush_radius)
        motion_frames, motion = _plan_continuous_motion(
            centers,
            frames,
            set(),
            fps=int(self.cfg.fps),
            short_edge=min(self.out_w, self.out_h),
            draw_max_step_ratio=HAND_COLOR_MAX_STEP_SHORT_EDGE_RATIO,
        )
        self.motion_plans.append({"phase": "legacy-color", **motion})
        for current in motion_frames:
            if current.pen_down:
                self._color_stamp(int(round(current.x)), int(round(current.y)), disk, allowed)
            self._write_tip_frame(
                writer,
                current.x,
                current.y,
                state="color" if current.state == "draw" else current.state,
                pen_down=current.pen_down,
            )

    def _local_color_plan(self, allowed: np.ndarray, *, include_ink: bool = True) -> tuple[list[tuple[int, int]], set[int]]:
        """Create short object-local colouring passes instead of one board-wide scan."""
        target_mask = self.foreground_pixels & allowed
        if not include_ink:
            target_mask &= ~self.ink_pixels
        target = target_mask.astype(np.uint8)
        if not target.any():
            return [], set()
        radius = max(6, int(self.cfg.brush_radius))
        join = max(5, radius // 2 * 2 + 1)
        grouped = cv2.dilate(target, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (join, join)), iterations=1)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(grouped, connectivity=8)
        components = [label for label in range(1, count) if stats[label, cv2.CC_STAT_AREA] > 0]
        if not components:
            return [], set()
        ys, xs = np.where(allowed)
        board_center = (float(xs.mean()), float(ys.mean()))
        components.sort(key=lambda label: (
            -stats[label, cv2.CC_STAT_AREA] * (
                1.25 - min(0.75, math.hypot(centroids[label][0] - board_center[0], centroids[label][1] - board_center[1]) / max(1.0, math.hypot(self.out_w, self.out_h)))
            ),
            label,
        ))
        step = radius
        ordered: list[tuple[int, int]] = []
        lifts: set[int] = set()
        for label in components:
            component_target = target.astype(bool) & (labels == label)
            cy0, cx0, ch, cw = (
                int(stats[label, cv2.CC_STAT_TOP]),
                int(stats[label, cv2.CC_STAT_LEFT]),
                int(stats[label, cv2.CC_STAT_HEIGHT]),
                int(stats[label, cv2.CC_STAT_WIDTH]),
            )
            candidates: list[tuple[int, int]] = []
            for y0 in range(cy0, cy0 + ch, step):
                for x0 in range(cx0, cx0 + cw, step):
                    y1, x1 = min(self.out_h, y0 + step), min(self.out_w, x0 + step)
                    if component_target[y0:y1, x0:x1].any():
                        candidates.append((min(self.out_w - 1, x0 + step // 2), min(self.out_h - 1, y0 + step // 2)))
            if not candidates:
                continue
            if ordered:
                lifts.add(len(ordered))
            current = min(candidates, key=lambda point: math.hypot(point[0] - centroids[label][0], point[1] - centroids[label][1]))
            while candidates:
                candidates.remove(current)
                ordered.append(current)
                if candidates:
                    current = min(candidates, key=lambda point: (math.hypot(point[0] - current[0], point[1] - current[1]), point[1], point[0]))
        return ordered, lifts

    def _color_reveal_stamp(
        self,
        px: int,
        py: int,
        allowed: np.ndarray,
        *,
        include_ink: bool = True,
        radius: int | None = None,
    ) -> None:
        radius = max(6, int(radius if radius is not None else self.cfg.brush_radius))
        y0, y1 = max(0, py - radius), min(self.out_h, py + radius + 1)
        x0, x1 = max(0, px - radius), min(self.out_w, px + radius + 1)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        disk = (xx - px) ** 2 + (yy - py) ** 2 <= radius ** 2
        reveal = disk & allowed[y0:y1, x0:x1] & self.foreground_pixels[y0:y1, x0:x1]
        if not include_ink:
            reveal &= ~self.ink_pixels[y0:y1, x0:x1]
        target = self.drawn[y0:y1, x0:x1]
        source = self.color_img[y0:y1, x0:x1].astype(np.float32)
        target[reveal] = source[reveal]

    def _wash_flat_color_sweeps(
        self,
        writer: FrameCountWriter,
        frames: int,
        allowed: np.ndarray,
    ) -> int:
        """Fill flat colours in three or four visible serpentine hand sweeps."""
        if frames <= 0:
            return 0
        # Structural ink is already visible; do not expose deferred texture ink.
        target = allowed & self.foreground_pixels & ~self.ink_pixels
        if not target.any():
            for _ in range(frames):
                writer.write(self.drawn.astype(np.uint8))
            return 0

        ys, xs = np.where(target)
        top, bottom = int(ys.min()), int(ys.max())
        left, right = int(xs.min()), int(xs.max())
        if frames < MIN_COLOR_FRAMES:
            raise ValueError("基础色帧预算不足，不能减少批准的填色次数")
        sweeps = 4 if frames >= 12 else MIN_COLOR_SWEEPS
        first_forward = True
        if self._last_hand_position is not None:
            first_forward = abs(self._last_hand_position[0] - left) <= abs(self._last_hand_position[0] - right)

        initial_travel = 2 if self._last_hand_position is not None and frames >= sweeps * 3 + 2 else 0
        lane_travel = max(0, sweeps - 1)
        draw_frames = max(sweeps, frames - initial_travel - lane_travel)
        allocations = [1] * sweeps
        spare = draw_frames - sweeps
        for index in range(spare):
            allocations[index % sweeps] += 1

        self.motion_plans.append({
            "phase": "base-color",
            "strategy": "serpentine-flat-fill-v1",
            "completed": True,
            "frames": frames,
            "sweeps": sweeps,
            "travel_frames": initial_travel + lane_travel,
        })
        self.color_metrics["passes"] += sweeps
        self.color_metrics["color_sweeps"] += sweeps

        boundaries = np.linspace(top, bottom + 1, sweeps + 1, dtype=int)
        first_y = max(top, min(bottom, (int(boundaries[0]) + int(boundaries[1]) - 1) // 2))
        first_x = left if first_forward else right
        written = 0
        if initial_travel and self._last_hand_position is not None:
            start_x, start_y = self._last_hand_position
            for index in range(initial_travel):
                progress = (index + 1) / initial_travel
                self._write_tip_frame(
                    writer,
                    start_x + (first_x - start_x) * progress,
                    start_y + (first_y - start_y) * progress,
                    state="fast-color",
                    pen_down=False,
                )
                written += 1

        source = self.color_img.astype(np.float32)
        previous_x, previous_y = first_x, first_y
        for sweep_index in range(sweeps):
            y0, y1 = int(boundaries[sweep_index]), int(boundaries[sweep_index + 1])
            lane_y = max(top, min(bottom, (y0 + y1 - 1) // 2))
            forward = first_forward if sweep_index % 2 == 0 else not first_forward
            start_x, end_x = (left, right) if forward else (right, left)
            if sweep_index > 0:
                self._write_tip_frame(
                    writer,
                    previous_x,
                    lane_y,
                    state="fast-color",
                    pen_down=False,
                )
                previous_y = lane_y
                written += 1

            lane_target = target[y0:y1, left:right + 1]
            lane_drawn = self.drawn[y0:y1, left:right + 1]
            lane_source = source[y0:y1, left:right + 1]
            lane_frames = allocations[sweep_index]
            for frame_index in range(lane_frames):
                progress = 1.0 if lane_frames == 1 else frame_index / (lane_frames - 1)
                current_x = int(round(start_x + (end_x - start_x) * progress))
                local_x = max(0, min(right - left, current_x - left))
                reveal = np.zeros_like(lane_target)
                if forward:
                    reveal[:, :local_x + 1] = lane_target[:, :local_x + 1]
                else:
                    reveal[:, local_x:] = lane_target[:, local_x:]
                lane_drawn[reveal] = lane_source[reveal]
                self._write_tip_frame(
                    writer,
                    current_x,
                    lane_y,
                    state="fast-color",
                    pen_down=True,
                )
                previous_x, previous_y = current_x, lane_y
                written += 1

        while written < frames:
            self._write_tip_frame(
                writer,
                previous_x,
                previous_y,
                state="fast-color",
                pen_down=False,
            )
            written += 1
        return sweeps

    def _wash_local_brush(
        self,
        writer,
        frames: int,
        allowed: np.ndarray,
        *,
        include_ink: bool = True,
        phase: str = "base",
    ) -> None:
        if frames <= 0:
            return
        centers, pen_lifts = self._local_color_plan(allowed, include_ink=include_ink)
        self.color_metrics["passes"] += len(centers)
        self.color_metrics["pen_lifts"] += len(pen_lifts)
        if phase == "base":
            self.color_metrics["objects_colored"] += 1
            self.color_metrics["base_color_frames"] += frames
        else:
            self.color_metrics["texture_frames"] += frames
        if not centers:
            self._write_idle_hand_frames(writer, frames)
            return
        if phase == "polish":
            idx_for_frame = _frame_progress_indices(len(centers), frames)
            fade_out = _fade_opacities(
                frames,
                int(self.cfg.fps),
                HAND_FADE_OUT_MS,
                fade_in=False,
            )
            last: int | None = None
            for frame_index, center_index in enumerate(idx_for_frame):
                if last is None:
                    self._color_reveal_stamp(*centers[center_index], allowed, include_ink=include_ink)
                else:
                    for index in range(last + 1, center_index + 1):
                        self._color_reveal_stamp(*centers[index], allowed, include_ink=include_ink)
                if frame_index == len(idx_for_frame) - 1:
                    residual = allowed & self.foreground_pixels & np.any(
                        np.abs(self.drawn - self.color_img.astype(np.float32)) > 0.5,
                        axis=2,
                    )
                    if not include_ink:
                        residual &= ~self.ink_pixels
                    self.drawn[residual] = self.color_img.astype(np.float32)[residual]
                opacity = fade_out[frame_index]
                if self._last_hand_position is not None and opacity > 0.0:
                    self._write_tip_frame(
                        writer,
                        *self._last_hand_position,
                        opacity=opacity,
                        state="hold",
                        pen_down=False,
                    )
                else:
                    writer.write(self.drawn.astype(np.uint8))
                last = center_index
            return

        motion_centers = list(centers)
        motion_lifts = set(pen_lifts)
        center_offset = 0
        if self._last_hand_position is not None and motion_centers:
            if math.hypot(
                motion_centers[0][0] - self._last_hand_position[0],
                motion_centers[0][1] - self._last_hand_position[1],
            ) > 1.0:
                motion_centers.insert(0, self._last_hand_position)
                motion_lifts = {index + 1 for index in motion_lifts}
                motion_lifts.add(1)
                center_offset = 1

        motion_frames, motion = _plan_continuous_motion(
            motion_centers,
            frames,
            motion_lifts,
            fps=int(self.cfg.fps),
            short_edge=min(self.out_w, self.out_h),
            draw_max_step_ratio=HAND_COLOR_MAX_STEP_SHORT_EDGE_RATIO,
        )
        self.motion_plans.append({"phase": "base-color", **motion})
        hold_fade = _fade_opacities(
            int(motion.get("hold_frames", 0)),
            int(self.cfg.fps),
            HAND_FADE_OUT_MS,
            fade_in=False,
        ) if not motion.get("completed") else []
        revealed_center = -1
        for frame_index, current in enumerate(motion_frames):
            if current.pen_down:
                self._color_reveal_stamp(
                    int(round(current.x)),
                    int(round(current.y)),
                    allowed,
                    include_ink=include_ink,
                )
            if current.state != "hold":
                completed_center = current.sample_index - center_offset - 1
                endpoint_index = current.sample_index - center_offset
                if 0 <= endpoint_index < len(centers):
                    endpoint = centers[endpoint_index]
                    if math.hypot(current.x - endpoint[0], current.y - endpoint[1]) <= 1.0:
                        completed_center = endpoint_index
                while revealed_center < min(len(centers) - 1, completed_center):
                    revealed_center += 1
                    self._color_reveal_stamp(*centers[revealed_center], allowed, include_ink=include_ink)
            else:
                hold_count = max(1, int(motion.get("hold_frames", 0)))
                hold_index = frame_index - int(motion.get("motion_frames", 0)) + 1
                remaining = len(centers) - revealed_center - 1
                target = revealed_center + int(math.ceil(remaining * hold_index / hold_count))
                while revealed_center < min(len(centers) - 1, target):
                    revealed_center += 1
                    self._color_reveal_stamp(*centers[revealed_center], allowed, include_ink=include_ink)

            if frame_index == len(motion_frames) - 1:
                residual = allowed & self.foreground_pixels & np.any(
                    np.abs(self.drawn - self.color_img.astype(np.float32)) > 0.5,
                    axis=2,
                )
                if not include_ink:
                    residual &= ~self.ink_pixels
                self.drawn[residual] = self.color_img.astype(np.float32)[residual]

            opacity = 1.0
            if current.state == "hold" and hold_fade:
                hold_index = frame_index - int(motion["motion_frames"])
                opacity = hold_fade[min(max(0, hold_index), len(hold_fade) - 1)]
            if opacity > 0.0:
                self._write_tip_frame(
                    writer,
                    current.x,
                    current.y,
                    opacity=opacity,
                    state="color" if current.state == "draw" else current.state,
                    pen_down=current.pen_down,
                )
            else:
                writer.write(self.drawn.astype(np.uint8))

    def _wash_contour(self, writer, frames: int, allowed: np.ndarray) -> None:
        if frames <= 0:
            return
        cfg = self.cfg
        ys_all, xs_all = np.where(allowed)
        if ys_all.size == 0:
            return
        top, bottom = int(ys_all.min()), int(ys_all.max())
        left, right = int(xs_all.min()), int(xs_all.max())
        region_h = bottom - top + 1
        region_w = right - left + 1

        # 区域内的阻力场（墨线膨胀 + 模糊 + 逐行向下衰减）
        ink_u8 = ((self.ink_pixels & allowed)[top:bottom + 1, left:right + 1].astype(np.uint8)) * 255
        spread = int(np.clip(min(region_w, region_h) // 32, 3, 17))
        if spread % 2 == 0:
            spread = max(3, spread - 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (spread, spread))
        dilated = cv2.dilate(ink_u8, kernel, iterations=1)
        blur_r = max(1, int(round(min(region_w, region_h) / 220.0)))
        if blur_r % 2 == 0:
            blur_r += 1
        resistance = cv2.GaussianBlur(dilated, (blur_r, blur_r), 0).astype(np.float32)
        peak = float(resistance.max())
        resistance = resistance / peak if peak > 1e-6 else np.zeros_like(resistance)
        decay = cfg.wipe_decay
        for row in range(1, region_h):
            resistance[row] = np.maximum(resistance[row], resistance[row - 1] * decay)

        wave = sr._build_wipe_wave(region_w)
        delay_px = int(np.clip(region_h * cfg.wipe_delay_ratio, 12, 52))
        ys = np.arange(region_h, dtype=np.float32)[:, None]
        sweep = region_h + 2 * delay_px
        blocks = max(1, cfg.wipe_blocks)

        allowed_crop = allowed[top:bottom + 1, left:right + 1]
        color_crop = self.color_img[top:bottom + 1, left:right + 1].astype(np.float32)
        drawn_crop = self.drawn[top:bottom + 1, left:right + 1]

        for fi in range(frames):
            progress = 1.0 if frames == 1 else fi / (frames - 1)
            lead = sr._ease_in_out_sine(progress) * sweep - delay_px
            threshold = lead + wave[None, :] - resistance * delay_px
            reveal = (ys <= threshold) & allowed_crop
            drawn_crop[reveal] = color_crop[reveal]

            lane = sr._ease_in_out_sine((fi / blocks * 2.0) % 1.0)
            forward = (int(fi // blocks) % 2 == 0)
            cx = int(lane * region_w) if forward else int((1.0 - lane) * region_w)
            cx = max(0, min(region_w - 1, cx))
            col = np.where(reveal[:, cx])[0]
            cy = int(col[-1]) if col.size > 0 else 0
            self._write_tip_frame(
                writer,
                left + cx,
                top + cy,
                state="color",
                pen_down=True,
            )

        # 收尾：确保区域内允许像素全部揭示
        drawn_crop[allowed_crop] = color_crop[allowed_crop]

    def _render_layered_lines(
        self,
        writer,
        frames: int,
        allowed: np.ndarray,
    ) -> list[tuple[int, int]]:
        """Draw the semantic object's structural outline, then its internal detail."""
        (outline, outline_lifts), (detail, detail_lifts), (texture, texture_lifts), color_centers = self._layered_paths(allowed)
        if not outline and not detail and not texture:
            self._lay_ink(writer, frames, [], set(), allowed)
            return color_centers
        outline_effort = max(1.0, float(len(outline)))
        detail_effort = max(0.0, float(len(detail)) * 1.30)
        texture_effort = max(0.0, float(len(texture)) * 0.72)
        if frames <= 1:
            outline_frames, detail_frames, texture_frames = frames, 0, 0
        else:
            total_effort = outline_effort + detail_effort + texture_effort
            detail_frames = round(frames * detail_effort / total_effort) if detail else 0
            texture_frames = round(frames * texture_effort / total_effort) if texture else 0
            detail_frames = min(detail_frames, max(0, frames - 1))
            texture_frames = min(texture_frames, max(0, frames - 1 - detail_frames))
            outline_frames = frames - detail_frames - texture_frames
        self._lay_ink(
            writer,
            outline_frames,
            outline,
            outline_lifts,
            allowed,
            self._last_recognition_effort_scales if self.stroke_planner == "semantic-v2" else None,
        )
        self._lay_ink(writer, detail_frames, detail, detail_lifts, allowed)
        self._lay_ink(writer, texture_frames, texture, texture_lifts, allowed)
        return color_centers

    def _render_progressive_object(
        self,
        writer,
        frames: int,
        allowed: np.ndarray,
        color_ratio: float,
        original_frames: int | None = None,
        budget: dict | None = None,
        element_id: str = "",
    ) -> None:
        """Render recognition and support, base colour, then optional polish."""
        phases = _progressive_phase_frames(
            frames,
            color_ratio,
            True,
            original_frames,
            fps=int(self.cfg.fps),
        )
        if budget is not None and (budget.get("phases") != phases or budget.get("totalFrames") != frames):
            raise ValueError("渲染阶段帧数与已编译计划不一致")
        phase_start = writer.frames_written
        (recognition, recognition_lifts), (_unused, _unused_lifts), (
            texture, texture_lifts
        ), _color_centers = self._layered_paths(allowed)
        self._lay_ink(
            writer,
            phases["recognition"],
            recognition,
            recognition_lifts,
            allowed,
            self._last_recognition_effort_scales,
        )
        actual_phases = {"recognition": writer.frames_written - phase_start}
        self.color_metrics["base_color_frames"] += phases["base_color"]
        self.color_metrics["texture_frames"] += phases["texture"]

        identity_ready = phases["recognition"] + phases["base_color"]
        self.color_metrics["max_identity_ready_ratio"] = max(
            float(self.color_metrics["max_identity_ready_ratio"]),
            identity_ready / max(1, original_frames if original_frames is not None else frames),
        )

        # Keep the clean flat-fill look, but let the visible hand wash it in
        # with broad serpentine passes after structural recognition, before polish.
        before_color = writer.frames_written
        color_pixels = int(np.count_nonzero(allowed & self.foreground_pixels & ~self.ink_pixels))
        sweeps = self._wash_flat_color_sweeps(writer, phases["base_color"], allowed)
        self.color_metrics["objects_colored"] += int(color_pixels > 0)
        self.color_metrics["object_records"].append({
            "elementId": element_id, "colorPixels": color_pixels,
            "sweeps": sweeps, "baseColorFrames": writer.frames_written - before_color,
        })
        actual_phases["base_color"] = writer.frames_written - before_color
        before_texture = writer.frames_written
        self._lay_ink(
            writer,
            phases["texture"],
            texture,
            texture_lifts,
            allowed,
        )
        actual_phases["texture"] = writer.frames_written - before_texture
        before_finalize = writer.frames_written
        fade_out = _fade_opacities(
            phases["finalize"],
            int(self.cfg.fps),
            HAND_FADE_OUT_MS,
            fade_in=False,
        ) if self._last_hand_position is not None else [0.0] * phases["finalize"]
        for opacity in fade_out:
            if opacity > 0.0 and self._last_hand_position is not None:
                self._write_tip_frame(
                    writer,
                    *self._last_hand_position,
                    opacity=opacity,
                    state="hold",
                    pen_down=False,
                )
            else:
                writer.write(self.drawn.astype(np.uint8))

        actual = writer.frames_written - phase_start
        actual_phases["finalize"] = writer.frames_written - before_finalize
        self.phase_budget_metrics.append({"elementId": element_id, **(budget or {}),
                                          "actualFrames": actual, "actualPhases": actual_phases})
        if actual != frames or actual_phases != phases:
            raise RuntimeError("对象实际输出帧数与批准的阶段预算不一致")

    # ── 网格路径的采样计划（插值 + 抬笔 + 块填充索引）──
    def _grid_plan(self, path: list[tuple[int, int]]):
        samples: list[tuple[int, int]] = []
        pen_lifts: set[int] = set()
        sample_cell: list[int] = []
        for idx, cell in enumerate(path):
            cx, cy = self._cell_center(cell)
            if idx == 0:
                samples.append((cx, cy))
                sample_cell.append(idx)
                continue
            prev_cell = path[idx - 1]
            prev = self._cell_center(prev_cell)
            if math.hypot(cell[0] - prev_cell[0], cell[1] - prev_cell[1]) > math.sqrt(2):
                pen_lifts.add(len(samples))
                samples.append((cx, cy))
                sample_cell.append(idx)
                continue
            steps = max(1, int(math.hypot(cx - prev[0], cy - prev[1]) / self.cfg.sample_step))
            for s in range(1, steps + 1):
                samples.append((int(prev[0] + (cx - prev[0]) * s / steps),
                                int(prev[1] + (cy - prev[1]) * s / steps)))
                sample_cell.append(idx)
        return samples, pen_lifts, sample_cell

    # ── 主渲染 ──
    def render_to(
        self,
        raw_path: Path,
        total_ms: int,
        lead_frames: int = 0,
        target_frames: int | None = None,
        frame_overlay: tuple[np.ndarray, int, int] | None = None,
    ) -> Path:
        cfg = self.cfg
        elements = sorted(self.ann["elements"], key=lambda e: e["reveal"]["startMs"])
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = FrameCountWriter(
            cv2.VideoWriter(str(raw_path), fourcc, cfg.fps, (self.out_w, self.out_h)),
            target_frames,
            fps=int(cfg.fps),
            short_edge=min(self.out_w, self.out_h),
            frame_overlay=frame_overlay,
        )
        if not writer.isOpened():
            raise RuntimeError("无法打开视频写入器")

        cur_ms = 0.0
        ms_per_frame = 1000.0 / cfg.fps
        lead_ms = max(0, lead_frames) * ms_per_frame

        def fill_static(until_ms: float) -> None:
            nonlocal cur_ms
            if until_ms < cur_ms - ms_per_frame:
                raise RuntimeError(
                    f"渲染时间线倒退：当前 {round(cur_ms)}ms，目标 {round(until_ms)}ms；"
                    "动画或板擦不能覆盖下一个真实时间点。"
                )
            n = int(round((until_ms - cur_ms) / ms_per_frame))
            if n <= 0:
                return
            for _ in range(n):
                writer.write(self._animated_snapshot(max(0.0, cur_ms - lead_ms)))
                cur_ms += ms_per_frame

        try:
            fill_static(lead_ms)
            ownership_masks = self._ownership_masks(elements)
            scene_color_mask = np.zeros((self.out_h, self.out_w), dtype=bool)
            scene_color_centers: list[tuple[int, int]] = []
            total_reveal_ms = sum(max(0, int(element["reveal"]["durationMs"])) for element in elements)
            progressive_color_ratio = min(
                0.34,
                max(
                    0.18,
                    self.color_reserve_ratio * 0.82,
                    self.minimum_color_ms / max(1, total_reveal_ms),
                ),
            )
            for idx, element in enumerate(elements):
                reveal = element["reveal"]
                start_ms = lead_ms + reveal["startMs"]
                dur_ms = reveal["durationMs"]
                fill_static(start_ms)

                allowed = ownership_masks[idx]
                duration_frames = frame_span(int(reveal["startMs"]), int(dur_ms), int(cfg.fps))
                is_last = idx == len(elements) - 1

                if self.draw_mode == "layered":
                    scene_color_mask |= allowed
                    if (
                        cfg.color_fill == "local-brush"
                        and self.stroke_planner == "semantic-v2"
                        and self.color_schedule == "object-progressive-v1"
                    ):
                        timing_reserve = reveal.get("animationTimingReserve", {})
                        original_duration_ms = int(
                            timing_reserve.get("originalDurationMs", dur_ms)
                        ) if isinstance(timing_reserve, dict) else int(dur_ms)
                        budget = compile_budget(int(reveal["startMs"]), int(dur_ms), original_duration_ms, int(cfg.fps))
                        if reveal.get("frameBudget") is not None and reveal["frameBudget"] != budget:
                            raise ValueError("工作流与渲染器的阶段预算不一致")
                        self._render_progressive_object(
                            writer, duration_frames, allowed, progressive_color_ratio,
                            budget["originalFrames"], budget, str(element["id"]),
                        )
                        cur_ms += duration_frames * ms_per_frame
                        continue
                    if is_last:
                        requested_color = max(
                            round(self.minimum_color_ms * cfg.fps / 1000),
                            round(duration_frames * self.color_reserve_ratio),
                        )
                        color_frames = max(1, min(duration_frames - 1, requested_color)) if duration_frames > 1 else 0
                    else:
                        color_frames = 0
                    ink_frames = max(1, duration_frames - color_frames)
                    scene_color_centers.extend(self._render_layered_lines(writer, ink_frames, allowed))
                    cur_ms += ink_frames * ms_per_frame
                    if is_last and color_frames > 0:
                        if cfg.color_fill == "local-brush":
                            self._wash_local_brush(writer, color_frames, scene_color_mask)
                        elif cfg.color_fill == "brush" and scene_color_centers:
                            self._wash_brush(writer, color_frames, scene_color_centers, scene_color_mask)
                        else:
                            self._wash_contour(writer, color_frames, scene_color_mask)
                        cur_ms += color_frames * ms_per_frame
                    continue

                weight_sum = cfg.ink_weight + cfg.color_weight
                ink_frames = max(1, round(dur_ms * cfg.ink_weight / weight_sum * cfg.fps / 1000))
                color_frames = max(1, round(dur_ms * cfg.color_weight / weight_sum * cfg.fps / 1000))

                if cfg.ink_path_mode == "skeleton":
                    strokes = self._region_skeleton_strokes(allowed)
                    if strokes:
                        samples, pen_lifts = [], set()
                        for si, stroke in enumerate(strokes):
                            if si > 0:
                                pen_lifts.add(len(samples))
                            samples.extend(stroke)
                        self._lay_ink(writer, ink_frames, samples, pen_lifts, allowed)
                        centers = samples
                    else:
                        path = self._region_grid_path(allowed)
                        samples, pen_lifts, _ = self._grid_plan(path) if path else ([], set(), [])
                        self._lay_ink(writer, ink_frames, samples, pen_lifts, allowed)
                        centers = [self._cell_center(c) for c in path]
                else:
                    path = self._region_grid_path(allowed)
                    if path:
                        samples, pen_lifts, sample_cell = self._grid_plan(path)
                        # 块填充：随笔尖推进逐格铺满（保证文字/大块实心）
                        self._lay_ink_grid(writer, ink_frames, samples, pen_lifts, sample_cell, path, allowed)
                        centers = [self._cell_center(c) for c in path]
                    else:
                        self._lay_ink(writer, ink_frames, [], set(), None, allowed)
                        centers = []

                cur_ms += ink_frames * ms_per_frame

                if cfg.color_fill == "contour-wipe":
                    self._wash_contour(writer, color_frames, allowed)
                else:
                    self._wash_brush(writer, color_frames, centers, allowed)
                cur_ms += color_frames * ms_per_frame

            if self.transition:
                erase_start = lead_ms + int(self.transition["eraseStartMs"])
                erase_end = lead_ms + int(self.transition["eraseEndMs"])
                clean_end = lead_ms + int(self.transition["cleanCanvasEndMs"])
                if clean_end > total_ms + 1:
                    raise RuntimeError(
                        f"板擦转场结束 {clean_end}ms 超出本幕总时长 {total_ms}ms；"
                        "请让 storyboard 场景边界覆盖真实停顿，不得在尾部追加转场。"
                    )
                if erase_start < cur_ms - 1:
                    raise RuntimeError(
                        f"板擦开始时间 {erase_start}ms 早于绘制完成时间 {round(cur_ms)}ms；"
                        "请扩大末元素 region/调整 annotation 时序，不得覆盖擦除。"
                    )
                fill_static(erase_start)
                erase_frames = max(1, round((erase_end - erase_start) * cfg.fps / 1000))
                self._render_eraser_transition(writer, erase_frames)
                cur_ms += erase_frames * ms_per_frame
                fill_static(clean_end)
                if clean_end < total_ms:
                    fill_static(total_ms)
            else:
                # 最后一幕保留完整画面；音频较长时由上层把 total_ms 延长。
                if cur_ms + 500 > total_ms + ms_per_frame:
                    raise RuntimeError(
                        f"最后一幕完整画面停留不足 500ms：当前绘制完成约 {round(cur_ms)}ms，"
                        f"本幕总时长 {total_ms}ms；不得为动画事后增加时长。"
                    )
                gaze_until = total_ms
                self.drawn[...] = self.color_img.astype(np.float32)
                fill_static(gaze_until)
        finally:
            writer.release()
            self.hand_motion_qa = writer.hand_qa.summary()
        if target_frames is not None:
            print(
                f"  精确帧预算: {writer.frames_written}/{target_frames} 帧"
                + (f"（截断 {writer.frames_attempted - target_frames} 次超额写入）" if writer.frames_attempted > target_frames else "")
            )
        return raw_path

    # 网格起笔专用：带块填充，笔尖与揭墨同步
    def _lay_ink_grid(self, writer, frames: int, samples, pen_lifts, sample_cell, path, allowed) -> None:
        if frames <= 0:
            return
        n = len(samples)
        if n == 0:
            self._write_idle_hand_frames(writer, frames)
            return
        motion_frames, motion = _plan_continuous_motion(
            samples,
            frames,
            pen_lifts,
            fps=int(self.cfg.fps),
            short_edge=min(self.out_w, self.out_h),
        )
        self.motion_plans.append({"phase": "grid-ink", **motion})
        fade_in = _fade_opacities(frames, int(self.cfg.fps), HAND_FADE_IN_MS, fade_in=True)
        hold_fade = _fade_opacities(
            int(motion.get("hold_frames", 0)),
            int(self.cfg.fps),
            HAND_FADE_OUT_MS,
            fade_in=False,
        ) if not motion.get("completed") else []
        cells_done = 0
        previous: MotionFrame | None = None
        for frame_index, current in enumerate(motion_frames):
            self._reveal_motion_step(previous, current, samples, pen_lifts, allowed)
            completed_sample = max(0, current.sample_index - 1)
            endpoint = samples[min(current.sample_index, len(samples) - 1)]
            if math.hypot(current.x - endpoint[0], current.y - endpoint[1]) <= 1.0:
                completed_sample = current.sample_index
            target_cell = sample_cell[min(completed_sample, len(sample_cell) - 1)]
            while cells_done <= target_cell and cells_done < len(path):
                self._ink_stamp_cell(path[cells_done], allowed)
                cells_done += 1
            opacity = fade_in[frame_index]
            if current.state == "hold" and hold_fade:
                hold_index = frame_index - int(motion["motion_frames"])
                opacity = hold_fade[min(max(0, hold_index), len(hold_fade) - 1)]
            if opacity > 0.0:
                self._write_tip_frame(
                    writer,
                    current.x,
                    current.y,
                    opacity=opacity,
                    state=current.state,
                    pen_down=current.pen_down,
                )
            else:
                writer.write(self.drawn.astype(np.uint8))
            previous = current
        while cells_done < len(path):
            self._ink_stamp_cell(path[cells_done], allowed)
            cells_done += 1


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="SRT 白板动画整合渲染器（V3 mask + stream + semantic animation + eraser）")
    p.add_argument("image", help="线稿图路径")
    p.add_argument("annotation", help="同名 annotation.json 路径")
    p.add_argument("output", help="输出 MP4 路径")
    p.add_argument("hand", nargs="?", default=str(DEFAULT_HAND), help="手部素材 PNG（默认内置）")
    p.add_argument("--total-ms", type=int, default=None, help="总时长；缺省用标注 sceneDurationMs")
    p.add_argument("--lead-frames", type=int, default=0, help="场景内容前的空白帧数，用于对齐绝对音频时间轴")
    p.add_argument("--target-frames", type=int, default=None, help="精确输出帧数；不足复制末帧，超出则截断")
    p.add_argument("--hand-mode", choices=["small-hand", "presenter", "no-hand", "full-hand"], default="small-hand",
                   help="手部模式：small-hand 默认小手；presenter 通用角色资产包；no-hand 隐藏；full-hand 兼容旧大手")
    p.add_argument("--small-hand", dest="hand_mode", action="store_const", const="small-hand", help="small-hand 的显式别名")
    p.add_argument("--presenter", dest="hand_mode", action="store_const", const="presenter", help="启用通用 Presenter 抱笔与推板擦覆盖层")
    p.add_argument("--no-hand", dest="hand_mode", action="store_const", const="no-hand", help="完全不显示手部")
    p.add_argument("--full-hand", dest="hand_mode", action="store_const", const="full-hand", help="兼容模式：旧大手高度策略")
    p.add_argument("--bare-tip", dest="hand_mode", action="store_const", const="no-hand",
                   help="历史兼容别名：实际关闭手部覆盖，不是小裸笔尖")
    p.add_argument("--eraser-hand", default=str(DEFAULT_ERASER), help="板擦手部素材 PNG（默认内置）")
    p.add_argument("--animation-plan", default=None, help="可选 V3 animation-plan.json")
    p.add_argument("--scene-id", default=None, help="animation-plan 中要渲染的 scene id")
    p.add_argument("--asset-manifest", default=str(DEFAULT_MANIFEST), help="手部素材与归一化锚点 manifest")
    p.add_argument("--ink-path", default="grid", choices=["grid", "skeleton"],
                   help="笔迹路径: grid 网格(默认); skeleton 骨架追踪")
    p.add_argument("--stroke-planner", default="semantic-v2", choices=["semantic-v2", "reading-bands-v1"],
                   help="笔画规划: semantic-v2 主体/组件/轮廓优先(默认); reading-bands-v1 旧水平阅读带")
    p.add_argument("--color-fill", default="local-brush", choices=["local-brush", "contour-wipe", "brush"],
                   help="上色: local-brush 逐对象局部手刷(默认); contour-wipe 旧轮廓扫描; brush 旧轨迹刷")
    p.add_argument("--draw-mode", default="layered", choices=["layered", "legacy"],
                   help="绘制编排: layered 逐对象可识别完成(默认); legacy 旧逐区域画法")
    p.add_argument("--color-reserve-ratio", type=float, default=0.32,
                   help="layered 模式整幕基础色目标比例，分配到每个对象，限制在 0.18–0.45")
    p.add_argument("--minimum-color-ms", type=int, default=900,
                   help="layered 模式整幕期望的最短基础色总时长；时间不足时压缩润色")
    p.add_argument("--pause", default="heavy", choices=["heavy", "auto", "light", "off"],
                   help="起笔段停顿节奏（预留，逐区域画法下影响较弱）")
    p.add_argument("--fps", type=int, default=None)
    p.add_argument("--grid-edge", type=int, default=None)
    p.add_argument("--brush-radius", type=int, default=None)
    p.add_argument("--cap-long-edge", type=int, default=None,
                   help="输出长边像素上限（预览可调小加速，默认 1080）")
    p.add_argument("--profile-json", default=None, help="可选：写入详细阶段耗时 JSON 报告")
    p.add_argument("--title-card-text", default=None, help="整幕持续显示的左上角重点卡片文字")
    p.add_argument("--title-card-accent", default="#356AE6", help="文字卡片左侧强调色")
    p.add_argument("--title-card-font", default=None, help="可选文字卡片字体文件")
    return p.parse_args(argv)


def _build_cfg(args) -> sr.Config:
    kw: dict = {}
    if args.fps is not None:
        kw["fps"] = args.fps
    if args.grid_edge is not None:
        kw["grid_edge"] = args.grid_edge
    if args.brush_radius is not None:
        kw["brush_radius"] = args.brush_radius
    if args.cap_long_edge is not None:
        kw["cap_long_edge"] = args.cap_long_edge
    kw["ink_path_mode"] = args.ink_path
    kw["stroke_planner"] = args.stroke_planner
    kw["color_fill"] = args.color_fill
    kw["pause_mode"] = args.pause
    return sr.Config(**kw)


def main(argv=None) -> int:
    t_global_start = time.perf_counter()
    args = _parse_args(argv)
    cfg = _build_cfg(args)

    print("=" * 56)
    print("SRT 白板动画整合渲染器 (mask 编排 + stream 画法)")
    print("=" * 56)

    t_img_start = time.perf_counter()
    image_bgr = sr._imread_any(args.image)
    t_img_ms = (time.perf_counter() - t_img_start) * 1000.0
    if image_bgr is None:
        print(f"[err] 无法读取图片: {args.image}")
        return 1
    t_ann_start = time.perf_counter()
    try:
        annotation = json.loads(Path(args.annotation).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[err] 无法读取标注: {e}")
        return 1
    t_ann_ms = (time.perf_counter() - t_ann_start) * 1000.0
    if not annotation.get("elements"):
        print("[err] 标注中没有 elements")
        return 1

    total_ms = args.total_ms if args.total_ms is not None else annotation.get("sceneDurationMs")
    if not total_ms:
        last = max(e["reveal"]["startMs"] + e["reveal"]["durationMs"] for e in annotation["elements"])
        total_ms = last + 1000

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_name(out_path.stem + "_raw.mp4")

    manifest_path = Path(args.asset_manifest)
    hand_png = Path(args.hand) if args.hand else None
    eraser_png = Path(args.eraser_hand) if args.eraser_hand else None
    animation_plan = None
    transition = None
    scene_id = args.scene_id or annotation.get("sceneId")
    if args.animation_plan:
        try:
            animation_plan = json.loads(Path(args.animation_plan).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[err] 无法读取 animation-plan.json：{exc}")
            return 1
        if scene_id:
            for plan_scene in animation_plan.get("scenes", []):
                if plan_scene.get("sceneId") == scene_id:
                    transition = plan_scene.get("transition")
                    if transition:
                        transition = dict(transition)
                        scene_start = int(plan_scene.get("sceneStartMs", 0))
                        for key in ("eraseStartMs", "eraseEndMs", "cleanCanvasStartMs", "cleanCanvasEndMs"):
                            if key in transition:
                                transition[key] = int(transition[key]) - scene_start
                    break
    annotation = effective_annotation(annotation, str(scene_id), animation_plan, int(cfg.fps))
    t_init_start = time.perf_counter()
    renderer = RegionStreamRenderer(
        image_bgr,
        annotation,
        cfg,
        hand_png,
        args.hand_mode,
        eraser_png,
        animation_plan,
        scene_id,
        transition,
        manifest_path,
        args.draw_mode,
        args.stroke_planner,
        args.color_reserve_ratio,
        args.minimum_color_ms,
    )
    t_init_ms = (time.perf_counter() - t_init_start) * 1000.0

    print(f"  输入: {args.image}")
    print(f"  输出尺寸: {renderer.out_w}x{renderer.out_h}, 帧率: {cfg.fps}")
    print(f"  区域数: {len(annotation['elements'])}, 总时长: {total_ms}ms, "
          f"笔迹: {cfg.ink_path_mode}/{renderer.stroke_planner}, 上色: {cfg.color_fill}, 手部: {args.hand_mode}, "
          f"绘制编排: {renderer.draw_mode}, 板擦转场: {'on' if transition else 'off'}")

    t_render_start = time.perf_counter()
    try:
        title_card_overlay = _build_title_card_overlay(
            args.title_card_text,
            args.title_card_accent,
            args.title_card_font,
            renderer.out_w,
            renderer.out_h,
        )
    except (OSError, ValueError) as exc:
        print(f"[err] 文字卡片无法生成：{exc}")
        return 1
    renderer.render_to(raw_path, total_ms, args.lead_frames, args.target_frames, title_card_overlay)
    t_render_ms = (time.perf_counter() - t_render_start) * 1000.0

    t_encode_start = time.perf_counter()
    final = sr.transcode_h264(raw_path, out_path)
    t_encode_ms = (time.perf_counter() - t_encode_start) * 1000.0

    total_wall_ms = (time.perf_counter() - t_global_start) * 1000.0
    size_mb = final.stat().st_size / (1024 * 1024)

    if args.profile_json:
        profile_data = {
            "scene_id": scene_id,
            "scene_plan_sha256": hashlib.sha256(json.dumps(
                next((item for item in (animation_plan or {}).get("scenes", []) if str(item.get("sceneId")) == str(scene_id)), {}),
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest(),
            "resolution": [renderer.out_w, renderer.out_h],
            "fps": cfg.fps,
            "elements": len(annotation["elements"]),
            "stroke_planner": renderer.stroke_planner,
            "stroke_strategy": "fast-semantic-trace-v1" if renderer.stroke_planner == "semantic-v2" else "legacy",
            "color_fill": cfg.color_fill,
            "color_schedule": renderer.color_schedule,
            "pixel_ownership": renderer.pixel_ownership,
            "title_card": {
                "text": args.title_card_text,
                "accent": args.title_card_accent,
                "position": "top-left",
                "style": "outlined-label-v1",
            } if args.title_card_text else None,
            "ownership_metrics": renderer.ownership_metrics,
            "phase_budget_metrics": renderer.phase_budget_metrics,
            "stroke_metrics": renderer.stroke_metrics,
            "color_metrics": renderer.color_metrics,
            "motion_plans": renderer.motion_plans,
            "hand_motion_qa": renderer.hand_motion_qa,
            "timings_ms": {
                "annotation_read_validate": round(t_ann_ms, 2),
                "image_decode": round(t_img_ms, 2),
                "manifest_hand_assets": round(renderer.manifest_hand_assets_ms, 2),
                "mask_grid_init": round(renderer.mask_grid_init_ms, 2),
                "frame_render": round(t_render_ms, 2),
                "h264_transcode": round(t_encode_ms, 2),
                "total_scene_wall": round(total_wall_ms, 2),
            },
            "output_bytes": final.stat().st_size,
        }
        profile_path = Path(args.profile_json)
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(profile_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n最终视频: {final}  ({size_mb:.2f} MB)")
    print(f"  阶段耗时: 初始化 {t_init_ms:.1f}ms | 渲染 {t_render_ms:.1f}ms | 转码 {t_encode_ms:.1f}ms | 总计 {total_wall_ms:.1f}ms")
    print("=" * 56)
    print(f"OUTPUT={final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
