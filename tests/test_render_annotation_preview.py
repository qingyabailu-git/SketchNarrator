from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "renderer" / "scripts" / "render_annotation_preview.py"
if str(MODULE_PATH.parent) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("render_annotation_preview", MODULE_PATH)
preview = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(preview)


class RenderAnnotationPreviewTests(unittest.TestCase):
    def test_semantic_annotation_without_hand_path_uses_reveal_direction(self) -> None:
        element = {
            "region": {"x": 100, "y": 50, "width": 200, "height": 100},
            "reveal": {"direction": "left_to_right"},
        }
        start, end = preview.preview_path(element)
        self.assertLess(start[0], end[0])
        self.assertEqual(start[1], end[1])

    def test_legacy_explicit_hand_path_is_preserved(self) -> None:
        element = {
            "region": {"x": 0, "y": 0, "width": 20, "height": 20},
            "reveal": {"direction": "top_to_bottom"},
            "handPath": {"start": [3, 4], "end": [18, 19]},
        }
        self.assertEqual(preview.preview_path(element), ((3, 4), (18, 19)))


if __name__ == "__main__":
    unittest.main()
