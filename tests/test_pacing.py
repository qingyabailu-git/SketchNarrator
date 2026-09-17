#!/usr/bin/env python3
"""Unit tests for adaptive duration pacing module."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pacing import (
    calculate_adaptive_durations,
    pace_annotation_dict,
    pace_project_annotations,
)


class PacingTests(unittest.TestCase):
    def test_calculate_adaptive_durations_multi_element(self) -> None:
        elements = [
            {"id": "e1", "sequence": 1, "reveal": {"startMs": 100, "durationMs": 1200}},
            {"id": "e2", "sequence": 2, "reveal": {"startMs": 8000, "durationMs": 1200}},
            {"id": "e3", "sequence": 3, "reveal": {"startMs": 16000, "durationMs": 1200}},
        ]
        scene_dur = 24000
        paced = calculate_adaptive_durations(elements, scene_dur, ratio=0.72)
        
        self.assertEqual(len(paced), 3)
        # e1 window: 8000 - 100 = 7900 ms -> ~72% = 5688 ms
        self.assertGreater(paced[0]["reveal"]["durationMs"], 4500)
        self.assertLessEqual(paced[0]["reveal"]["startMs"] + paced[0]["reveal"]["durationMs"], 8000)

        # e2 window: 16000 - 8000 = 8000 ms -> ~72% = 5760 ms
        self.assertGreater(paced[1]["reveal"]["durationMs"], 4500)
        self.assertLessEqual(paced[1]["reveal"]["startMs"] + paced[1]["reveal"]["durationMs"], 16000)

        # e3 (last) window: 24000 - 16000 = 8000 ms
        self.assertGreater(paced[2]["reveal"]["durationMs"], 4000)
        self.assertLessEqual(paced[2]["reveal"]["startMs"] + paced[2]["reveal"]["durationMs"], 24000 - 450)

    def test_calculate_adaptive_durations_default_ratio(self) -> None:
        elements = [
            {"id": "e1", "sequence": 1, "reveal": {"startMs": 100, "durationMs": 1200}},
            {"id": "e2", "sequence": 2, "reveal": {"startMs": 5000, "durationMs": 1200}},
        ]
        scene_dur = 10000
        paced = calculate_adaptive_durations(elements, scene_dur)
        # Default ratio is 0.92: window 4900 -> dur min(4508, 4900-250=4650) = 4508
        self.assertEqual(paced[0]["reveal"]["durationMs"], 4508)
        # Last element: window 5000 -> dur min(4500, 5000-450=4550) = 4500
        self.assertEqual(paced[1]["reveal"]["durationMs"], 4500)

    def test_calculate_adaptive_durations_single_element(self) -> None:
        elements = [
            {"id": "e1", "sequence": 1, "reveal": {"startMs": 200, "durationMs": 1200}},
        ]
        scene_dur = 10000
        paced = calculate_adaptive_durations(elements, scene_dur, ratio=0.70)
        self.assertEqual(len(paced), 1)
        dur = paced[0]["reveal"]["durationMs"]
        self.assertGreater(dur, 4000)
        self.assertLessEqual(paced[0]["reveal"]["startMs"] + dur, scene_dur - 600)

    def test_calculate_adaptive_durations_tight_window(self) -> None:
        elements = [
            {"id": "e1", "sequence": 1, "reveal": {"startMs": 0, "durationMs": 1200}},
            {"id": "e2", "sequence": 2, "reveal": {"startMs": 800, "durationMs": 1200}},
        ]
        scene_dur = 1600
        paced = calculate_adaptive_durations(elements, scene_dur)
        # e1 must end before e2 starts
        self.assertLessEqual(paced[0]["reveal"]["startMs"] + paced[0]["reveal"]["durationMs"], 800)
        # e2 must end before scene ends
        self.assertLessEqual(paced[1]["reveal"]["startMs"] + paced[1]["reveal"]["durationMs"], 1600)

    def test_pace_annotation_dict_detects_changes(self) -> None:
        doc = {
            "sceneId": "scene-01",
            "sceneDurationMs": 15000,
            "elements": [
                {"id": "e1", "reveal": {"startMs": 100, "durationMs": 1200}},
                {"id": "e2", "reveal": {"startMs": 7500, "durationMs": 1200}},
            ]
        }
        updated, changes = pace_annotation_dict(doc)
        self.assertEqual(len(changes), 2)
        self.assertGreater(updated["elements"][0]["reveal"]["durationMs"], 1200)
        self.assertGreater(updated["elements"][1]["reveal"]["durationMs"], 1200)


if __name__ == "__main__":
    unittest.main()
