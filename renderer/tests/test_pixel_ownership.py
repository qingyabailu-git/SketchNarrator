from __future__ import annotations

import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from pixel_contract import require_ownership


class PixelOwnershipTests(unittest.TestCase):
    def fixture(self):
        image = np.full((40, 100, 3), 255, dtype=np.uint8)
        image[12:28, 10:90] = 0
        annotation = {"canvas": {"width": 100, "height": 40}, "elements": [
            {"id": "left", "region": {"x": 5, "y": 5, "width": 55, "height": 30}},
            {"id": "right", "region": {"x": 40, "y": 5, "width": 55, "height": 30}},
        ]}
        return image, annotation

    def test_foreground_overlap_is_advisory_in_either_order(self):
        image, annotation = self.fixture()
        for _ in range(2):
            owner, foreground, report = require_ownership(image, annotation)
            self.assertEqual(report["errors"], [])
            self.assertTrue(report["warnings"])
            self.assertTrue(np.array_equal(owner > 0, foreground))
            annotation["elements"].reverse()

    def test_rectangles_may_overlap_in_background(self):
        image, annotation = self.fixture()
        image[12:28, 40:60] = 255
        owner, foreground, report = require_ownership(image, annotation)
        self.assertFalse(report["errors"])
        self.assertTrue(np.array_equal(owner > 0, foreground))
        self.assertEqual(owner[20, 20], 1)
        self.assertEqual(owner[20, 80], 2)


if __name__ == "__main__":
    unittest.main()
