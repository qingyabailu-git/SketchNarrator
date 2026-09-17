from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


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
