from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "visual_director.py"
SPEC = importlib.util.spec_from_file_location("visual_director", MODULE_PATH)
visual_director = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(visual_director)

ANIMATION_PATH = Path(__file__).parents[1] / "scripts" / "animation_plan.py"
ANIMATION_SPEC = importlib.util.spec_from_file_location("animation_plan", ANIMATION_PATH)
animation_plan = importlib.util.module_from_spec(ANIMATION_SPEC)
assert ANIMATION_SPEC.loader
ANIMATION_SPEC.loader.exec_module(animation_plan)


def words_for_scene(start: int, text: list[str], duration: int = 800) -> list[dict[str, int | str]]:
    result = []
    current = start
    for item in text:
        result.append({"text": item, "start_ms": current, "end_ms": current + duration})
        current += duration
    return result


class VisualDirectorTests(unittest.TestCase):
    def test_layout_allocates_independent_objects_without_changing_ids(self):
        for count in range(1, 7):
            elements = [{"id": f"id-{i}", "label": f"单元{i}"} for i in range(count)]
            layout = visual_director._native_layout_plan({"shot_id": "s"}, {"elements": elements}, {})
            self.assertEqual([r["element_ids"] for r in layout["native_regions"]], [[e["id"]] for e in elements])
            boxes = [r["box"] for r in layout["native_regions"]]
            for i, (x, y, w, h) in enumerate(boxes):
                self.assertLessEqual(y + h, 0.84)
                for bx, by, bw, bh in boxes[i + 1:]:
                    self.assertTrue(x + w <= bx or bx + bw <= x or y + h <= by or by + bh <= y)

    def test_layout_rejects_ambiguous_custom_allocation_before_generation(self):
        scene = {"elements": [{"id": "a"}, {"id": "b"}]}
        valid = [
            {"element_ids": ["a"], "box": [0.05, 0.07, 0.4, 0.7]},
            {"element_ids": ["b"], "box": [0.55, 0.07, 0.4, 0.7]},
        ]
        bad_cases = []
        for ids in (["a"], ["unknown"], ["a", "b"]):
            regions = copy.deepcopy(valid)
            regions[1]["element_ids"] = ids
            bad_cases.append(regions)
        regions = copy.deepcopy(valid)
        regions[1]["box"][0] = 0.2
        bad_cases.append(regions)
        for regions in bad_cases:
            with self.assertRaises(visual_director.VisualPlanError):
                visual_director._native_layout_plan({"shot_id": "s"}, scene, {"layout": {"native_regions": regions}})
        result = visual_director._native_layout_plan({"shot_id": "s"}, scene, {"layout": {"native_regions": valid}})
        self.assertEqual(result["template"], "custom")

    def test_layout_requires_semantic_ids_and_matching_preset(self):
        for scene, layout in [
            ({"elements": [{"label": "missing"}]}, {}),
            ({"elements": [{"id": "a"}, {"id": "a"}]}, {}),
            ({"elements": [{"id": "a"}]}, {"template": "hero-side"}),
        ]:
            with self.assertRaises(visual_director.VisualPlanError):
                visual_director._native_layout_plan({"shot_id": "s"}, scene, {"layout": layout})

    def test_paper_metaphor_reuses_existing_template_and_composition_routes(self) -> None:
        words = words_for_scene(0, ["选择", "需要", "权衡"], 900)
        project = {
            "version": 3,
            "style_id": "paper-metaphor-collage",
            "scenes": [{
                "id": "scene-01",
                "title": "两种选择的权衡",
                "narration": "选择需要权衡",
                "start_ms": 0,
                "end_ms": 2_700,
                "elements": [{"id":"a", "label":"选择一", "trigger_text":"选择"}, {"id":"b", "label":"选择二", "trigger_text":"权衡"}],
            }],
        }
        plan = visual_director.build_visual_plan(project, words)
        self.assertEqual(plan["shots"][0]["template"], "comparison")
        self.assertEqual(plan["shots"][0]["composition"], "before-after")
        self.assertEqual(plan["source"]["style_id"], "paper-metaphor-collage")

    def test_basic_plan_uses_real_word_boundaries_and_renders_markdown(self) -> None:
        words = words_for_scene(0, ["因为", "需求", "增加"], 900)
        words += words_for_scene(4_000, ["所以", "价格", "上涨"], 900)
        project = {
            "version": 3,
            "scenes": [
                {
                    "id": "scene-01",
                    "title": "原因",
                    "narration": "因为需求增加",
                    "start_ms": 0,
                    "end_ms": 2_700,
                    "composition": "causal-chain",
                    "title_card": {"text": "需求为何增加", "position": "top-left", "style": "outlined-label-v1", "accent": "#356AE6"},
                    "elements": [{"id":"a", "label":"需求", "trigger_text":"因为"}, {"id":"b", "label":"变化", "trigger_text":"增加"}],
                },
                {
                    "id": "scene-02",
                    "title": "结果",
                    "narration": "所以价格上涨",
                    "start_ms": 4_000,
                    "end_ms": 6_700,
                    "composition": "before-after",
                    "elements": [{"id":"a", "label":"价格", "trigger_text":"所以"}, {"id":"b", "label":"结果", "trigger_text":"上涨"}],
                },
            ],
        }
        plan = visual_director.build_visual_plan(project, words)
        first, second = plan["shots"]
        self.assertEqual(first["start_ms"], words[0]["start_ms"])
        self.assertEqual(first["end_ms"], words[2]["end_ms"])
        self.assertEqual(first["start_phrase_id"], "w-0001")
        self.assertEqual(first["beats"][0]["start_ms"], words[0]["start_ms"])
        self.assertEqual(second["start_ms"], words[3]["start_ms"])
        self.assertEqual(plan["summary"]["shot_count"], 2)
        self.assertIn("cause", plan["summary"]["template_counts"])
        self.assertEqual(first["expression_mode"], "native")
        self.assertEqual(first["title_card"]["text"], "需求为何增加")
        self.assertEqual(plan["sections"][0]["title_card"], first["title_card"])
        self.assertEqual(
            set(plan["source"]),
            {"project_version", "style_id", "word_count", "mapping"},
        )
        self.assertTrue(first["layout_plan"]["native_regions"])
        self.assertEqual(
            set(first["layout_plan"]),
            {
                "planned_before_board", "coordinate_space", "caption_region",
                "native_regions", "semantic_ownership", "planning_sequence",
                "board_generation_contract", "template",
            },
        )
        markdown = visual_director.render_visual_plan_markdown(plan)
        self.assertIn("中文视觉编排表", markdown)
        self.assertIn("A-roll", markdown)
        self.assertNotIn("动态覆盖层", markdown)
        visual_director.validate_visual_plan(plan, words)

    def test_coverage_rejects_missing_or_repeated_word(self) -> None:
        words = words_for_scene(0, ["一", "二", "三"], 900)
        project = {
            "version": 3,
            "scenes": [{
                "id": "scene-01", "narration": "一二三", "start_ms": 0, "end_ms": 2_700,
                "elements": [{"id":"a", "label":"一", "trigger_text":"一"}, {"id":"b", "label":"二", "trigger_text":"三"}],
            }],
        }
        plan = visual_director.build_visual_plan(project, words)
        plan["shots"][0]["word_index_end"] = 1
        with self.assertRaisesRegex(visual_director.VisualPlanError, "word_ids"):
            visual_director.validate_visual_plan(plan, words)

    def test_long_shot_requires_internal_beat(self) -> None:
        words = words_for_scene(0, ["长", "镜头", "需要", "真实", "节奏", "变化"], 2_500)
        project = {
            "version": 3,
            "scenes": [{
                "id": "scene-01", "narration": "长镜头需要真实节奏变化", "start_ms": 0, "end_ms": 15_000,
                "elements": [{"id":"focus", "label":"核心", "trigger_text":"真实"}],
            }],
        }
        plan = visual_director.build_visual_plan(project, words)
        self.assertTrue(any(0 < beat["start_ms"] < plan["shots"][0]["end_ms"] for beat in plan["shots"][0]["beats"]))
        broken = copy.deepcopy(plan)
        broken["shots"][0]["beats"] = []
        with self.assertRaisesRegex(visual_director.VisualPlanError, "内部视觉 beat"):
            visual_director.validate_visual_plan(broken, words)

    def test_three_identical_template_and_composition_are_rejected(self) -> None:
        words = []
        scenes = []
        for index in range(3):
            start = index * 3_000
            words += words_for_scene(start, [f"第{index}", "段", "话"], 900)
            scenes.append({
                "id": f"scene-{index + 1:02d}", "narration": "说明", "start_ms": start, "end_ms": start + 2_700,
                "template": "focus", "composition": "center-spoke", "elements": [{"id":"focus", "label":"核心", "trigger_text":f"第{index}"}],
            })
        with self.assertRaisesRegex(visual_director.VisualPlanError, "连续三个"):
            visual_director.build_visual_plan({"version": 3, "scenes": scenes}, words)

    def test_a_roll_without_presenter_downgrades_deterministically(self) -> None:
        words = words_for_scene(0, ["请", "看", "这里"], 900)
        project = {
            "version": 3,
            "scenes": [{
                "id": "scene-01", "narration": "请看这里", "start_ms": 0, "end_ms": 2_700,
                "shot_type": "a_host", "actor_slot": "presenter-main", "emotion": "curious", "action": "point",
                "elements": [{"id":"focus", "label":"重点", "trigger_text":"请"}],
            }],
        }
        with tempfile.TemporaryDirectory() as temp:
            plan = visual_director.build_visual_plan(project, words, temp)
        shot = plan["shots"][0]
        self.assertEqual(shot["requested_shot_type"], "a_host")
        self.assertEqual(shot["shot_type"], "b_text")
        self.assertEqual(shot["mode"], "B-roll")
        self.assertEqual(shot["fallback"]["reason"], "missing-presenter-assets")
        self.assertIn("presenter:presenter-main", shot["required_assets"])
        self.assertEqual(shot["actor_slot"], "presenter-main")

    def test_missing_screenshot_is_required_or_fallback_not_fake_ui(self) -> None:
        words = words_for_scene(0, ["录屏", "暂时", "缺失"], 900)
        project = {
            "version": 3,
            "scenes": [{
                "id": "scene-01", "narration": "录屏暂时缺失", "start_ms": 0, "end_ms": 2_700,
                "shot_type": "b_screenshot", "elements": [{"id":"screen", "label":"待补素材", "trigger_text":"录屏"}],
            }],
        }
        plan = visual_director.build_visual_plan(project, words)
        shot = plan["shots"][0]
        self.assertEqual(shot["shot_type"], "b_whiteboard")
        self.assertEqual(shot["fallback"]["reason"], "missing-screenshot-asset")
        self.assertIn("screenshot:scene-01", shot["required_assets"])

    def test_visual_beats_are_upstream_constraints_for_animation_plan(self) -> None:
        words = words_for_scene(0, ["先", "画", "重点"], 900)
        project = {
            "version": 3,
            "scenes": [{
                "id": "scene-01", "narration": "先画重点", "start_ms": 0, "end_ms": 2_700,
                "composition": "center-spoke", "elements": [{"id":"focus", "label":"重点", "trigger_text":"先"}],
            }],
        }
        visual_plan = visual_director.build_visual_plan(project, words)
        annotations = {"scene-01": {
            "canvas": {"width": 100, "height": 100},
            "sceneDurationMs": 2_700,
            "elements": [{
                "id": "focus", "sequence": 1,
                "region": {"x": 0, "y": 0, "width": 80, "height": 80},
                "reveal": {"startMs": 0, "durationMs": 800},
            }],
        }}
        with tempfile.TemporaryDirectory() as temp:
            board = Image.new("RGB", (100, 100), (245, 235, 215))
            ImageDraw.Draw(board).rectangle((20, 20, 60, 60), fill=(55, 80, 100))
            board_path = Path(temp) / "scene-01.png"
            board.save(board_path)
            animation = animation_plan.build_animation_plan(
                project,
                annotations,
                words,
                visual_plan=visual_plan,
                board_images={"scene-01": board_path},
            )
        events = animation["scenes"][0]["events"]
        self.assertEqual(events, [])
        self.assertEqual(animation["source"]["visualPlanVersion"], 1)
        beat = visual_plan["shots"][0]["beats"][0]
        self.assertEqual(beat["motion_role"], "conclusion")
        self.assertIsNone(beat["preferred_effect"])
        self.assertFalse(beat["animation_candidate"])
        self.assertEqual(beat["fallback"], "stable-hold")


if __name__ == "__main__":
    unittest.main()
