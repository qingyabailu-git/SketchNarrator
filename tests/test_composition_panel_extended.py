#!/usr/bin/env python3
"""Extended test cases for composition panel, edge validations, legacy plans, and formal source immutability."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from PIL import Image, ImageDraw
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import workflow as wf
from animation_plan import (
    AnimationPlanError,
    transition_plan,
    validate_animation_plan,
    validate_transition_pause_budgets,
)


class TestCompositionPanelExtended(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_panel_ext_"))
        self.project_dir = self.temp_dir / "project"
        self.setup_project(self.project_dir)

    def setup_project(self, root: Path):
        wf.init_project(root, "扩展测试项目", "验证边界与规则", 6, "暖米黄素描白板")

        audio_dir = root / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        words_data = {
            "version": 1,
            "duration_ms": 6000,
            "words": [
                {"text": "场景一首词", "start_ms": 100, "end_ms": 1000},
                {"text": "场景一末词", "start_ms": 1100, "end_ms": 2000},
                # Silence between 2000ms and 3000ms: pause budget = 1000ms (>= 820ms)
                {"text": "场景二首词", "start_ms": 3000, "end_ms": 4000},
                {"text": "场景二末词", "start_ms": 4100, "end_ms": 5500},
            ]
        }
        (audio_dir / "words.json").write_text(json.dumps(words_data, ensure_ascii=False), encoding="utf-8")

        storyboard_data = {
            "version": 3,
            "scenes": [
                {
                    "id": "scene-01", "title": "幕1", "narration": "测试1",
                    "start_ms": 0, "end_ms": 3000, "composition": "causal-chain",
                    "elements": [{"id":"scene-01_e1", "label":"主体1", "role":"原因", "trigger_text":"场景一首词"}]
                },
                {
                    "id": "scene-02", "title": "幕2", "narration": "测试2",
                    "start_ms": 3000, "end_ms": 6000, "composition": "timeline",
                    "elements": [{"id":"scene-02_e1", "label":"主体2", "role":"结果", "trigger_text":"场景二首词"}]
                }
            ]
        }
        (root / "storyboard.json").write_text(json.dumps(storyboard_data, ensure_ascii=False), encoding="utf-8")

        project, state = wf.load_project(root)
        project["scenes"] = storyboard_data["scenes"]
        state["approvals"]["script_voice"] = {"approved": True, "at": wf.now()}
        state["approvals"]["boards"] = {"approved": True, "at": wf.now()}

        # Boards and annotations
        boards_dir = root / "boards"
        ann_dir = root / "annotations"
        boards_dir.mkdir(parents=True, exist_ok=True)
        ann_dir.mkdir(parents=True, exist_ok=True)

        for sid, dur in [("scene-01", 3000), ("scene-02", 3000)]:
            board = Image.new("RGB", (1920,1080), "white")
            ImageDraw.Draw(board).rectangle((300,200,500,400), fill="black")
            board.save(boards_dir / f"{sid}.png")
            ann = {
                "sceneId": sid,
                "canvas": {"width": 1920, "height": 1080},
                "sceneDurationMs": dur,
                "elements": [
                    {
                        "id": f"{sid}_e1", "label": "主体", "sequence": 1, "narrativeRole": "主体",
                        "region": {"x": 100, "y": 100, "width": 500, "height": 500},
                        "reveal": {"direction": "top_to_bottom", "startMs": 100, "durationMs": 800, "maskPaddingPx": 22, "protectedRegions": []}
                    }
                ]
            }
            ann_file = ann_dir / f"{sid}.annotation.json"
            ann_file.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
            state.setdefault("boards", {})[sid] = {
                "image": f"boards/{sid}.png",
                "annotation": f"annotations/{sid}.annotation.json",
                "image_sha256": wf.digest(boards_dir / f"{sid}.png"),
                "annotation_sha256": wf.digest(ann_file),
            }
            state.setdefault("render_cache", {})[sid] = {"key": f"k_{sid}", "sha256": f"s_{sid}"}

        state["final_current"] = True
        state["final_sha256"] = "fsha_999"
        state["qa"] = {"ok": True}

        wf.refresh_stage(root, project, state)
        wf.write_json(root / "project.json", project)
        wf.write_json(root / "state.json", state)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_safe_animation_rejects_multiple_events_per_scene(self):
        """7: Verify V3.3 cannot have multiple animation events per scene."""
        invalid_plan = {
            "version": 3,
            "planVersion": "3.3",
            "rules": {"default_overlay_paths": False, "invent_geometry": False, "max_semantic_effects_per_scene": 1},
            "scenes": [
                {
                    "sceneId": "scene-01",
                    "sceneStartMs": 0,
                    "sceneEndMs": 3000,
                    "sceneDurationMs": 3000,
                    "events": [
                        {"targetElementId": "e1", "effect": "contour-indicate", "startMs": 100, "endMs": 1000, "timeBudgetMs": 900},
                        {"targetElementId": "e2", "effect": "highlight-wash", "startMs": 1100, "endMs": 2000, "timeBudgetMs": 900}
                    ],
                    "transition": None
                }
            ]
        }
        errors = validate_animation_plan(invalid_plan)
        self.assertTrue(any("超过每幕一个语义后动画" in err for err in errors))

    def test_safe_animation_rejects_geometric_paths(self):
        """8: Verify V3.3 rejects gesture paths / invented geometry."""
        invalid_plan = {
            "version": 3,
            "planVersion": "3.3",
            "rules": {"default_overlay_paths": False, "invent_geometry": False, "max_semantic_effects_per_scene": 1},
            "scenes": [
                {
                    "sceneId": "scene-01",
                    "sceneStartMs": 0,
                    "sceneEndMs": 3000,
                    "sceneDurationMs": 3000,
                    "events": [
                        {
                            "targetElementId": "e1",
                            "effect": "contour-indicate",
                            "gesturePath": [[100, 100], [200, 200]],
                            "startMs": 100,
                            "endMs": 1000,
                            "timeBudgetMs": 900
                        }
                    ],
                    "transition": None
                }
            ]
        }
        errors = validate_animation_plan(invalid_plan)
        self.assertTrue(any("不得带自动 gesturePath/path" in err for err in errors))

    def test_transition_pause_budget_validation(self):
        """Verify eraser timing follows real breath and rejects gaps below 300ms."""
        storyboard = wf.read_json(self.project_dir / "storyboard.json")
        tight_words = [
            {"text": "场景一首词", "start_ms": 100, "end_ms": 1000},
            {"text": "场景一末词", "start_ms": 1100, "end_ms": 2800},
            # Silence between 2800ms and 3000ms: pause budget = 200ms (< 300ms)
            {"text": "场景二首词", "start_ms": 3000, "end_ms": 4000},
            {"text": "场景二末词", "start_ms": 4100, "end_ms": 5500},
        ]
        with self.assertRaises(AnimationPlanError) as ctx:
            validate_transition_pause_budgets(storyboard["scenes"], tight_words)
        self.assertIn("300ms", str(ctx.exception))

    def test_long_pause_keeps_complete_frame_and_caps_active_transition(self):
        storyboard = wf.read_json(self.project_dir / "storyboard.json")
        words = [
            {"text": "场景一", "start_ms": 100, "end_ms": 1000},
            {"text": "场景二", "start_ms": 3000, "end_ms": 3600},
        ]
        transition = transition_plan(storyboard["scenes"], words)[0]
        self.assertEqual(transition["eraseDurationMs"], 420)
        self.assertEqual(transition["cleanCanvasMs"], 60)
        self.assertLessEqual(transition["eraseDurationMs"] + transition["cleanCanvasMs"], 500)
        self.assertEqual(transition["cleanCanvasEndMs"], transition["nextSceneFirstWordMs"])

    def test_v33_transition_copies_and_derived_fields_validate(self):
        transition = {
            "fromSceneId": "scene-01",
            "toSceneId": "scene-02",
            "previousWordEndMs": 2000,
            "nextSceneFirstWordMs": 3000,
            "gapMs": 1000,
            "stableHoldMs": 520,
            "eraseStartMs": 2520,
            "eraseEndMs": 2940,
            "eraseDurationMs": 420,
            "cleanCanvasStartMs": 2940,
            "cleanCanvasEndMs": 3000,
            "cleanCanvasMs": 60,
            "status": "ready",
        }
        plan = {
            "version": 3,
            "planVersion": "3.3",
            "rules": {
                "default_overlay_paths": False,
                "invent_geometry": False,
                "max_semantic_effects_per_scene": 1,
            },
            "scenes": [
                {
                    "sceneId": "scene-01",
                    "sceneStartMs": 0,
                    "sceneEndMs": 3000,
                    "sceneDurationMs": 3000,
                    "events": [],
                    "transition": dict(transition),
                },
                {
                    "sceneId": "scene-02",
                    "sceneStartMs": 3000,
                    "sceneEndMs": 6000,
                    "sceneDurationMs": 3000,
                    "events": [],
                    "transition": None,
                },
            ],
            "transitions": [dict(transition)],
        }
        self.assertEqual(validate_animation_plan(plan), [])

        plan["transitions"][0]["eraseStartMs"] = 2350
        errors = validate_animation_plan(plan)
        self.assertTrue(any("内容不一致" in error for error in errors))

    def test_v33_transition_rejects_lost_metadata_and_short_erase(self):
        incomplete = {
            "fromSceneId": "scene-01",
            "toSceneId": "scene-02",
            "eraseStartMs": 2450,
            "eraseEndMs": 2750,
            "cleanCanvasStartMs": 2750,
            "cleanCanvasEndMs": 3000,
        }
        plan = {
            "version": 3,
            "planVersion": "3.3",
            "rules": {
                "default_overlay_paths": False,
                "invent_geometry": False,
                "max_semantic_effects_per_scene": 1,
            },
            "scenes": [
                {
                    "sceneId": "scene-01",
                    "sceneStartMs": 0,
                    "sceneEndMs": 3000,
                    "sceneDurationMs": 3000,
                    "events": [],
                    "transition": dict(incomplete),
                },
                {
                    "sceneId": "scene-02",
                    "sceneStartMs": 3000,
                    "sceneEndMs": 6000,
                    "sceneDurationMs": 3000,
                    "events": [],
                    "transition": None,
                },
            ],
            "transitions": [dict(incomplete)],
        }
        errors = validate_animation_plan(plan)
        self.assertTrue(any("缺少派生字段" in error for error in errors))

    def test_apply_panel_annotation_change_invalidates_single_scene(self):
        """Verify modifying single scene annotation revokes board approval and invalidates only that scene."""
        root = self.project_dir
        ann_path = root / "annotations" / "scene-01.annotation.json"
        before_sha = wf.digest(ann_path)

        ann = wf.read_json(ann_path)
        ann["elements"][0]["region"]["x"] = 250.6

        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "project",
            "files": [
                {
                    "path": "annotations/scene-01.annotation.json",
                    "beforeSha256": before_sha,
                    "afterContent": ann,
                    "changesSummary": ["修改了场景1位置"]
                }
            ]
        }
        c_file = self.temp_dir / "single_scene_changes.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")

        res = wf.apply_panel(root, c_file, dry_run=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["annotations_changed"], ["scene-01"])

        saved_ann = wf.read_json(ann_path)
        self.assertEqual(saved_ann["elements"][0]["region"]["x"], 250.6)
        self.assertEqual(saved_ann["elements"][0]["reveal"]["durationMs"], 800)
        self.assertFalse((root / "animation-plan.json").exists(), "保存不隐式生成执行计划")

        _, state = wf.load_project(root)
        self.assertFalse(state["approvals"]["boards"]["approved"], "Board approval must be revoked")
        self.assertNotIn("scene-01", state.get("render_cache", {}))
        self.assertIn("scene-02", state.get("render_cache", {}), "Unmodified scene cache should be preserved")

    def test_apply_panel_rolls_back_refreshed_animation_plan_on_late_failure(self):
        root = self.project_dir
        annotation_path = root / "annotations" / "scene-01.annotation.json"
        animation_plan_path = root / "animation-plan.json"
        wf.write_json(animation_plan_path, {"sentinel": "before-transaction"})
        before = {
            path: path.read_bytes()
            for path in (
                annotation_path,
                animation_plan_path,
                root / "project.json",
                root / "state.json",
            )
        }

        annotation = wf.read_json(annotation_path)
        annotation["elements"][0]["region"]["x"] = 260
        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "project",
            "files": [{
                "path": "annotations/scene-01.annotation.json",
                "beforeSha256": wf.digest(annotation_path),
                "afterContent": annotation,
                "changesSummary": ["触发动画计划刷新后模拟晚期失败"],
            }],
        }
        changes_path = self.temp_dir / "rollback-existing-plan.json"
        changes_path.write_text(json.dumps(changeset, ensure_ascii=False), encoding="utf-8")

        original_write = wf.write_json
        def fail_state(path, value):
            if Path(path) == root / "state.json":
                raise OSError("simulated late failure")
            return original_write(path, value)
        with patch.object(wf, "write_json", side_effect=fail_state):
            with self.assertRaisesRegex(wf.WorkflowError, "已自动回滚全部文件"):
                wf.apply_panel(root, changes_path, dry_run=False)

        for path, expected in before.items():
            self.assertEqual(path.read_bytes(), expected, f"未完整回滚：{path.name}")

    def test_apply_panel_removes_new_animation_plan_on_late_failure(self):
        root = self.project_dir
        annotation_path = root / "annotations" / "scene-01.annotation.json"
        animation_plan_path = root / "animation-plan.json"
        self.assertFalse(animation_plan_path.exists())

        annotation = wf.read_json(annotation_path)
        annotation["elements"][0]["region"]["x"] = 270
        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "project",
            "files": [{
                "path": "annotations/scene-01.annotation.json",
                "beforeSha256": wf.digest(annotation_path),
                "afterContent": annotation,
                "changesSummary": ["验证回滚删除事务中新建的动画计划"],
            }],
        }
        changes_path = self.temp_dir / "rollback-new-plan.json"
        changes_path.write_text(json.dumps(changeset, ensure_ascii=False), encoding="utf-8")

        original_write = wf.write_json
        def fail_state(path, value):
            if Path(path) == root / "state.json":
                raise OSError("simulated late failure")
            return original_write(path, value)
        with patch.object(wf, "write_json", side_effect=fail_state):
            with self.assertRaisesRegex(wf.WorkflowError, "已自动回滚全部文件"):
                wf.apply_panel(root, changes_path, dry_run=False)

        self.assertFalse(animation_plan_path.exists())

    def test_apply_panel_style_change_revokes_all_three_approvals(self):
        """A style change returns the project to the first confirmation gate."""
        root = self.project_dir
        proj_path = root / "project.json"
        before_sha = wf.digest(proj_path)

        proj = wf.read_json(proj_path)
        proj["style_id"] = "minimal-whiteboard"
        proj["style"] = "极简线稿白板"

        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "project",
            "files": [
                {
                    "path": "project.json",
                    "beforeSha256": before_sha,
                    "afterContent": proj,
                    "changesSummary": ["切换风格为极简线稿白板"]
                }
            ]
        }
        c_file = self.temp_dir / "style_changes.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")

        res = wf.apply_panel(root, c_file, dry_run=False)
        self.assertTrue(res["success"])
        self.assertTrue(res["style_changed"])

        _, state = wf.load_project(root)
        self.assertFalse(state["approvals"]["script_style"]["approved"])
        self.assertFalse(state["approvals"]["script_voice"]["approved"])
        self.assertFalse(state["approvals"]["boards"]["approved"])
        self.assertEqual(len(state.get("render_cache", {})), 0, "All render caches must be cleared")

    def test_apply_panel_dry_run_does_not_write_files(self):
        """Verify dry-run mode returns predictions without writing any files or creating backups."""
        root = self.project_dir
        proj_path = root / "project.json"
        before_sha = wf.digest(proj_path)

        proj = wf.read_json(proj_path)
        proj["title"] = "新项目标题"

        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "project",
            "files": [
                {
                    "path": "project.json",
                    "beforeSha256": before_sha,
                    "afterContent": proj,
                    "changesSummary": ["修改标题"]
                }
            ]
        }
        c_file = self.temp_dir / "dry_run_changes.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")

        res = wf.apply_panel(root, c_file, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["files_count"], 1)

        # Ensure project.json untouched
        self.assertEqual(wf.digest(proj_path), before_sha)
        self.assertFalse((root / "panel-backups").exists(), "No backups should be created in dry-run")

    def test_versionless_legacy_plan_requires_explicit_migration(self):
        legacy_plan = {
            "version": 1,
            "scenes": [
                {
                    "sceneId": "scene-01",
                    "sceneStartMs": 0,
                    "sceneEndMs": 3000,
                    "events": []
                }
            ]
        }
        errors = validate_animation_plan(legacy_plan)
        self.assertTrue(any("未知的 animation plan 版本" in item for item in errors))

    def test_formal_sources_remain_strictly_unmodified(self):
        """18: Verify that the opt-in formal-source baseline remains untouched."""
        if os.environ.get("SKETCH_VERIFY_FORMAL_UNTOUCHED") != "1":
            self.skipTest("release isolation audit is opt-in")
        # This is an isolation guard for a work-candidate only. Once this test
        # file is intentionally merged into the formal source tree, the local
        # candidate baseline is absent and the guard must not compare the new
        # release against its pre-merge hashes.
        baseline_file = Path(__file__).parents[2] / "formal-sources-sha256.json"
        if not baseline_file.is_file():
            self.skipTest("candidate isolation baseline is not present")
        expected_hashes = json.loads(baseline_file.read_text(encoding="utf-8"))
        formal_root_raw = os.environ.get("SKETCH_FORMAL_ROOT")
        if not formal_root_raw:
            self.fail("SKETCH_VERIFY_FORMAL_UNTOUCHED=1 时必须设置 SKETCH_FORMAL_ROOT")
        formal_root = Path(formal_root_raw).expanduser().resolve()
        for rel_path, expected_sha in expected_hashes.items():
            actual_file = formal_root / rel_path
            self.assertTrue(actual_file.is_file(), f"Formal file missing: {rel_path}")
            actual_sha = hashlib.sha256(actual_file.read_bytes()).hexdigest()
            self.assertEqual(actual_sha, expected_sha, f"Formal file was modified! {rel_path}")


if __name__ == "__main__":
    unittest.main()
