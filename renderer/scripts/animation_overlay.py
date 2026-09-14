#!/usr/bin/env python3
"""Semantic-safe post-reveal animation with legacy compatibility.

V3.3 effects tint or outline only real foreground pixels inside a focusTarget;
they never synthesize a rectangle, circle, arrow, or freehand path. V3.2 keeps
its camera-only focus-push. Explicit V3.1 gesture and mechanical events remain
readable for old projects.
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _ease(p: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * _clamp(p))


def _parse_color(value: Any, fallback: tuple[int, int, int] = (206, 112, 46)) -> tuple[int, int, int]:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) != 6:
        return fallback
    try:
        r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        return b, g, r
    except ValueError:
        return fallback


def _phase_range(event: dict[str, Any], name: str) -> tuple[float, float] | None:
    phase = event.get("phase", {}).get(name)
    if not isinstance(phase, dict):
        return None
    try:
        return float(phase.get("startMs", 0)), float(phase.get("endMs", 0))
    except (TypeError, ValueError):
        return None


def _phase_progress(time_ms: float, event: dict[str, Any], name: str) -> float:
    value = _phase_range(event, name)
    if value is None:
        return 0.0
    start, end = value
    return _clamp((time_ms - start) / max(1.0, end - start))


def _lerp_point(a: tuple[float, float], b: tuple[float, float], p: float) -> tuple[int, int]:
    p = _clamp(p)
    return round(a[0] + (b[0] - a[0]) * p), round(a[1] + (b[1] - a[1]) * p)


class AnimationOverlay:
    """Apply V3.1 gestures and focus push to a rendered whiteboard frame."""

    def __init__(
        self,
        annotation: dict[str, Any],
        plan: dict[str, Any] | None,
        scene_id: str | None,
        sx: float,
        sy: float,
        out_w: int,
        out_h: int,
        background_bgr: np.ndarray,
    ) -> None:
        self.sx = sx
        self.sy = sy
        self.out_w = out_w
        self.out_h = out_h
        self.background_bgr = np.asarray(background_bgr, dtype=np.uint8)
        self.owner_masks: dict[str, np.ndarray] = {}
        self.subtitle_safe_top = max(1, round(out_h * 0.84))
        self.regions: dict[str, tuple[int, int, int, int]] = {}
        for element in annotation.get("elements", []):
            region = element.get("region", {})
            if not all(key in region for key in ("x", "y", "width", "height")):
                continue
            key = str(element.get("id") or element.get("label") or element.get("sequence"))
            x0 = round(int(region["x"]) * sx)
            y0 = round(int(region["y"]) * sy)
            x1 = round((int(region["x"]) + int(region["width"])) * sx)
            y1 = round((int(region["y"]) + int(region["height"])) * sy)
            self.regions[key] = (max(0, x0), max(0, y0), min(out_w, x1), min(out_h, y1))
        self.events: list[dict[str, Any]] = []
        if plan:
            for scene in plan.get("scenes", []):
                if scene.get("sceneId") == scene_id:
                    self.events = list(scene.get("events", []))
                    break

    def _region(self, event: dict[str, Any]) -> tuple[int, int, int, int]:
        region = self.regions.get(str(event.get("targetElementId") or event.get("targetElement", "")))
        if region:
            return region
        return (0, 0, self.out_w, min(self.out_h, self.subtitle_safe_top))

    def _target_region(self, event: dict[str, Any]) -> tuple[int, int, int, int]:
        target = event.get("focusTarget") or {}
        try:
            x0 = round(float(target["x"]) * self.sx)
            y0 = round(float(target["y"]) * self.sy)
            x1 = round((float(target["x"]) + float(target["width"])) * self.sx)
            y1 = round((float(target["y"]) + float(target["height"])) * self.sy)
        except (KeyError, TypeError, ValueError):
            return self._region(event)
        return (
            max(0, min(self.out_w - 1, x0)),
            max(0, min(self.subtitle_safe_top - 1, y0)),
            max(1, min(self.out_w, x1)),
            max(1, min(self.subtitle_safe_top, y1)),
        )

    @staticmethod
    def _center(region: tuple[int, int, int, int]) -> tuple[int, int]:
        x0, y0, x1, y1 = region
        return round((x0 + x1) / 2), round((y0 + y1) / 2)

    def _focus_center(self, event: dict[str, Any]) -> tuple[int, int]:
        target = event.get("focusTarget") or {}
        center = target.get("center")
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            return round(float(center[0]) * self.sx), round(float(center[1]) * self.sy)
        return self._center(self._region(event))

    def _scaled_path(self, event: dict[str, Any]) -> list[tuple[int, int]]:
        raw_path = event.get("gesturePath") or event.get("path") or []
        points: list[tuple[int, int]] = []
        for point in raw_path:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x = max(0, min(self.out_w - 1, round(float(point[0]) * self.sx)))
            y = max(0, min(self.subtitle_safe_top - 2, round(float(point[1]) * self.sy)))
            current = (x, y)
            if not points or current != points[-1]:
                points.append(current)
        return points

    def _scaled_point(self, point: Any, default: tuple[int, int]) -> tuple[int, int]:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return default
        return (
            max(0, min(self.out_w - 1, round(float(point[0]) * self.sx))),
            max(0, min(self.subtitle_safe_top - 2, round(float(point[1]) * self.sy))),
        )

    def _event_persist_end(self, event: dict[str, Any]) -> float:
        try:
            return float(event.get("persistUntilMs", event.get("endMs", 0)))
        except (TypeError, ValueError):
            return float(event.get("endMs", 0) or 0)

    def _active(self, event: dict[str, Any], time_ms: float) -> bool:
        try:
            start = float(event.get("startMs", 0))
        except (TypeError, ValueError):
            start = 0.0
        return start <= time_ms <= self._event_persist_end(event)

    def _warp(self, frame: np.ndarray, matrix: np.ndarray) -> np.ndarray:
        return cv2.warpAffine(
            frame,
            matrix,
            (self.out_w, self.out_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=tuple(int(value) for value in self.background_bgr),
        )

    def _focus_scale(self, event: dict[str, Any], time_ms: float) -> float:
        camera = _phase_range(event, "camera")
        if camera is None:
            # Legacy focus-zoom events use their old start/end but now have a
            # monotonic push instead of the old sine expand/retract.
            start = float(event.get("startMs", 0))
            end = min(float(event.get("endMs", start + 1)), start + 550)
        else:
            start, end = camera
        progress = _ease(_clamp((time_ms - start) / max(1.0, end - start)))
        amount = _clamp(float(event.get("intensity", 0.05)), 0.04, 0.06)
        return 1.0 + amount * progress

    def _focus_push(self, frame: np.ndarray, event: dict[str, Any], time_ms: float) -> np.ndarray:
        cx, cy = self._focus_center(event)
        scale = self._focus_scale(event, time_ms)
        matrix = np.array([[scale, 0.0, cx - scale * cx], [0.0, scale, cy - scale * cy]], dtype=np.float32)
        return self._warp(frame, matrix)

    def camera_active(self, time_ms: float) -> bool:
        return any(
            event.get("effect") in {"focus-push", "focus-zoom"}
            and self._active(event, time_ms)
            and (_phase_range(event, "camera") is None or time_ms >= _phase_range(event, "camera")[0])
            for event in self.events
        )

    def _line_color(self, event: dict[str, Any]) -> tuple[int, int, int]:
        style = event.get("style") or {}
        # New plans always provide style-registry colorHex. The fallback keeps
        # old plans readable without changing their compatibility semantics.
        return _parse_color(style.get("colorHex"), (206, 112, 46))

    def _line_width(self, event: dict[str, Any]) -> int:
        style = event.get("style") or {}
        ratio = float(style.get("lineWidthShortEdgeRatio", 0.0036) or 0.0036)
        return max(2, min(12, round(min(self.out_w, self.out_h) * ratio)))

    def _overlay_line(self, frame: np.ndarray, draw_fn, alpha: float = 1.0) -> np.ndarray:
        layer = frame.copy()
        draw_fn(layer)
        return cv2.addWeighted(layer, _clamp(alpha), frame, 1.0 - _clamp(alpha), 0.0)

    def _foreground_mask(self, frame: np.ndarray, event: dict[str, Any]) -> np.ndarray:
        """Find only already-visible source pixels inside the semantic target."""

        x0, y0, x1, y1 = self._target_region(event)
        mask = np.zeros((self.out_h, self.out_w), dtype=np.uint8)
        if x1 <= x0 or y1 <= y0:
            return mask
        crop = frame[y0:y1, x0:x1].astype(np.int16)
        background = self.background_bgr.astype(np.int16).reshape(1, 1, 3)
        distance = np.linalg.norm(crop - background, axis=2)
        local = (distance > 18).astype(np.uint8) * 255
        if not np.any(local):
            return mask
        local = cv2.morphologyEx(local, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(local, connectivity=8)
        cleaned = np.zeros_like(local)
        for label in range(1, count):
            if int(stats[label, cv2.CC_STAT_AREA]) >= 3:
                cleaned[labels == label] = 255
        mask[y0:y1, x0:x1] = cleaned
        owner = self.owner_masks.get(str(event.get("targetElementId") or ""))
        if owner is None:
            # No semantic source map means no authorized foreground effect.
            return np.zeros_like(mask)
        mask[~owner] = 0
        return mask

    @staticmethod
    def _phase_alpha(time_ms: float, event: dict[str, Any], peak: float = 0.78) -> float:
        accent = _phase_range(event, "accent")
        hold = _phase_range(event, "hold")
        leave = _phase_range(event, "leave")
        if accent and accent[0] <= time_ms < accent[1]:
            return peak * _ease((time_ms - accent[0]) / max(1.0, accent[1] - accent[0]))
        if hold and hold[0] <= time_ms < hold[1]:
            return peak
        if leave and leave[0] <= time_ms <= leave[1]:
            return peak * (1.0 - _ease((time_ms - leave[0]) / max(1.0, leave[1] - leave[0])))
        return 0.0

    @staticmethod
    def _tint_mask(
        frame: np.ndarray,
        mask: np.ndarray,
        color: tuple[int, int, int],
        strength: float,
    ) -> np.ndarray:
        strength = _clamp(strength)
        if strength <= 0 or not np.any(mask):
            return frame
        output = frame.copy()
        active = mask > 0
        source = frame[active].astype(np.float32)
        tint = np.asarray(color, dtype=np.float32)
        output[active] = np.clip(source * (1.0 - strength) + tint * strength, 0, 255).astype(np.uint8)
        return output

    def _contour_indicate(self, frame: np.ndarray, event: dict[str, Any], time_ms: float) -> np.ndarray:
        alpha = self._phase_alpha(time_ms, event, 0.82)
        foreground = self._foreground_mask(frame, event)
        if alpha <= 0 or not np.any(foreground):
            return frame
        width = self._line_width(event)
        kernel_size = max(3, width * 2 + 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        interior = cv2.erode(foreground, kernel, iterations=1)
        contour_pixels = cv2.subtract(foreground, interior)
        return self._tint_mask(frame, contour_pixels, self._line_color(event), alpha)

    def _highlight_wash(self, frame: np.ndarray, event: dict[str, Any], time_ms: float) -> np.ndarray:
        alpha = self._phase_alpha(time_ms, event, 0.32)
        foreground = self._foreground_mask(frame, event)
        if alpha <= 0 or not np.any(foreground):
            return frame
        x0, _, x1, _ = self._target_region(event)
        accent = _phase_range(event, "accent")
        if accent and accent[0] <= time_ms < accent[1]:
            progress = _ease((time_ms - accent[0]) / max(1.0, accent[1] - accent[0]))
            limit = round(x0 + (x1 - x0) * progress)
            foreground[:, max(0, limit):] = 0
        return self._tint_mask(frame, foreground, self._line_color(event), alpha)

    def _passing_flash(self, frame: np.ndarray, event: dict[str, Any], time_ms: float) -> np.ndarray:
        accent = _phase_range(event, "accent")
        if not accent or not accent[0] <= time_ms <= accent[1]:
            return frame
        foreground = self._foreground_mask(frame, event)
        if not np.any(foreground):
            return frame
        x0, _, x1, _ = self._target_region(event)
        progress = _ease((time_ms - accent[0]) / max(1.0, accent[1] - accent[0]))
        center = x0 + (x1 - x0) * progress
        band = max(8, round((x1 - x0) * 0.16))
        left, right = round(center - band), round(center + band)
        foreground[:, :max(0, left)] = 0
        foreground[:, min(self.out_w, right):] = 0
        envelope = math.sin(math.pi * _clamp(progress))
        return self._tint_mask(frame, foreground, self._line_color(event), 0.48 * envelope)

    def _partial_path(self, points: list[tuple[int, int]], progress: float) -> list[tuple[int, int]]:
        if len(points) < 2:
            return points
        progress = _clamp(progress)
        distances = [0.0]
        for first, second in zip(points, points[1:]):
            distances.append(distances[-1] + math.hypot(second[0] - first[0], second[1] - first[1]))
        target = distances[-1] * progress
        index = max(0, min(len(points) - 2, int(np.searchsorted(distances, target, side="right") - 1)))
        segment = max(1e-6, distances[index + 1] - distances[index])
        t = _clamp((target - distances[index]) / segment)
        partial = points[: index + 1]
        partial.append(_lerp_point(points[index], points[index + 1], t))
        return partial

    def _draw_gesture(self, frame: np.ndarray, event: dict[str, Any], time_ms: float) -> np.ndarray:
        raw_path = event.get("gesturePath") or event.get("path") or []
        # A V3.2 focus-push has no automatic underline/arrow/etc. Empty path is
        # the compatibility-safe signal; old plans remain unchanged because
        # their explicit gesturePath/path still reaches the code below.
        if event.get("effect") == "focus-push" and not raw_path:
            return frame
        points = self._scaled_path(event)
        if len(points) < 2:
            return frame
        draw_phase = _phase_range(event, "draw")
        if draw_phase is None:
            start = float(event.get("startMs", 0))
            end = float(event.get("endMs", start + 1))
        else:
            start, end = draw_phase
        if time_ms < start:
            return frame
        progress = 1.0 if time_ms >= end else _ease((time_ms - start) / max(1.0, end - start))
        partial = self._partial_path(points, progress)
        color = self._line_color(event)
        width = self._line_width(event)

        def draw(layer: np.ndarray) -> None:
            cv2.polylines(layer, [np.asarray(partial, dtype=np.int32)], False, color, width, cv2.LINE_AA)

        return self._overlay_line(frame, draw, 0.96)

    # Legacy visual effects -------------------------------------------------
    def _outline(self, frame: np.ndarray, event: dict[str, Any], p: float) -> np.ndarray:
        region = self._region(event)
        alpha = 0.75 * math.sin(math.pi * p)
        color = self._line_color(event)
        return self._overlay_line(frame, lambda layer: cv2.rectangle(layer, region[:2], region[2:], color, self._line_width(event), cv2.LINE_AA), alpha)

    def _circle(self, frame: np.ndarray, event: dict[str, Any], p: float) -> np.ndarray:
        x0, y0, x1, y1 = self._region(event)
        cx, cy = self._center((x0, y0, x1, y1))
        rx = max(18, round((x1 - x0) * 0.58))
        ry = max(14, round((y1 - y0) * 0.58))
        points = []
        count = max(3, round(28 * _ease(p)))
        for index in range(count):
            angle = -math.pi / 2 + 2 * math.pi * index / 27
            jitter = 1.0 + 0.025 * math.sin(index * 2.7)
            points.append((round(cx + rx * jitter * math.cos(angle)), round(cy + ry * jitter * math.sin(angle))))
        alpha = 0.78 * math.sin(math.pi * p)
        return self._overlay_line(frame, lambda layer: cv2.polylines(layer, [np.asarray(points, dtype=np.int32)], False, self._line_color(event), self._line_width(event), cv2.LINE_AA), alpha)

    def _flow(self, frame: np.ndarray, event: dict[str, Any], p: float) -> np.ndarray:
        points = self._scaled_path(event)
        if len(points) < 2:
            return frame
        partial = self._partial_path(points, _ease(p))
        color = self._line_color(event)
        return self._overlay_line(frame, lambda layer: cv2.polylines(layer, [np.asarray(partial, dtype=np.int32)], False, color, self._line_width(event), cv2.LINE_AA), 0.8 * math.sin(math.pi * p))

    def _ripple(self, frame: np.ndarray, event: dict[str, Any], p: float) -> np.ndarray:
        cx, cy = self._center(self._region(event))
        radius = round(8 + 36 * _ease(p))
        alpha = 0.7 * (1.0 - p)
        return self._overlay_line(frame, lambda layer: cv2.circle(layer, (cx, cy), radius, self._line_color(event), self._line_width(event), cv2.LINE_AA), alpha)

    def hand_position(self, time_ms: float) -> tuple[int, int] | None:
        """Return the small-hand pen-tip position for the current gesture."""

        active = [
            event for event in self.events
            if (
                (event.get("effect", "").startswith("gesture-") or event.get("effect") == "focus-push")
                and int(event.get("startMs", 0)) <= time_ms <= int(event.get("endMs", 0))
            )
        ]
        if not active:
            return None
        event = sorted(active, key=lambda item: (bool(item.get("auxiliary")), int(item.get("startMs", 0))))[0]
        path = self._scaled_path(event)
        if len(path) < 2:
            return None
        prepare = _phase_range(event, "prepare")
        draw = _phase_range(event, "draw")
        hold = _phase_range(event, "hold")
        leave = _phase_range(event, "leave")
        start = self._scaled_point((event.get("handBehavior") or {}).get("handStart"), path[0])
        end = self._scaled_point((event.get("handBehavior") or {}).get("handLeave"), path[-1])
        if prepare and prepare[0] <= time_ms < prepare[1]:
            return _lerp_point(start, path[0], (time_ms - prepare[0]) / max(1.0, prepare[1] - prepare[0]))
        if draw and draw[0] <= time_ms < draw[1]:
            return self._partial_path(path, (time_ms - draw[0]) / max(1.0, draw[1] - draw[0]))[-1]
        if hold and hold[0] <= time_ms < hold[1]:
            return path[-1]
        if leave and leave[0] <= time_ms <= leave[1]:
            return _lerp_point(path[-1], end, (time_ms - leave[0]) / max(1.0, leave[1] - leave[0]))
        return None

    def apply(self, frame: np.ndarray, time_ms: float) -> np.ndarray:
        active = [event for event in self.events if self._active(event, time_ms)]
        if not active:
            return frame
        output = frame
        # Camera motion is applied first. It is monotonic and held at its
        # final scale until the event persistence boundary.
        for event in active:
            effect = str(event.get("effect", ""))
            if effect in {"focus-push", "focus-zoom"}:
                camera = _phase_range(event, "camera")
                if camera is None or time_ms >= camera[0]:
                    output = self._focus_push(output, event, time_ms)
        for event in active:
            effect = str(event.get("effect", ""))
            if effect.startswith("gesture-") or effect == "focus-push":
                output = self._draw_gesture(output, event, time_ms)
            elif effect == "contour-indicate":
                output = self._contour_indicate(output, event, time_ms)
            elif effect == "highlight-wash":
                output = self._highlight_wash(output, event, time_ms)
            elif effect == "passing-flash":
                output = self._passing_flash(output, event, time_ms)
            elif effect in {"pulse", "pulse/outline", "outline"}:
                output = self._outline(output, event, _clamp((time_ms - float(event.get("startMs", 0))) / max(1.0, float(event.get("endMs", 1)) - float(event.get("startMs", 0)))))
            elif effect in {"hand-drawn-circle", "circle"}:
                output = self._circle(output, event, _clamp((time_ms - float(event.get("startMs", 0))) / max(1.0, float(event.get("endMs", 1)) - float(event.get("startMs", 0)))))
            elif effect == "arrow-flow":
                output = self._flow(output, event, _clamp((time_ms - float(event.get("startMs", 0))) / max(1.0, float(event.get("endMs", 1)) - float(event.get("startMs", 0)))))
            elif effect in {"tap/ripple", "ripple", "tap"}:
                output = self._ripple(output, event, _clamp((time_ms - float(event.get("startMs", 0))) / max(1.0, float(event.get("endMs", 1)) - float(event.get("startMs", 0)))))
        return output
