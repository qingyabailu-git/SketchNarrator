import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "renderer" / "scripts")]
from phase_budget import phase_frames
from animation_plan import semantic_effect_for

class RecoveryContractTests(unittest.TestCase):
    def test_optional_effect_cannot_remove_hand_release(self):
        original = phase_frames(246, fps=30)
        revised = phase_frames(218, 246, fps=30)
        self.assertGreaterEqual(revised["finalize"], 5)
        self.assertEqual(original["recognition"], revised["recognition"])
        self.assertEqual(original["base_color"], revised["base_color"])
        with self.assertRaises(ValueError):
            phase_frames(190, 246, fps=30)

    def test_relations_and_conclusions_do_not_add_local_effects(self):
        for label in ("结论", "警告", "箭头"):
            self.assertEqual(semantic_effect_for({}, {"label": label}, None)[0], "skip")

    def test_all_scene_diagnostics_name_real_sources(self):
        import json
        import tempfile
        from unittest.mock import patch
        from PIL import Image, ImageDraw
        import workflow
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = {"scenes": [{"id": "scene-01"}, {"id": "scene-09"}]}
            state = {"boards": {}}
            for sid in ("scene-01", "scene-09"):
                image = Image.new("RGB", (64, 64), "white")
                ImageDraw.Draw(image).rectangle((20, 20, 40, 40), fill="black")
                image.save(root / (sid + ".png"))
                annotation = {"canvas": {"width": 64, "height": 64}, "elements": [
                    {"id": "object-a", "region": {"x": 0, "y": 0, "width": 30, "height": 64}, "reveal": {}}
                ]}
                (root / (sid + ".json")).write_text(json.dumps(annotation), encoding="utf-8")
                state["boards"][sid] = {"image": sid + ".png", "annotation": sid + ".json"}
            with patch.object(workflow, "load_project", return_value=(project, state)):
                report = workflow.check_boards(root)
            self.assertTrue(report["ok"])
            self.assertEqual([r["scene_id"] for r in report["scenes"]], ["scene-01", "scene-09"])
            self.assertTrue(all(r["warnings"] and not r["errors"] and r.get("diagnostic_image") for r in report["scenes"]))
            self.assertFalse((root / "animation-plan.json").exists())

    def test_selection_is_stable_and_compiled_masks_replay_exactly(self):
        import copy
        import numpy as np
        from pixel_contract import require_ownership, compile_masks
        rgb = np.full((20, 20, 3), 255, dtype=np.uint8)
        rgb[5:15, 5:15] = 0
        ann = {"canvas": {"width": 20, "height": 20}, "elements": [
            {"id": "large", "sequence": 1, "region": {"x": 4, "y": 4, "width": 10, "height": 12}},
            {"id": "small", "sequence": 2, "region": {"x": 6, "y": 6, "width": 4, "height": 4}}]}
        owner, _, report = require_ownership(rgb, ann)
        self.assertEqual(owner[7, 7], 2)
        self.assertEqual(owner[14, 14], 0)  # unselected pixels stay unselected
        self.assertTrue(report["warnings"])
        masks = compile_masks(rgb, ann)
        reordered = copy.deepcopy(ann)
        reordered["elements"].reverse()
        self.assertEqual(masks, compile_masks(rgb, reordered))
        for item in ann["elements"]:
            item["pixelMask"] = masks[item["id"]]
        replay, _, _ = require_ownership(rgb, ann)
        np.testing.assert_array_equal(owner, replay)

    def test_corrupt_mask_is_an_execution_error_not_a_quality_warning(self):
        import numpy as np
        from pixel_contract import require_ownership
        ann = {"canvas": {"width": 10, "height": 10}, "elements": [
            {"id": "a", "region": {"x": 0, "y": 0, "width": 10, "height": 10},
             "pixelMask": {"size": [10, 10], "runs": [[100, 0, 1]]}}]}
        with self.assertRaises(ValueError):
            require_ownership(np.full((10, 10, 3), 255, dtype=np.uint8), ann)


if __name__ == "__main__":
    unittest.main()
