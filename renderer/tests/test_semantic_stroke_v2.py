import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import stream_render  # noqa: E402
import render_stream_whiteboard  # noqa: E402


class SemanticStrokePlannerTests(unittest.TestCase):
    def test_progressive_phase_budget_reserves_visible_color_sweeps(self):
        phases = render_stream_whiteboard._progressive_phase_frames(60, 0.26, True)
        self.assertEqual(sum(phases.values()), 60)
        self.assertGreater(phases["recognition"], 0)
        self.assertGreater(phases["base_color"], 0)
        self.assertGreater(phases["texture"], 0)
        self.assertGreaterEqual(phases["finalize"], 3)
        self.assertLessEqual(phases["finalize"], 6)
        self.assertGreaterEqual(phases["base_color"], 8)
        self.assertGreaterEqual(phases["finalize"], 2)

    def test_shorter_window_only_spends_optional_texture_frames(self):
        original = render_stream_whiteboard._progressive_phase_frames(90, 0.26, True)
        compressed = render_stream_whiteboard._progressive_phase_frames(
            76,
            0.26,
            True,
            original_total_frames=90,
        )
        self.assertEqual(sum(compressed.values()), 76)
        self.assertEqual(compressed["recognition"], original["recognition"])
        self.assertEqual(compressed["base_color"], original["base_color"])
        self.assertEqual(compressed["finalize"], original["finalize"])
        self.assertEqual(compressed["texture"], 0)
        with self.assertRaisesRegex(ValueError, "侵占识别或基础色"):
            render_stream_whiteboard._progressive_phase_frames(
                72,
                0.26,
                True,
                original_total_frames=90,
            )

    def test_continuous_motion_caps_every_frame_and_completes_feasible_strokes(self):
        samples = [(10, 10), (130, 10), (160, 40), (160, 150)]
        frames, metrics = render_stream_whiteboard._plan_continuous_motion(
            samples,
            90,
            {2},
            fps=30,
            short_edge=540,
        )
        self.assertTrue(metrics["completed"])
        self.assertEqual(len(frames), 90)
        maximum = max(
            np.hypot(b.x - a.x, b.y - a.y) / 540
            for a, b in zip(frames, frames[1:])
        )
        self.assertLessEqual(
            maximum,
            render_stream_whiteboard.HAND_TRAVEL_MAX_STEP_SHORT_EDGE_RATIO + 1e-6,
        )
        self.assertTrue(any(frame.state == "travel" for frame in frames))

    def test_infeasible_motion_holds_instead_of_compressing_the_path(self):
        frames, metrics = render_stream_whiteboard._plan_continuous_motion(
            [(0, 0), (1000, 0)],
            10,
            set(),
            fps=30,
            short_edge=540,
        )
        self.assertFalse(metrics["completed"])
        self.assertGreater(metrics["hold_frames"], 0)
        maximum = max(
            np.hypot(b.x - a.x, b.y - a.y) / 540
            for a, b in zip(frames, frames[1:])
        )
        self.assertLessEqual(
            maximum,
            render_stream_whiteboard.HAND_DRAW_MAX_STEP_SHORT_EDGE_RATIO + 1e-6,
        )

    def test_frame_coordinate_qa_reports_only_compact_outliers(self):
        tracker = render_stream_whiteboard.HandMotionQATracker(30, 1000)
        tracker.add(0, {"visible": True, "opacity": 1.0, "x": 0, "y": 0, "state": "draw"})
        tracker.add(1, {"visible": True, "opacity": 1.0, "x": 4, "y": 0, "state": "draw"})
        tracker.add(2, {"visible": True, "opacity": 1.0, "x": 40, "y": 0, "state": "draw"})
        summary = tracker.summary()
        self.assertEqual(summary["status"], "failed")
        self.assertGreater(summary["violation_count"], 0)
        self.assertLessEqual(len(summary["worst_events"]), 5)
        self.assertNotIn("trace", summary)

    def test_empty_phase_keeps_last_hand_visible_and_stationary(self):
        renderer = object.__new__(render_stream_whiteboard.RegionStreamRenderer)
        renderer.drawn = np.zeros((8, 8, 3), dtype=np.float32)
        renderer._last_hand_position = (17, 23)
        captured = []

        def capture(_writer, x, y, **kwargs):
            captured.append((x, y, kwargs))

        renderer._write_tip_frame = capture
        renderer._write_idle_hand_frames(object(), 3)

        self.assertEqual(len(captured), 3)
        self.assertTrue(all((x, y) == (17, 23) for x, y, _ in captured))
        self.assertTrue(all(item[2]["state"] == "hold" for item in captured))
        self.assertTrue(all(item[2]["pen_down"] is False for item in captured))

    def test_fast_trace_schedule_reaches_every_semantic_stroke_in_order(self):
        schedule = render_stream_whiteboard._fast_trace_schedule(
            18,
            {4, 9, 13},
            12,
        )
        self.assertEqual(len(schedule), 12)
        self.assertEqual(schedule, sorted(schedule))
        self.assertEqual(schedule[-1], 17)
        for start, end in ((0, 4), (4, 9), (9, 13), (13, 18)):
            self.assertTrue(any(start <= index < end for index in schedule))

    def test_dense_fast_trace_never_defers_a_stroke_to_image_reveal(self):
        schedule = render_stream_whiteboard._fast_trace_schedule(
            160,
            set(range(2, 160, 2)),
            80,
        )
        self.assertEqual(len(schedule), 80)
        self.assertEqual(schedule[-1], 159)
        self.assertEqual(len(set(schedule)), 80)

    def test_flat_color_uses_four_visible_sweeps_and_finishes_exactly(self):
        renderer = object.__new__(render_stream_whiteboard.RegionStreamRenderer)
        renderer.out_w = 80
        renderer.out_h = 40
        renderer.cfg = SimpleNamespace(fps=30)
        renderer.foreground_pixels = np.ones((40, 80), dtype=bool)
        renderer.ink_pixels = np.zeros((40, 80), dtype=bool)
        renderer.color_img = np.full((40, 80, 3), (40, 120, 220), dtype=np.uint8)
        renderer.drawn = np.full((40, 80, 3), 245.0, dtype=np.float32)
        renderer.motion_plans = []
        renderer.color_metrics = {"passes": 0, "color_sweeps": 0}
        renderer._last_hand_position = (10, 10)
        positions = []

        class Writer:
            def __init__(self):
                self.frames = []

            def write(self, frame):
                self.frames.append(frame.copy())

        writer = Writer()

        def write_tip(target_writer, x, y, **kwargs):
            positions.append((round(x), round(y), kwargs.get("pen_down")))
            renderer._last_hand_position = (round(x), round(y))
            target_writer.write(renderer.drawn.astype(np.uint8))

        renderer._write_tip_frame = write_tip
        sweeps = renderer._wash_flat_color_sweeps(
            writer,
            20,
            np.ones((40, 80), dtype=bool),
        )

        coloured_counts = [
            int(np.count_nonzero(np.all(frame == renderer.color_img, axis=2)))
            for frame in writer.frames
        ]
        self.assertEqual(sweeps, 4)
        self.assertEqual(len(writer.frames), 20)
        self.assertEqual(len(positions), 20)
        self.assertGreaterEqual(len(set(coloured_counts)), 8)
        self.assertEqual(coloured_counts, sorted(coloured_counts))
        self.assertEqual(coloured_counts[-1], 40 * 80)
        self.assertEqual(renderer.motion_plans[-1]["strategy"], "serpentine-flat-fill-v1")

    def test_pen_lift_travel_is_visible_and_never_draws_a_false_connector(self):
        samples = [(10, 10), (20, 10), (140, 90), (150, 90)]
        expanded, lifts, scales = render_stream_whiteboard._expand_pen_lift_travel(
            samples,
            {2},
            max_step_px=24,
            effort_scales=[0.5, 0.5, 1.5, 1.5],
        )
        self.assertEqual(expanded[0], samples[0])
        self.assertEqual(expanded[-1], samples[-1])
        self.assertIsNotNone(scales)
        self.assertGreater(len(expanded), len(samples))
        self.assertTrue(all(
            np.hypot(b[0] - a[0], b[1] - a[1]) <= 24.1
            for a, b in zip(expanded, expanded[1:])
        ))
        travel_start = expanded.index(samples[1]) + 1
        travel_end = expanded.index(samples[2])
        self.assertTrue(all(index in lifts for index in range(travel_start, travel_end + 1)))

    def test_hand_opacity_envelopes_are_short_and_monotonic(self):
        fade_in = render_stream_whiteboard._fade_opacities(12, 30, 120, fade_in=True)
        fade_out = render_stream_whiteboard._fade_opacities(12, 30, 180, fade_in=False)
        self.assertEqual(len(fade_in), 12)
        self.assertEqual(len(fade_out), 12)
        self.assertTrue(all(a <= b for a, b in zip(fade_in, fade_in[1:])))
        self.assertTrue(all(a >= b for a, b in zip(fade_out, fade_out[1:])))
        self.assertEqual(fade_in[-1], 1.0)
        self.assertEqual(fade_out[-1], 0.0)

    def test_layered_paths_keep_identity_details_with_their_component(self):
        renderer = object.__new__(render_stream_whiteboard.RegionStreamRenderer)
        renderer.stroke_planner = "semantic-v2"
        renderer.stroke_metrics = []
        renderer._extract_region_skeleton_strokes = lambda allowed: [[(0, 0), (1, 1)]]
        renderer._semantic_plan = lambda strokes, allowed: [
            {"points": [(10, 10), (20, 10)], "component": 1, "role": "outline"},
            {"points": [(12, 12), (18, 12)], "component": 1, "role": "detail"},
            {"points": [(60, 60), (70, 60)], "component": 2, "role": "outline"},
            {"points": [(62, 62), (68, 62)], "component": 2, "role": "detail"},
            {"points": [(64, 64), (66, 64)], "component": 2, "role": "texture"},
        ]
        allowed = np.ones((100, 100), dtype=bool)
        (recognition, lifts), (_detail, _), (texture, _), _centers = renderer._layered_paths(allowed)
        self.assertEqual(recognition, [
            (10, 10), (20, 10),
            (12, 12), (18, 12),
            (60, 60), (70, 60),
            (62, 62), (68, 62),
        ])
        self.assertEqual(lifts, {2, 4, 6})
        self.assertEqual(texture, [(64, 64), (66, 64)])

    def _masks(self):
        allowed = np.ones((180, 240), dtype=bool)
        ink = np.zeros_like(allowed)
        # A small decoration in the top-left and a larger semantic subject near centre.
        ink[12:14, 12:45] = True
        ink[70:73, 82:190] = True
        ink[85:88, 90:182] = True
        ink[72:130, 130:133] = True
        return ink, allowed

    def test_primary_subject_is_planned_before_top_left_decoration(self):
        ink, allowed = self._masks()
        strokes = [
            [(12.0, 12.0), (44.0, 12.0)],
            [(82.0, 71.0), (190.0, 71.0)],
            [(91.0, 86.0), (181.0, 86.0)],
            [(131.0, 72.0), (131.0, 129.0)],
        ]
        plan = stream_render._semantic_stroke_plan(strokes, ink, allowed)
        self.assertGreater(plan[0]["points"][0][0], 70.0)
        self.assertEqual(plan[0]["componentRank"], 0)
        self.assertGreater(max(item["componentRank"] for item in plan), 0)

    def test_each_component_finishes_outline_then_detail_then_texture(self):
        ink = np.zeros((160, 200), dtype=bool)
        allowed = np.ones_like(ink)
        ink[50:53, 45:160] = True
        ink[75:78, 55:145] = True
        ink[90:92, 80:90] = True
        ink[51:92, 84:87] = True
        strokes = [
            [(45.0, 51.0), (160.0, 51.0)],
            [(55.0, 76.0), (145.0, 76.0)],
            [(80.0, 91.0), (87.0, 91.0)],
        ]
        plan = stream_render._semantic_stroke_plan(strokes, ink, allowed)
        roles = [item["role"] for item in plan]
        self.assertEqual(roles[0], "outline")
        self.assertEqual(roles[-1], "texture")
        self.assertLessEqual(roles.index("outline"), roles.index("detail"))

    def test_closed_identity_feature_precedes_supporting_structure(self):
        ink = np.zeros((180, 220), dtype=bool)
        allowed = np.ones_like(ink)
        # One visual object: long silhouette, central closed face/tool mark,
        # then a long supporting limb/connector.
        strokes = [
            [(40.0, 45.0), (175.0, 45.0)],
            [
                (94.0, 75.0), (104.0, 68.0), (116.0, 70.0),
                (122.0, 80.0), (116.0, 91.0), (103.0, 92.0),
                (94.0, 84.0), (94.0, 75.0),
            ],
            [(55.0, 125.0), (165.0, 125.0)],
        ]
        for stroke in strokes:
            points = np.asarray(stroke, dtype=np.int32)
            cv2.polylines(ink.view(np.uint8), [points], False, 1, thickness=3)
        # Join the synthetic strokes into one semantic component.
        cv2.line(ink.view(np.uint8), (109, 45), (109, 125), 1, thickness=3)
        plan = stream_render._semantic_stroke_plan(strokes, ink, allowed)
        stages = [item.get("semanticStage") for item in plan]
        self.assertEqual(stages[0], "anchor")
        self.assertIn("identity", stages)
        self.assertIn("support", stages)
        self.assertLess(stages.index("identity"), stages.index("support"))

    def test_plan_is_deterministic_and_not_reading_band_order(self):
        ink, allowed = self._masks()
        strokes = [
            [(12.0, 12.0), (44.0, 12.0)],
            [(82.0, 71.0), (190.0, 71.0)],
            [(91.0, 86.0), (181.0, 86.0)],
            [(131.0, 72.0), (131.0, 129.0)],
        ]
        first = stream_render._semantic_stroke_plan(strokes, ink, allowed)
        second = stream_render._semantic_stroke_plan(strokes, ink, allowed)
        self.assertEqual(first, second)
        legacy = stream_render._order_skeleton_strokes(strokes)
        self.assertNotEqual(first[0]["points"], legacy[0])

    def test_dark_board_uses_bright_edge_extraction_without_replacing_planner(self):
        dark = np.full((180, 320, 3), (56, 62, 23), dtype=np.uint8)
        cv2.circle(dark, (160, 90), 48, (220, 238, 241), thickness=5)
        cfg = stream_render.Config(ink_threshold=10)
        threshold, ink_pixels, ink_paint, dark_mode, background = (
            render_stream_whiteboard._prepare_style_ink_maps(dark, cfg)
        )
        self.assertTrue(dark_mode)
        self.assertGreater(np.count_nonzero(ink_pixels), 0)
        self.assertTrue(np.array_equal(ink_paint, dark.astype(np.float32)))
        self.assertLess(float(np.mean(background)), 96.0)
        self.assertEqual(threshold.shape, dark.shape[:2])

        light = np.full((180, 320, 3), (239, 244, 246), dtype=np.uint8)
        cv2.line(light, (40, 90), (280, 90), (20, 20, 20), thickness=5)
        _, _, _, light_dark_mode, _ = render_stream_whiteboard._prepare_style_ink_maps(light, cfg)
        self.assertFalse(light_dark_mode)


if __name__ == "__main__":
    unittest.main()
