#!/usr/bin/env python3
"""Comprehensive test suite for SketchNarrator Composition Panel V1 and apply-panel workflow."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from PIL import Image, ImageDraw
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import workflow as wf
from build_panel_assets import build_style_registry_asset


class TestCompositionPanel(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_panel_"))
        self.project_dir = self.temp_dir / "test_project"
        self.setup_synthetic_project(self.project_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def setup_synthetic_project(self, root: Path):
        """Create a complete synthetic 2-scene project."""
        wf.init_project(root, "编排控制台测试", "测试口播与编排", 6, "暖米黄素描白板")

        # Create synthetic audio & script
        script_dir = root / "script"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_text = "因为白板动画，可以清晰表达核心逻辑。观众更容易看懂，所以记忆更深刻。"
        (script_dir / "narration.md").write_text(script_text, encoding="utf-8")

        audio_dir = root / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / "narration.wav").write_bytes(b"RIFFdummywavcontentdata0000")

        words_data = {
            "version": 1,
            "duration_ms": 6000,
            "words": [
                {"text": "因为", "start_ms": 100, "end_ms": 500},
                {"text": "白板动画", "start_ms": 550, "end_ms": 1100},
                {"text": "可以清晰表达", "start_ms": 1150, "end_ms": 1750},
                {"text": "核心逻辑", "start_ms": 1800, "end_ms": 2350},
                # silence between 2350ms and 3350ms: pause budget = 1000ms (>= 820ms)
                {"text": "观众", "start_ms": 3350, "end_ms": 3850},
                {"text": "更容易看懂", "start_ms": 3900, "end_ms": 4600},
                {"text": "所以", "start_ms": 4700, "end_ms": 5100},
                {"text": "记忆更深刻", "start_ms": 5150, "end_ms": 5850},
            ]
        }
        (audio_dir / "words.json").write_text(json.dumps(words_data, ensure_ascii=False, indent=2), encoding="utf-8")

        srt_text = (
            "1\n00:00:00,100 --> 00:00:02,350\n因为白板动画，可以清晰表达核心逻辑。\n\n"
            "2\n00:00:03,350 --> 00:00:05,850\n观众更容易看懂，所以记忆更深刻。\n"
        )
        (audio_dir / "captions.srt").write_text(srt_text, encoding="utf-8-sig")

        storyboard_data = {
            "version": 3,
            "scenes": [
                {
                    "id": "scene-01",
                    "title": "表达逻辑",
                    "narration": "因为白板动画，可以清晰表达核心逻辑。",
                    "start_ms": 0,
                    "end_ms": 3350,
                    "composition": "causal-chain",
                    "elements": [{"id":"scene-01_e1", "trigger_text":"因为", "label": "白板框架", "role": "铺垫"}, {"id":"scene-01_e2", "trigger_text":"核心逻辑", "label": "逻辑关系", "role": "核心"}]
                },
                {
                    "id": "scene-02",
                    "title": "加深记忆",
                    "narration": "观众更容易看懂，所以记忆更深刻。",
                    "start_ms": 3350,
                    "end_ms": 6000,
                    "composition": "center-spoke",
                    "elements": [{"id":"scene-02_e1", "trigger_text":"观众", "label": "观众角色", "role": "主体"}, {"id":"scene-02_e2", "trigger_text":"记忆更深刻", "label": "记忆大脑", "role": "结果"}]
                }
            ]
        }
        (root / "storyboard.json").write_text(json.dumps(storyboard_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # Fake stage script voice
        project, state = wf.load_project(root)
        project["scenes"] = storyboard_data["scenes"]
        state["approvals"]["script_voice"] = {"approved": True, "at": wf.now()}

        # Create boards & annotations
        boards_dir = root / "boards"
        ann_dir = root / "annotations"
        boards_dir.mkdir(parents=True, exist_ok=True)
        ann_dir.mkdir(parents=True, exist_ok=True)

        for sid, dur in [("scene-01", 3350), ("scene-02", 2650)]:
            board = Image.new("RGB", (1920,1080), "white")
            ImageDraw.Draw(board).rectangle((300,200,500,400), fill="black")
            board.save(boards_dir / f"{sid}.png")
            ann = {
                "sceneId": sid,
                "canvas": {"width": 1920, "height": 1080},
                "sceneDurationMs": dur,
                "drawingPlan": {"version": 1, "mode": "layered", "colorReserveRatio": 0.32, "minimumColorMs": 900},
                "elements": [
                    {
                        "id": f"{sid}_e1", "label": "元素1", "sequence": 1, "narrativeRole": "铺垫",
                        "region": {"x": 100, "y": 100, "width": 500, "height": 600},
                        "reveal": {"direction": "top_to_bottom", "startMs": 100, "durationMs": 800, "maskPaddingPx": 22, "protectedRegions": []}
                    },
                    {
                        "id": f"{sid}_e2", "label": "元素2", "sequence": 2, "narrativeRole": "核心",
                        "region": {"x": 800, "y": 100, "width": 600, "height": 600},
                        "reveal": {"direction": "top_to_bottom", "startMs": 950, "durationMs": 850, "maskPaddingPx": 22, "protectedRegions": []}
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
            state.setdefault("render_cache", {})[sid] = {
                "key": f"key_{sid}", "sha256": f"sha_{sid}"
            }

        # Create animation plan using build_animation_plan for 100% V3.3 compliance
        from animation_plan import build_animation_plan
        from visual_director import build_visual_plan
        v_plan = build_visual_plan(project, words_data.get("words", []), root)
        anim_plan = build_animation_plan(
            project,
            {
                "scene-01": wf.read_json(ann_dir / "scene-01.annotation.json"),
                "scene-02": wf.read_json(ann_dir / "scene-02.annotation.json"),
            },
            words_data.get("words", []),
            visual_plan=v_plan,
        )
        anim_file = root / "animation-plan.json"
        anim_file.write_text(json.dumps(anim_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        state.setdefault("artifacts", {})["animation_plan"] = {
            "path": "animation-plan.json",
            "sha256": wf.digest(anim_file)
        }

        state["approvals"]["boards"] = {"approved": True, "at": wf.now()}
        state["final_current"] = True
        state["final_sha256"] = "final_hash_123"
        state["qa"] = {"ok": True, "accepted": True}

        wf.refresh_stage(root, project, state)
        wf.write_json(root / "project.json", project)
        wf.write_json(root / "state.json", state)

    def test_style_registry_build_and_match(self):
        """Verify build_style_registry_asset compiles official references/style-registry.json with valid SHA256."""
        reg_file = Path(__file__).parents[1] / "references" / "style-registry.json"
        out_js = self.temp_dir / "style-registry-data.js"
        res = build_style_registry_asset(reg_file, out_js)
        self.assertTrue(out_js.is_file())
        self.assertEqual(res["sha256"], wf.digest(reg_file))
        first_bytes = out_js.read_bytes()
        build_style_registry_asset(reg_file, out_js)
        self.assertEqual(first_bytes, out_js.read_bytes(), "Repeated builds must be byte-identical")
        self.assertNotIn(b"generatedAt", first_bytes)

        # Check the full public style set is in the generated registry.
        registry = wf.read_json(reg_file)
        style_ids = [s["id"] for s in registry.get("styles", [])]
        expected_ids = [
            "warm-pencil", "minimal-whiteboard", "orderly-color-doodle", "business-doodle",
            "dark-chalkboard", "guofeng-flat", "comic-ink", "paper-metaphor-collage",
            "retro-newspaper", "black-gold-tech",
        ]
        self.assertEqual(len(style_ids), len(expected_ids))
        for expected in expected_ids:
            self.assertIn(expected, style_ids)

    def test_panel_uses_generated_style_source_and_all_real_gates(self):
        skill_root = Path(__file__).parents[1]
        panel_file = skill_root / "renderer" / "assets" / "panel.html"
        panel = panel_file.read_text(encoding="utf-8")
        self.assertNotIn("Fallback if style-registry-data.js", panel)
        self.assertNotIn('"id": "warm-pencil"', panel)
        self.assertEqual(panel.count("checks.push({"), 15)
        self.assertIn("整板图加载完整性", panel)
        self.assertIn("async function loadProjectFromFiles(files)", panel)
        self.assertIn("async function loadLegacyFromFiles(files)", panel)
        self.assertIn("changedAnimationSceneIds", panel)
        self.assertIn("synchronizeTransitionCopies", panel)
        self.assertIn("转场两份存储严格同步", panel)
        self.assertIn("动画晚于目标绘制并保留稳定窗口", panel)
        self.assertIn("开始/结束时间(ms，幕内)", panel)
        self.assertIn("delete beforeGlobal.transitions", panel)
        self.assertIn("el.region.x = Math.round(base.x + dx)", panel)
        self.assertNotIn("normalizeEditableGeometry", panel)

    def test_apply_panel_rejects_protected_and_unknown_paths(self):
        """Verify apply-panel strictly rejects changes targeting protected files like state.json or audio."""
        root = self.project_dir
        for bad_path in ["state.json", "audio/words.json", "audio/captions.srt", "boards/scene-01.png", "deliverables/final.mp4"]:
            changeset = {
                "version": 1,
                "projectFingerprint": wf.project_fingerprint(root),
                "projectRootId": "test_project",
                "files": [
                    {
                        "path": bad_path,
                        "beforeSha256": "any_sha",
                        "afterContent": {},
                        "changesSummary": ["非法修改"]
                    }
                ]
            }
            c_file = self.temp_dir / "bad_changes.json"
            c_file.write_text(json.dumps(changeset), encoding="utf-8")
            with self.assertRaises(wf.WorkflowError) as ctx:
                wf.apply_panel(root, c_file, dry_run=False)
            self.assertIn("禁止通过变更集修改", str(ctx.exception))

    def test_apply_panel_saves_unsynchronized_transition_copies_for_later_review(self):
        root = self.project_dir
        anim_path = root / "animation-plan.json"
        plan = wf.read_json(anim_path)
        self.assertTrue(plan.get("transitions"))
        plan["transitions"][0]["eraseStartMs"] += 1
        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "test_project",
            "files": [{
                "path": "animation-plan.json",
                "beforeSha256": wf.digest(anim_path),
                "afterContent": plan,
                "changesSummary": ["制造转场副本不一致"],
            }],
        }
        changes_file = self.temp_dir / "bad_transition_copy.json"
        changes_file.write_text(json.dumps(changeset, ensure_ascii=False), encoding="utf-8")
        result = wf.apply_panel(root, changes_file, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["invalidation_predicted"]["animation_plan_changed"])
        self.assertEqual(result["invalidation_predicted"]["production_next_action"], "rebuild-plan")

    def test_transition_index_change_invalidates_only_originating_scene(self):
        before = wf.read_json(self.project_dir / "animation-plan.json")
        after = json.loads(json.dumps(before, ensure_ascii=False))
        scene_transition = after["scenes"][0]["transition"]
        global_transition = after["transitions"][0]
        for transition in (scene_transition, global_transition):
            transition["eraseStartMs"] += 20
            transition["stableHoldMs"] += 20
        self.assertEqual(wf.animation_scene_ids_changed(before, after), ["scene-01"])

    def test_storyboard_semantic_diff_reports_object_changes_but_ignores_order(self):
        before = {
            "scenes": [{"id": "scene-01", "elements": [
                {"id": "a", "label": "甲", "trigger_text": "甲", "sequence": 1},
                {"id": "b", "label": "乙", "trigger_text": "乙", "sequence": 2},
            ]}]
        }
        reordered = {
            "scenes": [{"id": "scene-01", "elements": [
                {"id": "b", "label": "乙", "trigger_text": "乙", "sequence": 1},
                {"id": "a", "label": "甲", "trigger_text": "甲", "sequence": 2},
            ]}]
        }
        self.assertFalse(wf.storyboard_semantic_diff(before, reordered)["changed"])

        changed = json.loads(json.dumps(reordered, ensure_ascii=False))
        changed["scenes"][0]["elements"] = [
            {"id": "a", "label": "甲改", "trigger_text": "甲", "sequence": 1},
            {"id": "c", "label": "丙", "trigger_text": "丙", "sequence": 2},
        ]
        diff = wf.storyboard_semantic_diff(before, changed)
        self.assertTrue(diff["changed"])
        self.assertEqual((diff["before_count"], diff["after_count"]), (2, 2))
        self.assertEqual(diff["scenes"][0]["added_ids"], ["c"])
        self.assertEqual(diff["scenes"][0]["removed_ids"], ["b"])
        self.assertEqual(diff["scenes"][0]["changed_ids"], ["a"])

    def test_apply_panel_rejects_duplicate_file_entries(self):
        """Verify apply-panel rejects duplicate file entries in changeset."""
        root = self.project_dir
        ann_path = root / "annotations" / "scene-01.annotation.json"
        ann_sha = wf.digest(ann_path)
        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "test_project",
            "files": [
                {
                    "path": "annotations/scene-01.annotation.json",
                    "beforeSha256": ann_sha,
                    "afterContent": {"sceneId": "scene-01", "canvas": {"width": 1920, "height": 1080}, "elements": []},
                    "changesSummary": ["修改1"]
                },
                {
                    "path": "annotations/scene-01.annotation.json",
                    "beforeSha256": ann_sha,
                    "afterContent": {"sceneId": "scene-01", "canvas": {"width": 1920, "height": 1080}, "elements": []},
                    "changesSummary": ["修改2"]
                }
            ]
        }
        c_file = self.temp_dir / "dup_changes.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")
        with self.assertRaises(wf.WorkflowError) as ctx:
            wf.apply_panel(root, c_file, dry_run=False)
        self.assertIn("包含重复文件条目", str(ctx.exception))

    def test_apply_panel_rejects_missing_beforesha_and_saves_mismatch_as_conflict(self):
        """Missing version evidence is invalid; stale evidence saves a conflict copy."""
        root = self.project_dir
        # 1. Missing beforeSha256 on existing file
        changeset1 = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "test_project",
            "files": [
                {
                    "path": "annotations/scene-01.annotation.json",
                    "beforeSha256": None,
                    "afterContent": {"sceneId": "scene-01", "canvas": {"width": 1920, "height": 1080}, "elements": []},
                    "changesSummary": ["无哈希"]
                }
            ]
        }
        c_file1 = self.temp_dir / "c1.json"
        c_file1.write_text(json.dumps(changeset1), encoding="utf-8")
        with self.assertRaises(wf.WorkflowError) as ctx:
            wf.apply_panel(root, c_file1, dry_run=False)
        self.assertIn("已存在文件缺少 beforeSha256", str(ctx.exception))

        # 2. Mismatched beforeSha256
        changeset2 = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "test_project",
            "files": [
                {
                    "path": "annotations/scene-01.annotation.json",
                    "beforeSha256": "wrong_sha256_1234567890abcdef",
                    "afterContent": {"sceneId": "scene-01", "canvas": {"width": 1920, "height": 1080}, "elements": []},
                    "changesSummary": ["错哈希"]
                }
            ]
        }
        c_file2 = self.temp_dir / "c2.json"
        c_file2.write_text(json.dumps(changeset2), encoding="utf-8")
        original = wf.read_json(root / "annotations" / "scene-01.annotation.json")
        result = wf.apply_panel(root, c_file2, dry_run=False)
        self.assertTrue(result["conflict"])
        self.assertEqual(result["applied_files"], [])
        self.assertEqual(wf.read_json(root / "annotations" / "scene-01.annotation.json"), original)
        self.assertTrue((root / result["saved_copy"]).is_file())

    def test_apply_panel_stale_project_fingerprint_saves_conflict_copy(self):
        root = self.project_dir
        ann_path = root / "annotations" / "scene-01.annotation.json"
        ann_sha = wf.digest(ann_path)
        changeset = {
            "version": 1,
            "projectFingerprint": "wrong_fingerprint_hash:scene-01:3",
            "projectRootId": "test_project",
            "files": [
                {
                    "path": "annotations/scene-01.annotation.json",
                    "beforeSha256": ann_sha,
                    "afterContent": {"sceneId": "scene-01", "canvas": {"width": 1920, "height": 1080}, "elements": []},
                    "changesSummary": ["测试指纹"]
                }
            ]
        }
        c_file = self.temp_dir / "fingerprint_changes.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")
        result = wf.apply_panel(root, c_file, dry_run=False)
        self.assertTrue(result["conflict"])
        self.assertEqual(result["applied_files"], [])
        self.assertTrue((root / result["saved_copy"]).is_file())

    def test_apply_panel_requires_project_fingerprint(self):
        """A change set without a project fingerprint must never be accepted."""
        root = self.project_dir
        ann_path = root / "annotations" / "scene-01.annotation.json"
        changeset = {
            "version": 1,
            "files": [{
                "path": "annotations/scene-01.annotation.json",
                "beforeSha256": wf.digest(ann_path),
                "afterContent": wf.read_json(ann_path),
                "changesSummary": ["缺少项目指纹"]
            }]
        }
        c_file = self.temp_dir / "missing_fingerprint.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")
        with self.assertRaises(wf.WorkflowError) as ctx:
            wf.apply_panel(root, c_file, dry_run=False)
        self.assertIn("缺少有效的 projectFingerprint", str(ctx.exception))

    def test_apply_panel_rollback_on_late_state_write_failure(self):
        """A failure after committed edits restores the exact earlier files."""
        root = self.project_dir
        ann_path = root / "annotations" / "scene-01.annotation.json"
        before_ann_sha = wf.digest(ann_path)
        proj_path = root / "project.json"
        before_proj_sha = wf.digest(proj_path)

        changed_annotation = wf.read_json(ann_path)
        changed_annotation["elements"][0]["label"] = "已修改元素"
        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "test_project",
            "files": [
                {
                    "path": "annotations/scene-01.annotation.json",
                    "beforeSha256": before_ann_sha,
                    "afterContent": changed_annotation,
                    "changesSummary": ["有效修改"]
                },
            ]
        }
        c_file = self.temp_dir / "invalid_changes.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")

        original_write_json = wf.write_json
        def fail_state(path, value):
            if Path(path).name == "state.json":
                raise RuntimeError("injected state write failure")
            return original_write_json(path, value)

        with mock.patch.object(wf, "write_json", side_effect=fail_state):
            with self.assertRaises(wf.WorkflowError):
                wf.apply_panel(root, c_file, dry_run=False)

        # Verify 100% untouched
        self.assertEqual(wf.digest(ann_path), before_ann_sha)
        self.assertEqual(wf.digest(proj_path), before_proj_sha)

    def test_apply_panel_rollback_removes_new_file_after_commit_failure(self):
        """A post-commit failure restores existing files and removes newly created targets."""
        root = self.project_dir
        anim_path = root / "animation-plan.json"
        new_anim_plan = wf.read_json(anim_path)
        anim_path.unlink()
        state_path = root / "state.json"
        before_state_sha = wf.digest(state_path)
        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "files": [{
                "path": "animation-plan.json",
                "beforeSha256": None,
                "afterContent": new_anim_plan,
                "changesSummary": ["创建动画计划"]
            }]
        }
        c_file = self.temp_dir / "rollback_new_file.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")
        original_write_json = wf.write_json
        def fail_state(path, value):
            if Path(path).name == "state.json":
                raise RuntimeError("injected state write failure")
            return original_write_json(path, value)

        with mock.patch.object(wf, "write_json", side_effect=fail_state):
            with self.assertRaises(wf.WorkflowError) as ctx:
                wf.apply_panel(root, c_file, dry_run=False)
        self.assertIn("已自动回滚全部文件", str(ctx.exception))
        self.assertFalse(anim_path.exists(), "New target must be removed during rollback")
        self.assertEqual(before_state_sha, wf.digest(state_path))

    def test_apply_panel_animation_change_returns_to_third_approval(self):
        """A changed execution plan invalidates its previous board/order approval."""
        root = self.project_dir
        anim_path = root / "animation-plan.json"
        before_anim_sha = wf.digest(anim_path)

        new_anim_plan = wf.read_json(anim_path)
        new_anim_plan["scenes"][0]["panelNote"] = "仅修改第一幕的语义强调说明"

        changeset = {
            "version": 1,
            "projectFingerprint": wf.project_fingerprint(root),
            "projectRootId": "test_project",
            "files": [
                {
                    "path": "animation-plan.json",
                    "beforeSha256": before_anim_sha,
                    "afterContent": new_anim_plan,
                    "changesSummary": ["更新动画计划"]
                }
            ]
        }
        c_file = self.temp_dir / "anim_changes.json"
        c_file.write_text(json.dumps(changeset), encoding="utf-8")

        res = wf.apply_panel(root, c_file, dry_run=False)
        self.assertTrue(res["success"])

        _, state = wf.load_project(root)
        self.assertFalse(state["approvals"]["boards"]["approved"])
        self.assertEqual(state["stage"], "await-boards-approval")
        self.assertFalse(state["final_current"], "final_current must be cleared for re-rendering")
        self.assertNotIn("scene-01", state.get("render_cache", {}))
        self.assertIn("scene-02", state.get("render_cache", {}), "Unchanged scene cache must be preserved")
        self.assertEqual(res["animation_scenes_changed"], ["scene-01"])


if __name__ == "__main__":
    unittest.main()
