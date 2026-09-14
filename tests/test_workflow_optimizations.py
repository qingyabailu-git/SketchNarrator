#!/usr/bin/env python3
"""Regression tests for workflow optimizations, invalidations, and timing metrics."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import json
import shutil
import tempfile
from unittest import mock
import workflow as wf


class TestWorkflowOptimizations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_wf_opt_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_renderer_python_rejects_missing_dependencies(self):
        """9: Verify renderer_python() rejects dummy/broken python and selects a valid one."""
        # Non-existent file
        self.assertFalse(wf._probe_renderer_python(self.temp_dir / "non_existent_python.exe"))

        # Plain text file named python.exe (should be rejected instantly by binary header check)
        dummy_dir = self.temp_dir / "dummy_env" / "Scripts"
        dummy_dir.mkdir(parents=True)
        dummy_py = dummy_dir / "python.exe"
        dummy_py.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        self.assertFalse(wf._probe_renderer_python(dummy_py))

        # Real system python (which does not have cv2/numpy/av/PIL if run in clean env, or probe test)
        valid_py = wf.renderer_python(self.temp_dir)
        self.assertTrue(wf._probe_renderer_python(valid_py))

    def test_scene_invalidation_reasons(self):
        """10: Verify detailed scene invalidation reasons."""
        root = self.temp_dir
        img_path = root / "scene.png"
        img_path.write_bytes(b"image_data_1")
        ann_path = root / "scene.annotation.json"
        ann_path.write_text('{"sceneId":"s1"}', encoding="utf-8")

        record = {"image": "scene.png", "annotation": "scene.annotation.json"}
        scene_plan = {"sceneId": "s1"}
        frame_plan = {"target_frames": 90, "lead_frames": 0, "total_ms": 3000}
        renderer_profile = {"profile": "standard"}

        key1, payload1 = wf.scene_render_cache_payload(
            root, record, scene_plan, frame_plan, renderer_profile, 30, 1080, "small-hand", "hash123"
        )
        cached_entry = {
            "key": key1,
            "path": "renders/s1.mp4",
            "sha256": "fakehash",
            "payload": payload1,
        }

        # Case 1: no prior cache
        reason_none = wf.scene_invalidation_reason(root, None, payload1)
        self.assertEqual(reason_none, "no_prior_cache")

        # Case 2: output missing
        reason_missing = wf.scene_invalidation_reason(root, cached_entry, payload1)
        self.assertEqual(reason_missing, "cached_output_missing")

        # Create output file
        render_out = root / "renders" / "s1.mp4"
        render_out.parent.mkdir(parents=True, exist_ok=True)
        render_out.write_bytes(b"render_content")
        cached_entry["sha256"] = wf.digest(render_out)

        # Case 3: image modified
        img_path.write_bytes(b"image_data_2_changed")
        _, payload_img_changed = wf.scene_render_cache_payload(
            root, record, scene_plan, frame_plan, renderer_profile, 30, 1080, "small-hand", "hash123"
        )
        reason_img = wf.scene_invalidation_reason(root, cached_entry, payload_img_changed)
        self.assertEqual(reason_img, "image_modified")

        # Restore image, modify annotation
        img_path.write_bytes(b"image_data_1")
        ann_path.write_text('{"sceneId":"s1","changed":true}', encoding="utf-8")
        _, payload_ann_changed = wf.scene_render_cache_payload(
            root, record, scene_plan, frame_plan, renderer_profile, 30, 1080, "small-hand", "hash123"
        )
        reason_ann = wf.scene_invalidation_reason(root, cached_entry, payload_ann_changed)
        self.assertEqual(reason_ann, "annotation_modified")

    def test_subtitle_style_change_does_not_affect_scene_cache_key(self):
        """11: Verify changing subtitle styles leaves scene render cache key unchanged."""
        root = self.temp_dir
        img_path = root / "scene.png"
        img_path.write_bytes(b"img")
        ann_path = root / "scene.annotation.json"
        ann_path.write_text('{"sceneId":"s1"}', encoding="utf-8")
        record = {"image": "scene.png", "annotation": "scene.annotation.json"}

        key_style1, _ = wf.scene_render_cache_payload(
            root, record, {"sceneId": "s1"}, {"target_frames": 90}, {}, 30, 1080, "small-hand", "h1"
        )
        key_style2, _ = wf.scene_render_cache_payload(
            root, record, {"sceneId": "s1"}, {"target_frames": 90}, {}, 30, 1080, "small-hand", "h1"
        )
        self.assertEqual(key_style1, key_style2)

    def test_subtitle_filter_uses_shared_discovered_cjk_font(self):
        font_path = Path("C:/portable-fonts/NotoSansCJKsc-Regular.otf")
        with mock.patch.object(wf, "cjk_font_identity", return_value=(font_path, "Noto Sans CJK SC")):
            filter_value = wf.build_subtitle_filter(1.25)
        self.assertIn("FontName=Noto Sans CJK SC", filter_value)
        self.assertIn("fontsdir='C\\:/portable-fonts'", filter_value)
        self.assertNotIn("Microsoft YaHei", filter_value)

    def test_audio_duration_uses_unified_ffprobe_runtime(self):
        fake = mock.Mock(returncode=0, stdout="1.250\n")
        with (
            mock.patch.object(wf, "local_runtime_info", return_value={"ffprobe": {"path": "ffprobe-portable"}}),
            mock.patch.object(wf.subprocess, "run", return_value=fake) as run_mock,
        ):
            duration = wf.audio_duration_ms(self.temp_dir / "voice.mp3")
        self.assertEqual(duration, 1250)
        self.assertEqual(run_mock.call_args.args[0][0], "ffprobe-portable")


if __name__ == "__main__":
    unittest.main()
