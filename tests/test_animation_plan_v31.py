from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

import numpy as np
from PIL import Image

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.animation_plan import (
    build_animation_plan,
    focus_target_from_pixels,
    gesture_path_for,
    reserve_animation_windows,
    validate_animation_plan,
)
from scripts.qa_final import _actual_hand_mode, _animation_plan_checks_v33, _phase_bounds


class AnimationPlanV31Tests(unittest.TestCase):
    def test_actual_hand_mode_prefers_the_current_render_evidence(self) -> None:
        project = {"renderer_profile": {"hand_mode": "small-hand"}}
        state = {"render_metrics": {"hand_mode": "no-hand"}}
        self.assertEqual(_actual_hand_mode(project, state), "no-hand")

    def test_phase_bounds_returns_valid_integer_range(self) -> None:
        event = {"phase": {"hold": {"startMs": "120", "endMs": 620}}}
        self.assertEqual(_phase_bounds(event, "hold"), (120, 620))
        self.assertIsNone(_phase_bounds(event, "missing"))
        self.assertIsNone(_phase_bounds({"phase": {"hold": {"startMs": 3, "endMs": 3}}}, "hold"))

    def _annotation(self, duration: int = 2500) -> dict:
        return {
            "sceneId": "scene-01",
            "canvas": {"width": 200, "height": 180},
            "sceneDurationMs": duration,
            "elements": [{
                "id": "subject",
                "sequence": 1,
                "label": "人物身份",
                "role": "结论",
                "region": {"x": 20, "y": 20, "width": 150, "height": 100},
                "reveal": {"startMs": 100, "durationMs": 400},
            }],
        }

    def _board(self, path: Path) -> None:
        image = np.full((180, 200, 3), (245, 235, 215), dtype=np.uint8)
        image[45:110, 70:125] = (55, 80, 100)
        Image.fromarray(image, mode="RGB").save(path)

    def test_focus_target_comes_from_foreground_pixels_and_arc_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            board = Path(temp) / "board.png"
            self._board(board)
            annotation = self._annotation()
            target = focus_target_from_pixels(annotation, annotation["elements"][0], board)
            self.assertEqual(target["source"], "foreground-pixels")
            self.assertGreater(target["confidence"], 0.18)
            path = gesture_path_for("gesture-arc", target, annotation)
            self.assertGreaterEqual(len(path), 3)
            self.assertNotEqual(path[0], path[-1])
            self.assertTrue(all(0 <= point[1] < 180 * 0.84 for point in path))

    def test_conclusion_does_not_generate_local_contour_effect(self) -> None:
        project = {
            "version": 3,
            "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 2500, "composition": "center-spoke"}],
        }
        words = [{"text": "人物身份", "start_ms": 0, "end_ms": 2400}]
        with tempfile.TemporaryDirectory() as temp:
            board = Path(temp) / "board.png"
            self._board(board)
            annotation = self._annotation()
            plan = build_animation_plan(
                project,
                {"scene-01": annotation},
                words,
                board_images={"scene-01": board},
                style_config={"id": "warm-pencil", "gesture_colors": {"contour-indicate": "#4E7598"}},
            )
            qa = _animation_plan_checks_v33(Path(temp), project, {}, plan, {"scene-01": annotation}, "small-hand")
            self.assertTrue(qa["ok"], qa["errors"])
        self.assertEqual(plan["scenes"][0]["events"], [])
        self.assertTrue(plan["scenes"][0]["skipped"])
        self.assertEqual(validate_animation_plan(plan, {"scene-01": annotation}), [])

    def test_default_plan_keeps_ordinary_elements_stable_and_records_skip(self) -> None:
        annotation = self._annotation()
        annotation["elements"][0]["label"] = "普通关系线"
        annotation["elements"][0]["role"] = "关系"
        project = {"version": 3, "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 2500, "composition": "causal-chain"}]}
        plan = build_animation_plan(
            project,
            {"scene-01": annotation},
            [{"text": "普通关系线", "start_ms": 0, "end_ms": 500}],
        )
        self.assertEqual(plan["scenes"][0]["events"], [])
        self.assertTrue(plan["scenes"][0]["skipped"])
        self.assertEqual(plan["scenes"][0]["skipped"][0]["fallback"]["to"], "skip")
        self.assertNotIn("focusTarget", plan["scenes"][0]["skipped"][0])

    def test_only_explicit_focus_intent_can_add_a_post_effect(self) -> None:
        cases = [
            ("关系说明", "关系", None, None),
            ("风险提醒", "警告", None, None),
            ("聚焦核心", "聚焦", "focus-push", "focus"),
        ]
        project = {
            "version": 3,
            "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 2500, "composition": "center-spoke"}],
        }
        for label, role, expected_effect, expected_intent in cases:
            with self.subTest(effect=expected_effect), tempfile.TemporaryDirectory() as temp:
                board = Path(temp) / "board.png"
                self._board(board)
                annotation = self._annotation()
                annotation["elements"][0]["label"] = label
                annotation["elements"][0]["role"] = role
                plan = build_animation_plan(
                    project,
                    {"scene-01": annotation},
                    [{"text": label, "start_ms": 0, "end_ms": 2400}],
                    board_images={"scene-01": board},
                )
                events = plan["scenes"][0]["events"]
                if expected_effect is None:
                    self.assertEqual(events, [])
                    self.assertTrue(plan["scenes"][0]["skipped"])
                else:
                    event = events[0]
                    self.assertEqual(event["effect"], expected_effect)
                    self.assertEqual(event["semanticIntent"], expected_intent)
                    self.assertEqual(event["visualMapping"], "annotation-semantic-evidence")
                    self.assertFalse(event["handBehavior"]["showHandForEffect"])
                self.assertEqual(validate_animation_plan(plan, {"scene-01": annotation}), [])

    def test_short_window_is_skipped_without_time_stretch(self) -> None:
        annotation = self._annotation(duration=900)
        annotation["elements"][0]["reveal"] = {"startMs": 0, "durationMs": 100}
        project = {"version": 3, "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 900, "composition": "focus"}]}
        plan = build_animation_plan(project, {"scene-01": annotation}, [{"text": "人物身份", "start_ms": 0, "end_ms": 100}])
        self.assertEqual(plan["scenes"][0]["events"], [])
        self.assertTrue(plan["skipped"])

    def test_removed_pixel_effect_does_not_shorten_or_overlap_draws(self) -> None:
        annotation = self._annotation(duration=2600)
        annotation["elements"] = [
            {
                "id": "conclusion",
                "sequence": 1,
                "label": "核心结论",
                "role": "结论",
                "region": {"x": 20, "y": 20, "width": 75, "height": 100},
                "reveal": {"startMs": 0, "durationMs": 700},
            },
            {
                "id": "detail",
                "sequence": 2,
                "label": "补充细节",
                "role": "说明",
                "region": {"x": 105, "y": 20, "width": 75, "height": 100},
                "reveal": {"startMs": 700, "durationMs": 900},
            },
        ]
        original_durations = [item["reveal"]["durationMs"] for item in annotation["elements"]]
        project = {
            "version": 3,
            "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 2600, "composition": "comparison"}],
        }
        words = [{"text": "核心结论补充细节", "start_ms": 0, "end_ms": 2450}]
        with tempfile.TemporaryDirectory() as temp:
            board = Path(temp) / "board.png"
            self._board(board)
            reservations = reserve_animation_windows(
                project,
                {"scene-01": annotation},
                words,
                board_images={"scene-01": board},
            )
            plan = build_animation_plan(
                project,
                {"scene-01": annotation},
                words,
                board_images={"scene-01": board},
            )
        self.assertEqual(reservations, [])
        self.assertEqual(
            [item["reveal"]["durationMs"] for item in annotation["elements"]],
            original_durations,
        )
        self.assertEqual(plan["scenes"][0]["events"], [])
        self.assertTrue(plan["scenes"][0]["skipped"])
        self.assertEqual(validate_animation_plan(plan, {"scene-01": annotation}), [])

    def test_removed_local_effects_do_not_consume_three_scene_narration_windows(self) -> None:
        for role, label in (("结论", "核心结论"), ("警告", "风险提醒")):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                project = {"version": 3, "scenes": []}
                annotations = {}
                boards = {}
                words = []
                original_scene_durations = []
                for index in range(3):
                    scene_start = index * 3500
                    scene_duration = 3500 if index < 2 else 3000
                    scene_end = scene_start + scene_duration
                    scene_id = f"scene-{index + 1:02d}"
                    project["scenes"].append({
                        "id": scene_id,
                        "start_ms": scene_start,
                        "end_ms": scene_end,
                        "composition": "center-spoke",
                    })
                    annotation = {
                        "sceneId": scene_id,
                        "canvas": {"width": 200, "height": 180},
                        "sceneDurationMs": scene_duration,
                        "drawingPlan": {"minimumColorMs": 700},
                        "elements": [{
                            "id": f"target-{index}",
                            "sequence": 1,
                            "label": label,
                            "role": role,
                            "region": {"x": 20, "y": 20, "width": 150, "height": 100},
                            "reveal": {"startMs": 100, "durationMs": 1900},
                        }],
                    }
                    annotations[scene_id] = annotation
                    original_scene_durations.append(annotation["sceneDurationMs"])
                    board = root / f"{scene_id}.png"
                    self._board(board)
                    boards[scene_id] = board
                    words.append({
                        "text": label,
                        "start_ms": scene_start,
                        "end_ms": scene_start + 2900,
                    })

                reservations = reserve_animation_windows(
                    project,
                    annotations,
                    words,
                    board_images=boards,
                )
                plan = build_animation_plan(
                    project,
                    annotations,
                    words,
                    board_images=boards,
                )
                plan["timingReservations"] = reservations
                events = [event for scene in plan["scenes"] for event in scene["events"]]
                self.assertEqual(events, [])
                self.assertEqual(reservations, [])
                self.assertEqual(
                    [annotations[scene["id"]]["sceneDurationMs"] for scene in project["scenes"]],
                    original_scene_durations,
                )
                self.assertEqual(validate_animation_plan(plan, annotations), [])
                for transition in plan.get("transitions", []):
                    active_ms = int(transition["cleanCanvasEndMs"]) - int(transition["eraseStartMs"])
                    self.assertLessEqual(active_ms, 500)

    def test_legacy_v31_gesture_plan_remains_validatable(self) -> None:
        legacy = {
            "planVersion": "3.1",
            "scenes": [{
                "sceneId": "scene-01",
                "sceneDurationMs": 1500,
                "events": [{
                    "effect": "gesture-arrow",
                    "targetElementId": "subject",
                    "startMs": 0,
                    "endMs": 1000,
                    "persistUntilMs": 1300,
                    "timeBudgetMs": 1000,
                    "gesturePath": [[10, 20], [100, 20]],
                    "focusTarget": {"source": "annotation-region-fallback"},
                    "phase": {
                        "prepare": {"startMs": 0, "endMs": 100},
                        "draw": {"startMs": 100, "endMs": 600},
                        "hold": {"startMs": 600, "endMs": 920},
                        "leave": {"startMs": 920, "endMs": 1000},
                    },
                    "style": {"colorSource": "style-registry:warm-pencil.gesture_colors"},
                }],
            }],
        }
        self.assertEqual(validate_animation_plan(legacy), [])


if __name__ == "__main__":
    unittest.main()
