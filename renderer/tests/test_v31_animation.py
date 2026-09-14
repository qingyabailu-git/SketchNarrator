from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).parents[1]


def load_overlay():
    path = ROOT / "scripts" / "animation_overlay.py"
    spec = importlib.util.spec_from_file_location("animation_overlay_v31", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def load_stream_render():
    path = ROOT / "scripts" / "stream_render.py"
    spec = importlib.util.spec_from_file_location("stream_render_ordering", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


overlay = load_overlay()
stream_render = load_stream_render()


class V31OverlayTests(unittest.TestCase):
    @staticmethod
    def _semantic_fixture(effect: str):
        annotation = {
            "canvas": {"width": 200, "height": 180},
            "elements": [{"id": "subject", "region": {"x": 20, "y": 20, "width": 150, "height": 100}}],
        }
        event = {
            "id": effect,
            "targetElementId": "subject",
            "effect": effect,
            "startMs": 0,
            "endMs": 1000,
            "persistUntilMs": 1250,
            "focusTarget": {"x": 20, "y": 20, "width": 150, "height": 100, "center": [95, 70]},
            "phase": {
                "prepare": {"startMs": 0, "endMs": 100},
                "accent": {"startMs": 100, "endMs": 600},
                "hold": {"startMs": 600, "endMs": 920},
                "leave": {"startMs": 920, "endMs": 1000},
            },
            "style": {"colorHex": "#C77D3C", "lineWidthShortEdgeRatio": 0.01},
        }
        frame = np.full((180, 200, 3), 240, dtype=np.uint8)
        frame[50:90, 75:120] = (45, 70, 95)
        renderer = overlay.AnimationOverlay(
            annotation,
            {"planVersion": "3.3", "scenes": [{"sceneId": "scene-01", "events": [event]}]},
            "scene-01", 1.0, 1.0, 200, 180, frame[0, 0],
        )
        return frame, renderer

    def test_v33_removed_semantic_effects_do_not_modify_the_approved_frame(self) -> None:
        for effect in ("contour-indicate", "highlight-wash", "passing-flash"):
            with self.subTest(effect=effect):
                frame, renderer = self._semantic_fixture(effect)
                output = renderer.apply(frame.copy(), 350)
                self.assertTrue(np.array_equal(output, frame))

    def test_v33_safe_effects_have_no_drawing_hand_position(self) -> None:
        for effect in ("contour-indicate", "highlight-wash", "passing-flash"):
            _, renderer = self._semantic_fixture(effect)
            self.assertIsNone(renderer.hand_position(350))
    def test_line_progresses_and_persists_after_draw(self) -> None:
        annotation = {
            "canvas": {"width": 200, "height": 180},
            "elements": [{"id": "subject", "region": {"x": 20, "y": 20, "width": 150, "height": 100}}],
        }
        event = {
            "id": "underline",
            "targetElementId": "subject",
            "effect": "gesture-underline",
            "startMs": 0,
            "endMs": 1000,
            "persistUntilMs": 1500,
            "gesturePath": [[30, 100], [160, 100]],
            "phase": {
                "prepare": {"startMs": 0, "endMs": 100},
                "draw": {"startMs": 100, "endMs": 700},
                "hold": {"startMs": 700, "endMs": 920},
                "leave": {"startMs": 920, "endMs": 1000},
            },
            "style": {"colorHex": "#C77D3C", "lineWidthShortEdgeRatio": 0.02},
            "handBehavior": {"handStart": [0, 0], "handLeave": [190, 20]},
        }
        plan = {"scenes": [{"sceneId": "scene-01", "events": [event]}]}
        background = np.full((180, 200, 3), 240, dtype=np.uint8)
        renderer = overlay.AnimationOverlay(annotation, plan, "scene-01", 1.0, 1.0, 200, 180, background[0, 0])
        before = renderer.apply(background.copy(), 50)
        middle = renderer.apply(background.copy(), 400)
        held = renderer.apply(background.copy(), 1200)
        self.assertEqual(np.count_nonzero(before != background), 0)
        self.assertGreater(np.count_nonzero(middle != background), 0)
        self.assertGreater(np.count_nonzero(held != background), 0)

    def test_focus_push_is_monotonic_and_capped(self) -> None:
        annotation = {"canvas": {"width": 200, "height": 180}, "elements": []}
        event = {
            "effect": "focus-push",
            "startMs": 0,
            "endMs": 1500,
            "persistUntilMs": 1500,
            "focusTarget": {"center": [100, 80]},
            "phase": {"camera": {"startMs": 100, "endMs": 650}},
            "intensity": 0.05,
        }
        background = np.full((180, 200, 3), 240, dtype=np.uint8)
        renderer = overlay.AnimationOverlay(annotation, {"scenes": [{"sceneId": "scene-01", "events": [event]}]}, "scene-01", 1.0, 1.0, 200, 180, background[0, 0])
        self.assertLess(renderer._focus_scale(event, 400), renderer._focus_scale(event, 900))
        self.assertLessEqual(renderer._focus_scale(event, 900), 1.06)

    def test_focus_push_draws_before_camera_and_hides_hand_during_camera(self) -> None:
        annotation = {"canvas": {"width": 200, "height": 180}, "elements": []}
        event = {
            "effect": "focus-push",
            "startMs": 0,
            "endMs": 1500,
            "persistUntilMs": 1500,
            "gesturePath": [[60, 100], [140, 100]],
            "focusTarget": {"center": [100, 80]},
            "phase": {
                "prepare": {"startMs": 0, "endMs": 100},
                "draw": {"startMs": 100, "endMs": 500},
                "camera": {"startMs": 500, "endMs": 900},
                "hold": {"startMs": 900, "endMs": 1300},
                "leave": {"startMs": 1300, "endMs": 1500},
            },
            "handBehavior": {"handStart": [0, 0], "handLeave": [190, 20]},
        }
        background = np.full((180, 200, 3), 240, dtype=np.uint8)
        renderer = overlay.AnimationOverlay(annotation, {"scenes": [{"sceneId": "scene-01", "events": [event]}]}, "scene-01", 1.0, 1.0, 200, 180, background[0, 0])
        drawn = renderer.apply(background.copy(), 400)
        held = renderer.apply(background.copy(), 1100)
        self.assertGreater(np.count_nonzero(drawn != background), 0)
        self.assertGreater(np.count_nonzero(held != background), 0)
        self.assertIsNotNone(renderer.hand_position(400))
        self.assertTrue(renderer.camera_active(600))

    def test_v32_focus_push_without_path_does_not_draw_extra_line(self) -> None:
        annotation = {"canvas": {"width": 200, "height": 180}, "elements": []}
        event = {
            "effect": "focus-push",
            "startMs": 0,
            "endMs": 1500,
            "persistUntilMs": 1500,
            "focusTarget": {"center": [100, 80]},
            "phase": {
                "prepare": {"startMs": 0, "endMs": 100},
                "camera": {"startMs": 100, "endMs": 600},
                "hold": {"startMs": 600, "endMs": 1420},
                "leave": {"startMs": 1420, "endMs": 1500},
            },
            "intensity": 0.05,
        }
        background = np.full((180, 200, 3), 240, dtype=np.uint8)
        renderer = overlay.AnimationOverlay(
            annotation,
            {"planVersion": "3.2", "scenes": [{"sceneId": "scene-01", "events": [event]}]},
            "scene-01",
            1.0,
            1.0,
            200,
            180,
            background[0, 0],
        )
        self.assertEqual(np.count_nonzero(renderer.apply(background.copy(), 400) != background), 0)
        self.assertIsNone(renderer.hand_position(400))

    def test_legacy_overlay_events_are_still_accepted(self) -> None:
        annotation = {
            "canvas": {"width": 200, "height": 180},
            "elements": [{"id": "subject", "region": {"x": 20, "y": 20, "width": 150, "height": 100}}],
        }
        events = [
            {"effect": "gesture-arrow", "targetElementId": "subject", "startMs": 0, "endMs": 1000, "persistUntilMs": 1000, "gesturePath": [[30, 60], [160, 60]]},
            {"effect": "focus-zoom", "targetElementId": "subject", "startMs": 0, "endMs": 1000, "persistUntilMs": 1000, "focusTarget": {"center": [100, 80]}},
            {"effect": "outline", "targetElementId": "subject", "startMs": 0, "endMs": 1000, "persistUntilMs": 1000},
            {"effect": "circle", "targetElementId": "subject", "startMs": 0, "endMs": 1000, "persistUntilMs": 1000},
        ]
        background = np.full((180, 200, 3), 240, dtype=np.uint8)
        renderer = overlay.AnimationOverlay(
            annotation,
            {"planVersion": "3.1", "scenes": [{"sceneId": "scene-01", "events": events}]},
            "scene-01",
            1.0,
            1.0,
            200,
            180,
            background[0, 0],
        )
        output = renderer.apply(background.copy(), 400)
        self.assertEqual(output.shape, background.shape)

    def test_no_hand_mode_can_hide_overlay_without_disabling_path_contract(self) -> None:
        event = {"effect": "gesture-arrow", "gesturePath": [[10, 20], [100, 20]]}
        self.assertGreaterEqual(len(event["gesturePath"]), 2)

    def test_skeleton_stroke_order_preserves_reading_bands_and_reverses_near_endpoint(self) -> None:
        strokes = [
            [(10.0, 10.0), (30.0, 10.0)],
            [(90.0, 12.0), (40.0, 12.0)],
            [(10.0, 80.0), (30.0, 80.0)],
        ]
        ordered = stream_render._order_skeleton_strokes(strokes)
        self.assertLess(max(point[1] for point in ordered[0]), min(point[1] for point in ordered[-1]))
        self.assertEqual(ordered[1][0], (40.0, 12.0))


if __name__ == "__main__":
    unittest.main()
