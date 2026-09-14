from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "board_layout.py"
SPEC = importlib.util.spec_from_file_location("board_layout", MODULE_PATH)
board_layout = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(board_layout)


class BoardLayoutTests(unittest.TestCase):
    def test_large_subject_is_scaled_into_safe_frame_without_crop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "target.png"
            image = Image.new("RGB", (200, 100), "#f5ead8")
            draw = ImageDraw.Draw(image)
            draw.rectangle((5, 0, 195, 95), fill="#222222")
            image.save(source)
            annotation = {
                "canvas": {"width": 200, "height": 100},
                "elements": [{"region": {"x": 5, "y": 0, "width": 190, "height": 95}}],
            }
            result = board_layout.fit_board_safe_area(source, target, annotation)
            self.assertTrue(result["applied"])
            self.assertLess(result["scale"], 1.0)
            with Image.open(target) as img_open:
                output = np.asarray(img_open.convert("RGB"))
            background = board_layout.median_background(output)
            bbox = board_layout.foreground_bbox(output, background)
            self.assertIsNotNone(bbox)
            assert bbox
            self.assertGreaterEqual(bbox[0], round(200 * 0.04))
            self.assertGreaterEqual(bbox[1], round(100 * 0.04))
            self.assertLessEqual(bbox[2], round(200 * 0.96))
            self.assertLessEqual(bbox[3], round(100 * 0.82))
            region = annotation["elements"][0]["region"]
            self.assertGreater(region["y"], 0)
            self.assertLess(region["height"], 95)

    def test_small_safe_subject_is_not_rescaled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "target.png"
            image = Image.new("RGB", (200, 100), "#f5ead8")
            ImageDraw.Draw(image).ellipse((60, 15, 140, 65), fill="#222222")
            image.save(source)
            annotation = {"elements": [{"region": {"x": 55, "y": 10, "width": 90, "height": 60}}]}
            result = board_layout.fit_board_safe_area(source, target, annotation)
            self.assertFalse(result["applied"])
            self.assertEqual(result["reason"], "already-safe")

    def test_raw_ai_image_1376x768_snaps_to_1920x1080(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "target.png"
            image = Image.new("RGB", (1376, 768), "#f5ebd7")
            draw = ImageDraw.Draw(image)
            draw.ellipse((300, 150, 1000, 600), fill="#333333")
            image.save(source)
            annotation = {
                "canvas": {"width": 1376, "height": 768},
                "elements": [{"region": {"x": 300, "y": 150, "width": 700, "height": 450}}],
            }
            result = board_layout.fit_board_safe_area(source, target, annotation)
            self.assertTrue(result["applied"])
            with Image.open(target) as target_img:
                self.assertEqual(target_img.size, (1920, 1080))
            self.assertEqual(annotation["canvas"], {"width": 1920, "height": 1080})
            reg = annotation["elements"][0]["region"]
            self.assertGreater(reg["width"], 700)
            self.assertLessEqual(reg["x"] + reg["width"], 1920)

    def test_vertical_image_snaps_to_9_16(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "target.png"
            image = Image.new("RGB", (768, 1376), "#f5ebd7")
            draw = ImageDraw.Draw(image)
            draw.ellipse((150, 300, 600, 1000), fill="#333333")
            image.save(source)
            annotation = {
                "canvas": {"width": 768, "height": 1376},
                "elements": [{"region": {"x": 150, "y": 300, "width": 450, "height": 700}}],
            }
            result = board_layout.fit_board_safe_area(source, target, annotation)
            self.assertTrue(result["applied"])
            with Image.open(target) as target_img:
                self.assertEqual(target_img.size, (1080, 1920))
            self.assertEqual(annotation["canvas"], {"width": 1080, "height": 1920})

    def test_layout_aware_fit_preserves_preplanned_side_placement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "target.png"
            image = Image.new("RGB", (400, 225), "#f5ead8")
            ImageDraw.Draw(image).ellipse((12, 45, 145, 175), fill="#222222")
            image.save(source)
            annotation = {
                "canvas": {"width": 400, "height": 225},
                "elements": [{"region": {"x": 10, "y": 42, "width": 140, "height": 138}}],
            }
            result = board_layout.fit_board_safe_area(
                source,
                target,
                annotation,
                project_aspect="16:9",
                preserve_layout=True,
            )
            self.assertEqual(result["mode"], "uniform-fit-preserve-layout")
            with Image.open(target) as target_img:
                output = np.asarray(target_img.convert("RGB"))
            bbox = board_layout.foreground_bbox(output, board_layout.median_background(output))
            self.assertIsNotNone(bbox)
            assert bbox
            self.assertLess((bbox[0] + bbox[2]) / 2, 1920 / 2)


if __name__ == "__main__":
    unittest.main()
