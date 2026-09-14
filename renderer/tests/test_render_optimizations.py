#!/usr/bin/env python3
"""Regression and verification tests for SketchNarrator rendering optimizations."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import numpy as np
import cv2
import tempfile
import json
import shutil
import time

import stream_render as sr
import render_stream_whiteboard as rsw
import ffmpeg_runtime as fr


class TestRenderOptimizations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_opt_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reveal_ink_segment_roi_equivalence(self):
        """1 & 2: Verify ROI _reveal_ink_segment matches full-canvas reference across edges, zero-length, thick, and random segments."""
        h, w = 1080, 1920
        ink_pixels = np.zeros((h, w), dtype=bool)
        cv2.rectangle(ink_pixels.view(np.uint8), (50, 50), (1850, 1030), 1, -1)
        allowed = np.ones((h, w), dtype=bool)
        ink_paint = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8).astype(np.float32)

        def ref_reveal(drawn, a, b, radius=3):
            seg = np.zeros((h, w), dtype=np.uint8)
            thick = max(1, radius * 2 + 1)
            cv2.line(seg, a, b, 255, thickness=thick, lineType=cv2.LINE_AA)
            rev = (seg > 0) & ink_pixels & allowed
            drawn[rev] = ink_paint[rev]

        def roi_reveal(drawn, a, b, radius=3):
            thick = max(1, radius * 2 + 1)
            margin = thick + 2
            x0 = max(0, min(a[0], b[0]) - margin)
            y0 = max(0, min(a[1], b[1]) - margin)
            x1 = min(w, max(a[0], b[0]) + margin + 1)
            y1 = min(h, max(a[1], b[1]) + margin + 1)
            if x1 <= x0 or y1 <= y0:
                return
            local_a = (a[0] - x0, a[1] - y0)
            local_b = (b[0] - x0, b[1] - y0)
            seg_local = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
            cv2.line(seg_local, local_a, local_b, 255, thickness=thick, lineType=cv2.LINE_AA)
            rev_local = (seg_local > 0) & ink_pixels[y0:y1, x0:x1] & allowed[y0:y1, x0:x1]
            target_crop = drawn[y0:y1, x0:x1]
            paint_crop = ink_paint[y0:y1, x0:x1]
            target_crop[rev_local] = paint_crop[rev_local]

        test_segments = [
            ((100, 100), (500, 500)),
            ((0, 0), (200, 200)),
            ((1800, 1000), (1919, 1079)),
            ((300, 300), (300, 300)),
            ((0, 540), (1919, 540)),
            ((960, 0), (960, 1079)),
        ]
        np.random.seed(42)
        for _ in range(50):
            p1 = (int(np.random.randint(0, w)), int(np.random.randint(0, h)))
            p2 = (int(np.random.randint(0, w)), int(np.random.randint(0, h)))
            test_segments.append((p1, p2))

        for rad in [1, 3, 7, 15]:
            drawn_ref = np.zeros((h, w, 3), dtype=np.float32)
            drawn_roi = np.zeros((h, w, 3), dtype=np.float32)
            for a, b in test_segments:
                ref_reveal(drawn_ref, a, b, radius=rad)
                roi_reveal(drawn_roi, a, b, radius=rad)
            self.assertTrue(np.array_equal(drawn_ref, drawn_roi), f"Mismatch for radius {rad}")

    def test_skeleton_cropping_equivalence(self):
        """3: Verify cropped Zhang-Suen thinning matches full-canvas skeleton coordinates."""
        h, w = 1080, 1920
        mask = np.zeros((h, w), dtype=bool)
        cv2.circle(mask.view(np.uint8), (550, 450), 60, 1, 4)
        cv2.line(mask.view(np.uint8), (550, 390), (550, 510), 1, 4)

        skel_full = sr._zhang_suen_skeleton(mask, max_iterations=160)
        strokes_full = sr.trace_8connected(skel_full, min_points=4)

        ys, xs = np.where(mask)
        min_y, max_y = int(ys.min()), int(ys.max())
        min_x, max_x = int(xs.min()), int(xs.max())
        crop = mask[min_y:max_y + 1, min_x:max_x + 1]
        skel_crop = sr._zhang_suen_skeleton(crop, max_iterations=160)
        strokes_crop = sr.trace_8connected(skel_crop, min_points=4)
        strokes_mapped = [[(x + min_x, y + min_y) for x, y in s] for s in strokes_crop]

        self.assertEqual(len(strokes_full), len(strokes_mapped))
        for s1, s2 in zip(strokes_full, strokes_mapped):
            self.assertEqual(s1, s2)

    def test_vectorized_tip_overlay_stamp_equivalence(self):
        """4: Verify vectorized TipOverlay.stamp matches original channel-by-channel blending."""
        h, w = 400, 600
        canvas1 = np.full((h, w, 3), 200, dtype=np.uint8)
        canvas2 = canvas1.copy()

        hand_h, hand_w = 120, 80
        hand_bgr = np.random.randint(0, 255, (hand_h, hand_w, 3), dtype=np.uint8)
        mask = np.linspace(0.0, 1.0, hand_h * hand_w, dtype=np.float32).reshape(hand_h, hand_w)

        overlay = sr.TipOverlay(hand_bgr, mask, tip_anchor_x=0.5, tip_anchor_y=0.7)

        positions = [(100, 100), (0, 0), (w, h), (-20, 50), (w - 10, h - 10)]
        for px, py in positions:
            overlay.stamp(canvas1, px, py)

        for px, py in positions:
            anchor_x = px - overlay.tip_px
            anchor_y = py - overlay.tip_py
            x0 = max(0, anchor_x)
            y0 = max(0, anchor_y)
            x1 = min(w, anchor_x + overlay.w)
            y1 = min(h, anchor_y + overlay.h)
            if x1 <= x0 or y1 <= y0:
                continue
            sx0 = x0 - anchor_x
            sy0 = y0 - anchor_y
            sx1 = sx0 + (x1 - x0)
            sy1 = sy0 + (y1 - y0)
            region = canvas2[y0:y1, x0:x1]
            hand_region = overlay.hand[sy0:sy1, sx0:sx1]
            mask_region = overlay.mask[sy0:sy1, sx0:sx1]
            inv_region = overlay.mask_inv[sy0:sy1, sx0:sx1]
            for c in range(3):
                region[:, :, c] = (
                    region[:, :, c] * inv_region + hand_region[:, :, c] * mask_region
                ).astype(np.uint8)
            canvas2[y0:y1, x0:x1] = region

        self.assertTrue(np.array_equal(canvas1, canvas2))

    def test_synthetic_large_rgba_overlay(self):
        """5: Verify large synthetic RGBA character overlay works cleanly without private assets."""
        canvas = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        char_h, char_w = 600, 400
        char_bgr = np.full((char_h, char_w, 3), (80, 120, 240), dtype=np.uint8)
        char_alpha = np.zeros((char_h, char_w), dtype=np.float32)
        cv2.circle(char_alpha, (200, 300), 180, 1.0, -1)
        char_alpha = cv2.GaussianBlur(char_alpha, (15, 15), 0)

        overlay = sr.TipOverlay(char_bgr, char_alpha, tip_anchor_x=0.5, tip_anchor_y=0.9)
        stamped = overlay.stamp(canvas, 960, 540)
        self.assertEqual(stamped.shape, (1080, 1920, 3))
        self.assertGreater(overlay.visible_ratio((1080, 1920), 960, 540), 0.95)

    def test_tip_overlay_opacity_supports_natural_fade(self):
        canvas = np.full((120, 160, 3), 200, dtype=np.uint8)
        hand = np.full((40, 30, 3), 20, dtype=np.uint8)
        mask = np.ones((40, 30), dtype=np.float32)
        overlay = sr.TipOverlay(hand, mask, tip_anchor_x=0.5, tip_anchor_y=0.5)
        hidden = overlay.stamp(canvas.copy(), 80, 60, opacity=0.0)
        half = overlay.stamp(canvas.copy(), 80, 60, opacity=0.5)
        full = overlay.stamp(canvas.copy(), 80, 60, opacity=1.0)
        self.assertTrue(np.array_equal(hidden, canvas))
        self.assertGreater(float(np.mean(half)), float(np.mean(full)))
        self.assertLess(float(np.mean(half)), float(np.mean(hidden)))

    def test_ffmpeg_runtime_cache_and_invalidation(self):
        """8: Verify FFmpeg runtime cache reduces probes and clear_runtime_cache resets state."""
        fr.clear_runtime_cache()
        self.assertEqual(len(fr._RUNTIME_INFO_CACHE), 0)

        info1 = fr.runtime_info()
        self.assertIn("__default__", fr._RUNTIME_INFO_CACHE)
        self.assertIs(info1, fr.runtime_info())

        fr.clear_runtime_cache()
        self.assertEqual(len(fr._RUNTIME_INFO_CACHE), 0)


if __name__ == "__main__":
    unittest.main()
