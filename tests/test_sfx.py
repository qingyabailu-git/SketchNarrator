from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np
import av
from unittest.mock import patch


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SOURCE_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sfx  # noqa: E402
import workflow  # noqa: E402
import compose_final  # noqa: E402


class SfxTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict, dict, dict]:
        (root / "annotations").mkdir(parents=True)
        annotation = {
            "sceneId": "scene-01",
            "elements": [
                {
                    "id": "subject",
                    "label": "核心疑问",
                    "narrativeRole": "提出问题",
                    "sequence": 1,
                    "reveal": {"startMs": 100, "durationMs": 900},
                },
                {
                    "id": "detail",
                    "label": "正确答案",
                    "sequence": 2,
                    "reveal": {"startMs": 1100, "durationMs": 700},
                },
            ],
        }
        (root / "annotations" / "scene-01.annotation.json").write_text(
            json.dumps(annotation), encoding="utf-8"
        )
        project = {
            "version": 3,
            "sfx_profile": {
                "enabled": True,
                "preset": "classic-light",
                "use_local_default": False,
            },
            "scenes": [
                {"id": "scene-01", "start_ms": 0, "end_ms": 2500},
                {"id": "scene-02", "start_ms": 2500, "end_ms": 5000},
            ],
        }
        state = {
            "boards": {
                "scene-01": {"annotation": "annotations/scene-01.annotation.json"}
            }
        }
        animation = {
            "scenes": [
                {
                    "sceneId": "scene-01",
                    "sceneStartMs": 0,
                    "sceneEndMs": 2500,
                    "events": [
                        {
                            "effect": "contour-indicate",
                            "semanticIntent": "conclusion",
                            "targetElementId": "detail",
                            "startMs": 1850,
                            "endMs": 2250,
                        }
                    ],
                },
                {
                    "sceneId": "scene-02",
                    "sceneStartMs": 2500,
                    "sceneEndMs": 5000,
                    "events": [],
                },
            ],
            "transitions": [
                {
                    "fromSceneId": "scene-01",
                    "toSceneId": "scene-02",
                    "status": "ready",
                    "eraseStartMs": 2100,
                    "eraseEndMs": 2440,
                    "cleanCanvasEndMs": 2500,
                }
            ],
        }
        return project, state, animation

    def _semantic_manifest(self, target: Path, manifest_id: str = "private-semantic") -> Path:
        target.mkdir(parents=True)
        source = SOURCE_ROOT / "assets" / "sfx" / "classic-light"
        source_files = {
            "writing": "writing-pencil.wav",
            "eraser": "eraser-rub.wav",
            "transition": "transition-whoosh.wav",
            "emphasis": "emphasis-pop.wav",
            "conclusion": "conclusion-chime.wav",
            "question": "emphasis-pop.wav",
            "reveal": "emphasis-pop.wav",
            "warning": "emphasis-pop.wav",
            "success": "conclusion-chime.wav",
        }
        effects = {}
        for effect, source_name in source_files.items():
            filename = f"{effect}.wav"
            shutil.copy2(source / source_name, target / filename)
            effects[effect] = {"file": filename, "gain_db": -24.0, "fade_ms": 20}
        manifest = target / "manifest.json"
        manifest.write_text(json.dumps({
            "version": 1,
            "id": manifest_id,
            "sample_rate": 48000,
            "effects": effects,
        }), encoding="utf-8")
        return manifest

    def test_new_projects_enable_deterministic_classic_light(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            workflow.init_project(root, "测试", "测试主题", 20, "auto")
            project = json.loads((root / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["sfx_profile"]["enabled"], True)
            self.assertEqual(project["sfx_profile"]["preset"], "classic-light")
            self.assertEqual(project["sfx_profile"]["mode"], "deterministic")
            if "manifest_path" in project["sfx_profile"]:
                self.assertTrue(Path(project["sfx_profile"]["manifest_path"]).is_file())

    def test_plan_follows_reveal_animation_and_transition_times(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, state, animation = self._fixture(root)
            plan, preset_root = sfx.build_plan(
                root, project, state, animation, 5000, SOURCE_ROOT
            )
            self.assertIsNotNone(preset_root)
            effects = [event["effect"] for event in plan["events"]]
            self.assertEqual(effects.count("writing"), 2)
            # The built-in five-role preset remains compatible and falls back
            # from an optional question cue to the generic emphasis sound.
            self.assertIn("emphasis", effects)
            self.assertIn("conclusion", effects)
            self.assertIn("eraser", effects)
            self.assertIn("transition", effects)
            self.assertTrue(all(0 <= event["start_ms"] < event["end_ms"] <= 5000 for event in plan["events"]))

    def test_rendered_track_has_exact_duration_and_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, state, animation = self._fixture(root)
            plan, preset_root = sfx.build_plan(
                root, project, state, animation, 5000, SOURCE_ROOT
            )
            assert preset_root is not None
            output = root / "audio" / "sfx-track.wav"
            result = sfx.render_track(plan, preset_root, output)
            self.assertEqual(result["events"], len(plan["events"]))
            with wave.open(str(output), "rb") as stream:
                self.assertEqual(stream.getframerate(), 48000)
                self.assertEqual(stream.getnchannels(), 1)
                self.assertEqual(stream.getnframes(), 240000)
                samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2")
            self.assertGreater(int(np.max(np.abs(samples))), 0)

    def test_short_loop_uses_audible_source_window(self) -> None:
        source = np.zeros(48000, dtype=np.float32)
        source[36000:] = 0.5

        segment = sfx._loop_segment(source, 4800)

        self.assertGreater(float(np.mean(np.abs(segment))), 0.49)

    def test_old_project_without_profile_stays_silent(self) -> None:
        plan, preset_root = sfx.build_plan(
            Path("."), {"version": 2, "scenes": []}, {"boards": {}},
            {"scenes": [], "transitions": []}, 1000, SOURCE_ROOT
        )
        self.assertFalse(plan["enabled"])
        self.assertEqual(plan["events"], [])
        self.assertIsNone(preset_root)

    def test_external_local_manifest_can_supply_private_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, state, animation = self._fixture(root)
            private = root / "private-sfx"
            private.mkdir()
            source = SOURCE_ROOT / "assets" / "sfx" / "classic-light"
            effects = {}
            for effect, filename in {
                "writing": "writing-pencil.wav",
                "eraser": "eraser-rub.wav",
                "transition": "transition-whoosh.wav",
                "emphasis": "emphasis-pop.wav",
                "conclusion": "conclusion-chime.wav",
            }.items():
                shutil.copy2(source / filename, private / filename)
                effects[effect] = {
                    "file": filename,
                    "gain_db": -24.0,
                    "loop": effect in {"writing", "eraser"},
                    "fade_ms": 20,
                }
            (private / "manifest.json").write_text(
                json.dumps({
                    "version": 1,
                    "id": "private-local",
                    "license": "user-provided",
                    "sample_rate": 48000,
                    "effects": effects,
                }),
                encoding="utf-8",
            )
            project["sfx_profile"] = {
                "enabled": True,
                "preset": "private-local",
                "manifest_path": "private-sfx/manifest.json",
            }
            plan, preset_root = sfx.build_plan(
                root, project, state, animation, 5000, SOURCE_ROOT
            )
            self.assertEqual(preset_root, private.resolve())
            self.assertEqual(plan["preset"], "private-local")
            self.assertEqual(plan["source"], "external-local")
            self.assertGreater(len(plan["events"]), 0)

    def test_optional_semantic_roles_use_dedicated_assets_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, state, animation = self._fixture(root)
            private = root / "private-sfx"
            manifest = self._semantic_manifest(private)
            project["sfx_profile"] = {
                "enabled": True,
                "manifest_path": str(manifest),
            }
            animation["scenes"][0]["events"].extend([
                {"effect": "highlight-wash", "semanticIntent": "warning", "startMs": 200},
                {"effect": "focus-push", "semanticIntent": "answer", "startMs": 500},
                {"effect": "focus-push", "semanticIntent": "success", "startMs": 800},
            ])
            plan, _ = sfx.build_plan(root, project, state, animation, 5000, SOURCE_ROOT)
            planned = [event["effect"] for event in plan["events"]]
            self.assertIn("question", planned)
            self.assertIn("warning", planned)
            self.assertIn("reveal", planned)
            self.assertIn("success", planned)

    def test_visual_plan_structured_cues_trigger_question_warning_and_reveal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, state, animation = self._fixture(root)
            manifest = self._semantic_manifest(root / "private-sfx")
            project["sfx_profile"] = {
                "enabled": True,
                "manifest_path": str(manifest),
            }
            animation["scenes"][0]["events"] = []
            (root / "visual-plan.json").write_text(json.dumps({
                "version": 1,
                "shots": [{
                    "shot_id": "scene-01-shot-01",
                    "section_id": "scene-01",
                    "template": "question",
                    "start_ms": 100,
                    "beats": [
                        {
                            "start_ms": 1100,
                            "target": "错误分支",
                            "semantic_goal": "风险警告",
                            "trigger_text": "第一位不对系统返回失败",
                        },
                        {
                            "start_ms": 1700,
                            "target": "正确答案",
                            "semantic_goal": "答案揭示",
                            "trigger_text": "现在揭晓正确答案",
                        },
                    ],
                }],
            }), encoding="utf-8")
            plan, _ = sfx.build_plan(root, project, state, animation, 5000, SOURCE_ROOT)
            planned = [event["effect"] for event in plan["events"]]
            self.assertEqual(planned.count("question"), 1)
            self.assertEqual(planned.count("warning"), 1)
            self.assertEqual(planned.count("reveal"), 1)
            question = next(event for event in plan["events"] if event["effect"] == "question")
            self.assertEqual(question["start_ms"], 100)
            self.assertEqual(question["target_id"], "scene-01-shot-01")
            reveal = next(event for event in plan["events"] if event["effect"] == "reveal")
            self.assertEqual(reveal["requested_start_ms"], 1700)
            self.assertEqual(reveal["start_ms"], 1800)
            self.assertEqual(reveal["timing_source"], "target-reveal-end")

    def test_machine_local_default_uses_private_manifest_and_gain_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_root = root / "project"
            skill_root = root / "skill"
            project, state, animation = self._fixture(project_root)
            project["sfx_profile"] = {"enabled": True, "preset": "classic-light"}
            manifest = self._semantic_manifest(root / "private-sfx", "machine-private")
            local = skill_root / ".local"
            local.mkdir(parents=True)
            (local / "settings.json").write_text(json.dumps({
                "version": 1,
                "sfx": {
                    "default_manifest": str(manifest),
                    "gain_db_overrides": {"reveal": -2.0, "question": -15.0},
                },
            }), encoding="utf-8")
            (project_root / "visual-plan.json").write_text(json.dumps({
                "version": 1,
                "shots": [{
                    "shot_id": "scene-01-shot-01",
                    "section_id": "scene-01",
                    "template": "question",
                    "start_ms": 100,
                    "beats": [{
                        "start_ms": 1200,
                        "target": "答案",
                        "sfx_cue": "reveal",
                    }],
                }],
            }), encoding="utf-8")
            with patch.dict("os.environ", {"SKETCHNARRATOR_LOCAL_SETTINGS": str(local / "settings.json")}):
                plan, preset_root = sfx.build_plan(
                    project_root, project, state, animation, 5000, skill_root
                )
            self.assertEqual(plan["source"], "machine-local")
            self.assertEqual(plan["preset"], "machine-private")
            self.assertEqual(preset_root, manifest.parent.resolve())
            reveal = next(event for event in plan["events"] if event["effect"] == "reveal")
            question = next(event for event in plan["events"] if event["effect"] == "question")
            self.assertEqual(reveal["gain_db"], -2.0)
            self.assertEqual(question["gain_db"], -15.0)

    def test_external_manifest_cannot_escape_its_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            private = root / "private-sfx"
            private.mkdir()
            outside = root / "outside.wav"
            shutil.copy2(
                SOURCE_ROOT / "assets" / "sfx" / "classic-light" / "writing-pencil.wav",
                outside,
            )
            effects = {
                effect: {"file": "../outside.wav"}
                for effect in sfx.SUPPORTED_EFFECTS
            }
            manifest = private / "manifest.json"
            manifest.write_text(json.dumps({
                "version": 1,
                "id": "unsafe",
                "sample_rate": 48000,
                "effects": effects,
            }), encoding="utf-8")
            with self.assertRaises(sfx.SfxError):
                sfx.load_manifest(SOURCE_ROOT, "unsafe", manifest)

    def test_pyav_fallback_mixes_optional_sfx_track(self) -> None:
        asset = SOURCE_ROOT / "assets" / "sfx" / "classic-light" / "emphasis-pop.wav"
        reader = compose_final.SfxPcmReader(asset)
        try:
            narration = av.AudioFrame.from_ndarray(
                np.zeros((2, 1024), dtype=np.float32), format="fltp", layout="stereo"
            )
            narration.sample_rate = 48000
            mixed = compose_final.mix_sfx(narration, reader)
            self.assertGreater(float(np.max(np.abs(mixed.to_ndarray()))), 0.0)
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
