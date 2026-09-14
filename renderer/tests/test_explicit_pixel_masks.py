import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from pixel_contract import compile_masks, encode_mask, require_ownership


class ExplicitMaskTests(unittest.TestCase):
    def setUp(self):
        self.image = np.full((30, 40, 3), 255, dtype=np.uint8)
        self.image[10:20, 10:30] = 0

    def annotation(self, masks=None):
        elements = []
        for index in range(2):
            element = {'id': str(index), 'region': {'x': 5, 'y': 5, 'width': 30, 'height': 20}, 'reveal': {}}
            if masks is not None:
                element['pixelMask'] = encode_mask(masks[index])
            elements.append(element)
        return {'canvas': {'width': 40, 'height': 30}, 'elements': elements}

    def test_ambiguous_overlap_is_not_assigned_by_order_or_center(self):
        owner, _, report = require_ownership(self.image, self.annotation())
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["warnings"])
        self.assertEqual(owner[15, 15], 1)

    def test_explicit_identity_can_cross_a_connected_component(self):
        left = np.zeros((30, 40), dtype=bool)
        right = left.copy()
        left[10:20,10:20] = True
        right[10:20,20:30] = True
        ann = self.annotation([left,right])
        owner, _, _ = require_ownership(self.image, ann)
        self.assertEqual(owner[15,19], 1)
        self.assertEqual(owner[15,20], 2)
        ann['elements'][0]['reveal']['protectedRegions'] = [{'x':19,'y':10,'width':1,'height':10}]
        with self.assertRaises(ValueError):
            require_ownership(self.image, ann)

    def test_nearby_unmarked_foreground_is_not_silently_claimed(self):
        ann = {'canvas': {'width':40,'height':30}, 'elements': [
            {'id':'one','region':{'x':10,'y':10,'width':19,'height':10},'reveal':{}}]}
        owner, _, report = require_ownership(self.image, ann)
        self.assertTrue(report["warnings"])
        self.assertEqual(owner[15, 29], 0)
        self.assertIn("one", compile_masks(self.image, ann))


if __name__ == "__main__":
    unittest.main()
