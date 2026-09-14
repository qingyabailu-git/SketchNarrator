from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "board_qa.py"
SPEC = importlib.util.spec_from_file_location("board_qa", MODULE_PATH)
board_qa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(board_qa)


class BoardQaTests(unittest.TestCase):
    def _fixture(self, root: Path, top: int) -> dict:
        image = Image.new("RGB", (200, 100), "#f5ead8")
        draw = ImageDraw.Draw(image)
        draw.ellipse((25, 8, 85, 55), fill="#111111")
        draw.rectangle((35, 45, 75, 82), fill="#f1b52e", outline="#111111", width=3)
        draw.rectangle((130, 20, 165, 50), outline="#111111", width=4)
        (root / "boards").mkdir()
        image.save(root / "boards" / "scene.png")

        annotation = {
            "sceneId": "scene-01",
            "canvas": {"width": 200, "height": 100},
            "sceneDurationMs": 2000,
            "elements": [
                {"id": '1',
                    "sequence": 1,
                    "region": {"x": 15, "y": top, "width": 85, "height": 85 - top},
                    "reveal": {"startMs": 0, "durationMs": 700, "protectedRegions": []},
                },
                {"id": '2',
                    "sequence": 2,
                    "region": {"x": 120, "y": 10, "width": 60, "height": 50},
                    "reveal": {"startMs": 800, "durationMs": 600, "protectedRegions": []},
                },
            ],
        }
        (root / "annotations").mkdir()
        (root / "annotations" / "scene.json").write_text(json.dumps(annotation), encoding="utf-8")
        return {"image": "boards/scene.png", "annotation": "annotations/scene.json"}

    def test_uncovered_head_is_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._fixture(root, top=35)
            result = board_qa.check_scene(root, {"id": "scene-01"}, record)
            self.assertTrue(result["ok"], result["errors"])
            self.assertGreater(result["uncovered_foreground_ratio"], 0.05)
            self.assertTrue(any("不会绘制" in warning for warning in result["warnings"]))

    def test_padded_region_covers_full_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._fixture(root, top=2)
            result = board_qa.check_scene(root, {"id": "scene-01"}, record)
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["uncovered_foreground_pixels"], 0)

    def test_explicit_masks_allow_spatial_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._fixture(root, top=2)
            annotation_path = root / record["annotation"]
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            from pixel_contract import compile_masks
            import numpy as np
            masks = compile_masks(np.asarray(Image.open(root / record["image"]).convert("RGB")), annotation)
            for element in annotation["elements"]:
                element["pixelMask"] = masks[element["id"]]
            annotation["elements"][1]["region"] = {"x": 70, "y": 10, "width": 110, "height": 50}
            annotation_path.write_text(json.dumps(annotation), encoding="utf-8")

            result = board_qa.check_scene(root, {"id": "scene-01"}, record)

            self.assertTrue(result["ok"], result["errors"])
            self.assertGreater(result["unprotected_overlap_pixels"], 0)
            self.assertEqual(result["overlap_foreground_pixels"], 0)

    def test_single_cohesive_element_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._fixture(root, top=2)
            annotation_path = root / record["annotation"]
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["elements"] = [{
                "id": "whole-illustration",
                "sequence": 1,
                "region": {"x": 10, "y": 2, "width": 170, "height": 82},
                "reveal": {"startMs": 0, "durationMs": 1400, "protectedRegions": []},
            }]
            annotation_path.write_text(json.dumps(annotation), encoding="utf-8")

            result = board_qa.check_scene(root, {"id": "scene-01"}, record)

            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["element_count"], 1)

    def test_empty_selection_is_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = self._fixture(root, top=2)
            annotation_path = root / record["annotation"]
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["elements"][0]["region"] = {"x": 10, "y": 2, "width": 90, "height": 82}
            annotation["elements"][1]["region"] = {"x": 10, "y": 2, "width": 90, "height": 82}
            annotation_path.write_text(json.dumps(annotation), encoding="utf-8")

            result = board_qa.check_scene(root, {"id": "scene-01"}, record)

            self.assertTrue(result["ok"], result["errors"])
            self.assertTrue(any("没有选中的前景" in warning for warning in result["warnings"]))

    def test_ambiguous_connected_stroke_has_deterministic_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "boards").mkdir()
            (root / "annotations").mkdir()
            image = Image.new("RGB", (200, 100), "#f5ead8")
            ImageDraw.Draw(image).line((20, 35, 180, 35), fill="#111111", width=6)
            image.save(root / "boards" / "scene.png")
            annotation = {
                "sceneId": "scene-01",
                "canvas": {"width": 200, "height": 100},
                "sceneDurationMs": 2000,
                "elements": [
                    {"id": '1',
                        "sequence": 1,
                        "region": {"x": 10, "y": 20, "width": 105, "height": 35},
                        "reveal": {"startMs": 0, "durationMs": 700, "protectedRegions": []},
                    },
                    {"id": '2',
                        "sequence": 2,
                        "region": {"x": 85, "y": 20, "width": 105, "height": 35},
                        "reveal": {"startMs": 800, "durationMs": 600, "protectedRegions": []},
                    },
                ],
            }
            (root / "annotations" / "scene.json").write_text(json.dumps(annotation), encoding="utf-8")
            record = {"image": "boards/scene.png", "annotation": "annotations/scene.json"}

            result = board_qa.check_scene(root, {"id": "scene-01"}, record)

            self.assertTrue(result["ok"], result["errors"])
            self.assertGreater(result["overlap_foreground_pixels"], 0)
            self.assertTrue(any("重叠前景像素" in warning for warning in result["warnings"]))

    def test_sparse_decorative_border_does_not_join_independent_elements(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "boards").mkdir()
            (root / "annotations").mkdir()
            image = Image.new("RGB", (240, 140), "#ffffff")
            draw = ImageDraw.Draw(image)
            draw.rectangle((4, 4, 236, 136), outline="#111111", width=1)
            draw.ellipse((35, 35, 75, 75), fill="#111111")
            draw.ellipse((165, 35, 205, 75), fill="#111111")
            image.save(root / "boards" / "scene.png")
            annotation = {
                "sceneId": "scene-01",
                "canvas": {"width": 240, "height": 140},
                "sceneDurationMs": 2000,
                "elements": [
                    {"id": '1',
                        "sequence": 1,
                        "region": {"x": 25, "y": 25, "width": 60, "height": 60},
                        "reveal": {"startMs": 0, "durationMs": 700, "protectedRegions": []},
                    },
                    {"id": '2',
                        "sequence": 2,
                        "region": {"x": 155, "y": 25, "width": 60, "height": 60},
                        "reveal": {"startMs": 800, "durationMs": 600, "protectedRegions": []},
                    },
                ],
            }
            from pixel_contract import encode_mask
            import numpy as np
            background = Image.new("L", (240,140), 0)
            ImageDraw.Draw(background).rectangle((4,4,236,136), outline=255, width=1)
            annotation["backgroundMask"] = encode_mask(np.asarray(background) > 0)
            (root / "annotations" / "scene.json").write_text(json.dumps(annotation), encoding="utf-8")
            record = {"image": "boards/scene.png", "annotation": "annotations/scene.json"}

            result = board_qa.check_scene(root, {"id": "scene-01"}, record)

            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["uncovered_foreground_pixels"], 0)
            self.assertEqual(result["split_foreground_components"], [])

    def test_style_contract_distinguishes_light_and_dark_backgrounds(self) -> None:
        dark_style = {
            "id": "dark-chalkboard",
            "render_mode": "dark",
            "visual_contract": {
                "background_hex": "#173E38",
                "background_luma_range": [22, 92],
                "background_rgb_tolerance": 55,
            },
        }
        valid = board_qa._background_style_check(
            board_qa.np.asarray([23, 62, 56], dtype=board_qa.np.float32),
            dark_style,
        )
        invalid = board_qa._background_style_check(
            board_qa.np.asarray([245, 244, 239], dtype=board_qa.np.float32),
            dark_style,
        )
        self.assertTrue(valid["checked"])
        self.assertEqual(valid["errors"], [])
        self.assertTrue(any("背景亮度" in item for item in invalid["errors"]))


if __name__ == "__main__":
    unittest.main()
