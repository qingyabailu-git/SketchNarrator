from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("render_stream_frame_writer", SCRIPTS / "render_stream_whiteboard.py")
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeWriter:
    def __init__(self) -> None:
        self.frames = []
        self.released = False

    def isOpened(self) -> bool:
        return True

    def write(self, frame) -> None:
        self.frames.append(frame.copy())

    def release(self) -> None:
        self.released = True


class FrameCountWriterTests(unittest.TestCase):
    def test_title_card_fits_reserved_region_without_cropping(self) -> None:
        card = Image.new("RGBA", (1400, 120), (255, 255, 255, 255))
        for width, height in ((320, 180), (960, 540), (1920, 1080)):
            with self.subTest(size=(width, height)), patch.object(module, "render_concept_card", return_value=card):
                rgba, x, y = module._build_title_card_overlay("本幕重点", "#356AE6", None, width, height)
                self.assertEqual((x, y), (round(width * 0.025), round(height * 0.028)))
                self.assertLessEqual(rgba.shape[1], int(width * 0.95))
                self.assertLessEqual(rgba.shape[0], int(height * 0.112))
                self.assertLessEqual(x + rgba.shape[1], width)
                self.assertLessEqual(y + rgba.shape[0], round(height * 0.14))
                scale = min(1.0, int(width * 0.95) / card.width, int(height * 0.112) / card.height)
                self.assertLessEqual(abs(rgba.shape[1] - card.width * scale), 1)
                self.assertLessEqual(abs(rgba.shape[0] - card.height * scale), 1)

    def test_disabled_title_card_does_not_build_overlay(self) -> None:
        with patch.object(module, "render_concept_card") as render:
            for text in (None, "", "   "):
                self.assertIsNone(module._build_title_card_overlay(text, "#356AE6", None, 960, 540))
            render.assert_not_called()

    def test_static_title_card_overlay_is_present_on_written_and_padded_frames(self) -> None:
        delegate = FakeWriter()
        rgba = np.zeros((1, 1, 4), dtype=np.uint8)
        rgba[0, 0] = [255, 0, 0, 255]
        writer = module.FrameCountWriter(
            delegate,
            target_frames=2,
            frame_overlay=(rgba, 0, 0),
        )
        writer.write(np.zeros((2, 2, 3), dtype=np.uint8))
        writer.release()
        self.assertEqual(delegate.frames[0][0, 0].tolist(), [0, 0, 255])
        self.assertEqual(delegate.frames[1][0, 0].tolist(), [0, 0, 255])

    def test_pads_and_caps_to_exact_frame_budget(self) -> None:
        delegate = FakeWriter()
        writer = module.FrameCountWriter(delegate, target_frames=3)
        writer.write(np.zeros((2, 2, 3), dtype=np.uint8))
        writer.release()
        self.assertEqual(len(delegate.frames), 3)
        self.assertTrue(delegate.released)

        delegate = FakeWriter()
        writer = module.FrameCountWriter(delegate, target_frames=2)
        for value in range(4):
            writer.write(np.full((2, 2, 3), value, dtype=np.uint8))
        writer.release()
        self.assertEqual(len(delegate.frames), 2)
        self.assertEqual(writer.frames_attempted, 4)


if __name__ == "__main__":
    unittest.main()
