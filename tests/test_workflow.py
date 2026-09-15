from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import wave
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "workflow.py"
SPEC = importlib.util.spec_from_file_location("workflow", MODULE_PATH)
workflow = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(workflow)

TTS_PATH = Path(__file__).parents[1] / "scripts" / "tts_elevenlabs.py"
sys_path_added = str(TTS_PATH.parent)
sys.path.insert(0, sys_path_added)
TTS_SPEC = importlib.util.spec_from_file_location("tts_elevenlabs", TTS_PATH)
tts_elevenlabs = importlib.util.module_from_spec(TTS_SPEC)
assert TTS_SPEC.loader
TTS_SPEC.loader.exec_module(tts_elevenlabs)


class WorkflowTests(unittest.TestCase):
    def test_multiscene_diagnostics_keep_global_word_anchors(self) -> None:
        words = [
            {"text": "先说", "start_ms": 0, "end_ms": 900},
            {"text": "再说", "start_ms": 1000, "end_ms": 1900},
        ]
        scenes = [
            {
                "id": "scene-01",
                "narration": "先说",
                "start_ms": 0,
                "end_ms": 900,
                "elements": [{"id": "first", "label": "第一幕", "trigger_text": "先说"}],
            },
            {
                "id": "scene-02",
                "narration": "再说",
                "start_ms": 1000,
                "end_ms": 1900,
                "elements": [{"id": "second", "label": "第二幕"}],
                "visual_beats": [{"trigger_word_id": "w-0002", "target": "second"}],
            },
        ]
        project = {"version": 3, "scenes": scenes}

        workflow.validate_visual_scenes_together(project, scenes, words, Path.cwd())

    def test_annotation_semantic_ids_are_reconciled_without_user_storyboard_edits(self) -> None:
        scene = {
            "id": "scene-01",
            "elements": [
                {"id": "visual-a", "label": "静止屏幕", "trigger_text": "眼睛看到的"},
                {"id": "visual-b", "label": "运动耳朵", "trigger_text": "耳朵感受到的"},
            ],
        }
        annotation = {
            "elements": [
                {"id": "old-1", "label": "运动耳朵", "sequence": 1},
                {"id": "old-2", "label": "静止屏幕", "sequence": 2},
            ]
        }

        changed = workflow.reconcile_annotation_semantic_ids(annotation, scene, migrate=True)

        self.assertTrue(changed)
        self.assertEqual([item["id"] for item in annotation["elements"]], ["visual-b", "visual-a"])
        self.assertEqual(
            [item["sourceElementIds"] for item in annotation["elements"]],
            [["visual-b"], ["visual-a"]],
        )

    def test_annotation_semantic_count_mismatch_is_producer_side(self) -> None:
        scene = {"id": "scene-01", "elements": [{"id": "visual-a"}]}
        annotation = {"elements": [{"id": "visual-a"}, {"id": "extra"}]}

        with self.assertRaisesRegex(workflow.WorkflowError, "制作层同步.*无需编辑分镜"):
            workflow.reconcile_annotation_semantic_ids(annotation, scene, migrate=True)

    def test_panel_storyboard_sync_accepts_added_visual_element_without_timing_edit(self) -> None:
        current = {
            "scenes": [{
                "id": "scene-01",
                "title": "测试",
                "narration": "测试口播",
                "start_ms": 0,
                "end_ms": 1000,
                "composition": "center-spoke",
                "elements": [{"id": "visual-a", "label": "旧元素", "trigger_text": "测试"}],
            }]
        }
        candidate = {
            "scenes": [{
                **current["scenes"][0],
                "elements": [
                    {"id": "visual-a", "label": "旧元素", "trigger_text": "测试"},
                    {"id": "visual-2", "label": "新增元素", "trigger_text": "新增"},
                ],
            }]
        }

        workflow.validate_panel_storyboard(candidate, current, {"version": 3})

    def test_storyboard_rejects_scene_ids_that_can_escape_project_directories(self) -> None:
        storyboard = {
            "scenes": [{
                "id": "../outside",
                "narration": "测试",
                "start_ms": 0,
                "end_ms": 1000,
                "elements": [{"label": "主体"}],
            }]
        }
        with self.assertRaisesRegex(workflow.WorkflowError, "场景 id 不安全"):
            workflow.validate_storyboard(storyboard)

    def test_init_defaults_to_auto_style_and_edge_voice(self) -> None:
        parser = workflow.build_parser()
        args = parser.parse_args(["init", "--project", "demo", "--title", "为什么没有炸鸭", "--topic", "生活食物科普"])
        self.assertEqual(args.style, "auto")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "auto"
            workflow.init_project(root, args.title, args.topic, 60, args.style)
            project = workflow.read_json(root / "project.json")
            self.assertEqual(project["style_selection"]["mode"], "auto")
            self.assertTrue(project["style_selection"]["requires_confirmation"])
            self.assertEqual(project["voice_selection"]["provider"], "edge")
            self.assertFalse(project["voice_selection"]["alternative_auditions_generated"])

    def test_style_recommendation_can_be_overridden(self) -> None:
        recommendation = workflow.recommend_style("传统诗词里的月亮", "古诗为什么爱写月亮")
        self.assertEqual(recommendation["style_id"], "guofeng-flat")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "manual"
            workflow.init_project(root, "手动选择", "传统诗词", 60, "minimal-whiteboard")
            project = workflow.read_json(root / "project.json")
            self.assertEqual(project["style_id"], "minimal-whiteboard")
            self.assertEqual(project["style_selection"]["mode"], "user")

    def test_panel_command_defaults_to_localhost_and_auto_open(self) -> None:
        args = workflow.build_parser().parse_args(["panel", "--project", "demo"])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 0)
        self.assertFalse(args.no_open)

    def test_public_tts_command_uses_edge_defaults(self) -> None:
        args = workflow.build_parser().parse_args([
            "tts", "--project", "demo", "--script", "narration.md", "--out-dir", "audio"
        ])
        self.assertEqual(args.provider, "edge")
        self.assertIsNone(args.voice)
        self.assertEqual(args.rate, "+12%")

    def test_public_tts_command_exposes_optional_providers(self) -> None:
        parser = workflow.build_parser()
        for provider in ("piper", "azure", "elevenlabs", "sherpa", "voicestudio"):
            args = parser.parse_args([
                "tts", "--project", "demo", "--script", "narration.md",
                "--out-dir", "audio", "--provider", provider,
            ])
            self.assertEqual(args.provider, provider)

    def test_offline_or_local_voice_can_use_public_alignment_command(self) -> None:
        args = workflow.build_parser().parse_args([
            "align-voice", "--project", "demo", "--audio", "voice.wav",
            "--script", "narration.md", "--out-dir", "audio",
        ])
        self.assertEqual(args.command, "align-voice")
        self.assertEqual(args.model, "small")

    def test_setup_prepares_alignment_for_local_voice_providers(self) -> None:
        for provider in ("piper", "sherpa", "voicestudio"):
            with self.subTest(provider=provider), patch.object(
                workflow.subprocess, "run", return_value=Namespace(returncode=0)
            ) as run:
                code = workflow.prepare_optional_environment(
                    Namespace(provider=[provider], extraction=False, install_ffmpeg=False)
                )
                command = run.call_args.args[0]
                self.assertEqual(code, 0)
                self.assertIn("--install-alignment", command)
                self.assertNotIn("--install-extraction", command)
                self.assertNotIn("--install-ffmpeg", command)

        for provider in ("azure", "elevenlabs"):
            with self.subTest(provider=provider), patch.object(
                workflow.subprocess, "run", return_value=Namespace(returncode=0)
            ) as run:
                workflow.prepare_optional_environment(
                    Namespace(provider=[provider], extraction=False, install_ffmpeg=False)
                )
            self.assertNotIn("--install-alignment", run.call_args.args[0])

    def test_source_extraction_prepares_only_extraction_dependencies(self) -> None:
        skill_root = Path("/portable/sketch-narrator/renderer")
        renderer_python = Path("/portable/sketch-narrator/renderer/.venv/bin/python")
        with patch.object(workflow, "renderer_root", return_value=skill_root), patch.object(
            workflow, "renderer_python", return_value=renderer_python
        ) as select_python, patch.object(
            workflow.subprocess, "run", return_value=Namespace(returncode=0, stdout="", stderr="")
        ) as run:
            selected = workflow.prepare_extraction_environment()
        command = run.call_args.args[0]
        self.assertEqual(selected, renderer_python)
        self.assertEqual(command[-1], "--install-extraction")
        self.assertNotIn("--install-ffmpeg", command)
        select_python.assert_called_once_with(skill_root, prepare=False)

    def test_extract_source_uses_the_prepared_extraction_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            skill_root = base / "renderer"
            extractor = skill_root / "scripts" / "extract_subtitles.py"
            extractor.parent.mkdir(parents=True)
            extractor.touch()
            workflow.init_project(project, "提取测试", "素材转口播", 30, "minimal-whiteboard")
            args = Namespace(
                input="https://example.test/watch/1",
                model="auto",
                device="cpu",
                source_language="auto",
                force_asr=False,
                asr_python="",
            )
            class FailedProcess:
                stdout = iter(["[err] expected stop\n"])

                @staticmethod
                def wait() -> int:
                    return 1

            with patch.object(workflow, "renderer_root", return_value=skill_root), patch.object(
                workflow, "prepare_extraction_environment", return_value=Path("prepared-python")
            ) as prepare, patch.object(
                workflow.subprocess,
                "Popen",
                return_value=FailedProcess(),
            ) as popen:
                with self.assertRaisesRegex(workflow.WorkflowError, "expected stop"):
                    workflow.extract_source(project, args)
            prepare.assert_called_once_with()
            self.assertEqual(popen.call_args.args[0][0], "prepared-python")
            state = workflow.read_json(project / "state.json")
            self.assertEqual(state["source_extract"]["status"], "failed")
            self.assertEqual(state["source_extract"]["error"], "[err] expected stop")
            draft = base / "draft.txt"
            draft.write_text("不能跳过失败的参考提取", encoding="utf-8")
            with self.assertRaisesRegex(workflow.WorkflowError, "不能登记推断或代写稿"):
                workflow.stage_script(project, str(draft))

    def test_explicit_user_model_selection_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            skill_root = base / "renderer"
            extractor = skill_root / "scripts" / "extract_subtitles.py"
            extractor.parent.mkdir(parents=True)
            extractor.touch()
            workflow.init_project(project, "模型授权测试", "素材转口播", 30, "minimal-whiteboard")
            args = Namespace(
                input="https://example.test/watch/large-approved",
                reference_scope="transcript",
                model="large-v3-turbo",
                device="cpu",
                source_language="en",
                force_asr=False,
                asr_python="",
            )

            class FailedProcess:
                stdout = iter(["[err] expected stop\n"])

                @staticmethod
                def wait() -> int:
                    return 1

            with patch.object(workflow, "renderer_root", return_value=skill_root), patch.object(
                workflow, "prepare_extraction_environment", return_value=Path("prepared-python")
            ), patch.object(workflow.subprocess, "Popen", return_value=FailedProcess()) as popen:
                with self.assertRaisesRegex(workflow.WorkflowError, "expected stop"):
                    workflow.extract_source(project, args)
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--model") + 1], "large-v3-turbo")

    def test_extract_source_records_complete_package_before_script_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            skill_root = base / "renderer"
            extractor = skill_root / "scripts" / "extract_subtitles.py"
            extractor.parent.mkdir(parents=True)
            extractor.touch()
            workflow.init_project(project, "提取成功测试", "素材转口播", 30, "minimal-whiteboard")
            args = Namespace(
                input="https://example.test/watch/2",
                model="auto",
                device="cpu",
                source_language="en",
                force_asr=False,
                asr_python="",
            )

            class SuccessfulProcess:
                stdout = iter(["EXTRACT_STAGE=audio-download\n", "MANIFEST=ready\n"])

                @staticmethod
                def wait() -> int:
                    return 0

            launched_command = []

            def launch(command, **_kwargs):
                launched_command.extend(command)
                out_dir = Path(command[command.index("--out-dir") + 1])
                out_dir.mkdir(parents=True, exist_ok=True)
                outputs = {}
                for name, filename in {
                    "text": "script.txt",
                    "srt": "voiceover.srt",
                    "vtt": "voiceover.vtt",
                    "cues": "cues.json",
                }.items():
                    path = out_dir / filename
                    path.write_text("ok\n", encoding="utf-8")
                    outputs[name] = str(path.resolve())
                audio = out_dir / "voiceover.m4a"
                audio.write_bytes(b"audio")
                workflow.write_json(out_dir / "extraction.json", {
                    "transcript_source": "local-asr/current",
                    "requested_language": "en",
                    "transcript_language": "en",
                    "requested_asr_model": "auto",
                    "asr_model": "small",
                    "asr_model_source": "cache",
                    "cached_asr_models": ["small"],
                    "quality": {"status": "passed", "errors": [], "warnings": []},
                    "cue_count": 1,
                    "word_count": 1,
                    "audio_path": str(audio.resolve()),
                    "outputs": outputs,
                    "words_path": None,
                })
                return SuccessfulProcess()

            with patch.object(workflow, "renderer_root", return_value=skill_root), patch.object(
                workflow, "prepare_extraction_environment", return_value=Path("prepared-python")
            ), patch.object(workflow.subprocess, "Popen", side_effect=launch):
                result = workflow.extract_source(project, args)

            self.assertEqual(result["source_extract"]["status"], "awaiting-script-review")
            self.assertEqual(result["source_extract"]["transcript_language"], "en")
            self.assertEqual(result["source_extract"]["asr_model"], "small")
            self.assertEqual(result["source_extract"]["asr_model_source"], "cache")
            language_index = launched_command.index("--source-language")
            self.assertEqual(launched_command[language_index + 1], "en")
            model_index = launched_command.index("--model")
            self.assertEqual(launched_command[model_index + 1], "auto")
            self.assertIn("audio", result["source_extract"]["files"])
            draft = base / "draft.txt"
            draft.write_text("已经核对提取稿", encoding="utf-8")
            workflow.stage_script(project, str(draft))

    def test_stage_script_rejects_extraction_without_passed_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            workflow.init_project(project, "质量门禁测试", "素材转口播", 30, "minimal-whiteboard")
            state = workflow.read_json(project / "state.json")
            state["source_extract"] = {"status": "awaiting-script-review"}
            workflow.write_json(project / "state.json", state)
            draft = Path(temp) / "draft.txt"
            draft.write_text("不能使用未通过质量检查的提取稿", encoding="utf-8")
            with self.assertRaisesRegex(workflow.WorkflowError, "转写质量尚未通过"):
                workflow.stage_script(project, str(draft))

    def test_new_projects_use_semantic_strokes_and_local_colour(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp) / "semantic"
            workflow.init_project(project_root, "语义笔画", "测试主体优先", 10, "warm-pencil")
            project = workflow.read_json(project_root / "project.json")
            self.assertEqual(project["renderer_profile"]["stroke_planner"], "semantic-v2")
            self.assertEqual(project["renderer_profile"]["color_fill"], "local-brush")
            self.assertEqual(project["renderer_profile"]["color_schedule"], "object-progressive-v1")

    def test_annotation_rejects_unknown_stroke_planner(self) -> None:
        annotation = {
            "canvas": {"width": 100, "height": 100},
            "sceneDurationMs": 1000,
            "drawingPlan": {
                "mode": "layered",
                "strokePlanner": "scan-everything",
                "colorReserveRatio": 0.32,
                "minimumColorMs": 900,
            },
            "elements": [
                {"id": '1',
                    "sequence": 1,
                    "region": {"x": 0, "y": 0, "width": 80, "height": 80},
                    "reveal": {"startMs": 0, "durationMs": 500},
                }
            ],
        }
        with self.assertRaises(workflow.WorkflowError):
            workflow.validate_annotation(annotation)

    def test_annotation_rejects_unknown_color_schedule(self) -> None:
        annotation = {
            "canvas": {"width": 100, "height": 100},
            "sceneDurationMs": 1000,
            "drawingPlan": {
                "mode": "layered",
                "strokePlanner": "semantic-v2",
                "colorSchedule": "all-at-the-end",
                "colorReserveRatio": 0.32,
                "minimumColorMs": 900,
            },
            "elements": [{"id": '1',
                "sequence": 1,
                "region": {"x": 0, "y": 0, "width": 80, "height": 80},
                "reveal": {"startMs": 0, "durationMs": 500},
            }],
        }
        with self.assertRaisesRegex(workflow.WorkflowError, "colorSchedule"):
            workflow.validate_annotation(annotation)

    def test_annotation_geometry_rounds_browser_coordinates(self) -> None:
        annotation = {
            "canvas": {"width": 100, "height": 80},
            "sceneDurationMs": 1000,
            "elements": [{"id": '1',
                "sequence": 1,
                "region": {"x": 10.6, "y": -0.4, "width": 95.2, "height": 90.8},
                "reveal": {"startMs": 0, "durationMs": 500},
            }],
        }
        workflow.normalize_annotation_geometry(annotation)
        self.assertEqual(annotation["elements"][0]["region"], {"x": 11, "y": 0, "width": 89, "height": 80})
        workflow.validate_annotation(annotation)

    def test_three_gate_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "demo"
            workflow.init_project(project, "测试视频", "解释价格变化", 10, "chalkboard")
            self.assertEqual(workflow.status(project)["stage"], "prepare-script")
            (project / "characters").mkdir()
            Image.new("RGB", (24, 24), (220, 180, 120)).save(project / "characters" / "host.png")

            inputs = base / "inputs"
            inputs.mkdir()
            (inputs / "script.md").write_text("# 口播稿\n\n因为需求增加。", encoding="utf-8")
            workflow.stage_script(project, str(inputs / "script.md"))
            self.assertEqual(workflow.status(project)["stage"], "await-script-style-approval")
            first_confirmation = workflow.confirmation_bundle(project)
            self.assertEqual(first_confirmation["gate"], "script-style")
            workflow.approve(project, "script-style")
            self.assertEqual(workflow.status(project)["stage"], "prepare-script-voice")
            storyboard = {
                "version": 2,
                "characters": [{"id": "host", "name": "讲解者", "reference": "characters/host.png"}],
                "scenes": [{
                    "id": "scene-01", "title": "需求上升", "narration": "因为需求增加",
                    "start_ms": 0, "end_ms": 2000,
                    "composition": "causal-chain",
                    "character_ids": ["host"],
                    "elements": [
                        {"id":"1", "label":"人群", "trigger_text":"因为"},
                        {
                            "id":"2", "trigger_text":"需求增加", "label": "价格上涨结果",
                        },
                    ],
                }],
            }
            (inputs / "storyboard.json").write_text(json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
            words = {
                "version": 1, "duration_ms": 2000,
                "words": [{"text": "因为", "start_ms": 0, "end_ms": 800}, {"text": "需求增加", "start_ms": 820, "end_ms": 2000}],
            }
            (inputs / "words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
            (inputs / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n因为需求增加\n", encoding="utf-8")
            with wave.open(str(inputs / "voice.wav"), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes(b"\x00\x00" * 16000)

            args = Namespace(
                script=str(inputs / "script.md"), storyboard=str(inputs / "storyboard.json"),
                audio=str(inputs / "voice.wav"), words=str(inputs / "words.json"), captions=str(inputs / "captions.srt"),
            )
            workflow.stage_script_voice(project, args)
            self.assertEqual(workflow.status(project)["stage"], "await-script-voice-approval")
            self.assertTrue((project / "visual-plan.json").is_file())
            self.assertTrue((project / "visual-plan.md").is_file())
            staged_state = workflow.read_json(project / "state.json")
            staged_project = workflow.read_json(project / "project.json")
            self.assertEqual(staged_project["voice_selection"]["provider"], "edge")
            self.assertIsNone(staged_project["voice_selection"]["timing_source"])
            self.assertIn("visual_plan", staged_state["artifacts"])
            self.assertIn("visual_plan_markdown", staged_state["artifacts"])
            self.assertIn("tts_script", staged_state["artifacts"])
            self.assertIn("pronunciation_overrides", staged_state["artifacts"])
            self.assertEqual((project / "script" / "narration.md").read_text(encoding="utf-8"), "因为需求增加。\n")
            confirmation = workflow.confirmation_bundle(project)
            self.assertEqual(confirmation["gate"], "script-voice")
            self.assertTrue(confirmation["files"]["audio"].endswith("narration.wav"))
            self.assertTrue(confirmation["files"]["visual_plan_markdown"].endswith("visual-plan.md"))
            self.assertEqual(confirmation["scenes"][0]["composition"], "causal-chain")
            request = confirmation["board_generation_requests"][0]
            self.assertEqual([section["kind"] for section in request["prompt_sections"]], [
                "scene-semantics",
                "composition-and-characters",
                "style-reference-and-contract",
                "safe-frame-and-forbidden-items",
            ])
            self.assertEqual(
                request["prompt_sections"][1]["character_references"][0]["id"],
                "host",
            )
            self.assertTrue(any(path.endswith("host.png") for path in request["reference_images"]))
            workflow.approve(project, "script-voice")
            approved_state = workflow.read_json(project / "state.json")
            self.assertEqual(approved_state["approvals"]["script_voice"]["provider"], "edge")
            self.assertEqual(
                approved_state["approvals"]["script_voice"]["audio_sha256"],
                approved_state["artifacts"]["audio"]["sha256"],
            )
            self.assertEqual(workflow.status(project)["stage"], "prepare-boards")

            image = inputs / "scene.png"
            Image.new("RGB", (100, 100), (245, 235, 215)).save(image)
            annotation = {
                "sceneId": "scene-01", "canvas": {"width": 100, "height": 100}, "sceneDurationMs": 2000,
                "elements": [
                    {"id": '1', "sequence": 1, "region": {"x": 0, "y": 0, "width": 40, "height": 90}, "reveal": {"startMs": 0, "durationMs": 900}},
                    {"id": '2', "sequence": 2, "region": {"x": 55, "y": 0, "width": 40, "height": 90}, "reveal": {"startMs": 950, "durationMs": 950}},
                ],
            }
            (inputs / "scene.annotation.json").write_text(json.dumps(annotation), encoding="utf-8")
            workflow.add_board(project, "scene-01", str(image), str(inputs / "scene.annotation.json"))
            self.assertEqual(workflow.status(project)["stage"], "prepare-boards")
            workflow.rebuild_plan(project)
            self.assertEqual(workflow.status(project)["stage"], "await-boards-approval")
            workflow.approve(project, "boards")
            self.assertEqual(workflow.status(project)["stage"], "ready-to-render")

            (inputs / "script.md").write_text("因为需求上涨。", encoding="utf-8")
            (inputs / "words.json").write_text(json.dumps({
                "version": 1, "duration_ms": 2000,
                "words": [{"text": "因为", "start_ms": 0, "end_ms": 800}, {"text": "需求上涨", "start_ms": 820, "end_ms": 2000}],
            }, ensure_ascii=False), encoding="utf-8")
            (inputs / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n因为需求上涨\n", encoding="utf-8")
            storyboard["scenes"][0]["narration"] = "因为需求上涨"
            storyboard["scenes"][0]["elements"][1]["trigger_text"] = "需求上涨"
            (inputs / "storyboard.json").write_text(
                json.dumps(storyboard, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(workflow.WorkflowError, "第一次确认"):
                workflow.stage_script_voice(project, args)
            workflow.stage_script(project, str(inputs / "script.md"))
            restaged = workflow.read_json(project / "state.json")
            self.assertFalse(restaged["approvals"]["script_style"]["approved"])
            self.assertFalse(restaged["approvals"]["script_voice"]["approved"])
            self.assertFalse(restaged["approvals"]["boards"]["approved"])
            self.assertIn("scene-01", restaged["boards"])
            self.assertTrue(restaged["boards"]["scene-01"]["stale"])
            self.assertEqual(
                restaged["boards"]["scene-01"]["stale_reason"],
                "script-or-style-changed",
            )
            self.assertEqual(restaged["render_cache"], {})
            self.assertFalse(restaged["final_current"])
            self.assertEqual(restaged["qa"], {})

            workflow.approve(project, "script-style")
            workflow.stage_script_voice(project, args)
            voice_restaged = workflow.read_json(project / "state.json")
            self.assertIn("scene-01", voice_restaged["boards"])
            self.assertTrue(voice_restaged["boards"]["scene-01"]["stale"])
            self.assertEqual(
                voice_restaged["boards"]["scene-01"]["stale_reason"],
                "script-voice-or-timeline-changed",
            )
            self.assertEqual(voice_restaged["render_cache"], {})

    def test_reconcile_existing_boards_recovers_lost_state_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "boards").mkdir()
            (root / "annotations").mkdir()
            image = root / "boards" / "scene-01.png"
            annotation = root / "annotations" / "scene-01.annotation.json"
            image.write_bytes(b"png-placeholder")
            annotation.write_text(
                json.dumps({"sceneId": "scene-01", "elements": []}), encoding="utf-8"
            )
            project = {"scenes": [{"id": "scene-01"}, {"id": "scene-02"}]}
            state = {"boards": {}}

            recovered = workflow.reconcile_existing_boards(root, project, state)

            self.assertEqual(recovered, ["scene-01"])
            record = state["boards"]["scene-01"]
            self.assertEqual(record["image"], "boards/scene-01.png")
            self.assertEqual(record["annotation"], "annotations/scene-01.annotation.json")
            self.assertTrue(record["stale"])
            self.assertEqual(record["stale_reason"], "recovered-existing-files")

    def test_words_reject_overlap(self) -> None:
        with self.assertRaisesRegex(workflow.WorkflowError, "重叠"):
            workflow.validate_words({
                "duration_ms": 1000,
                "words": [
                    {"text": "前", "start_ms": 0, "end_ms": 600},
                    {"text": "后", "start_ms": 500, "end_ms": 900},
                ],
            })

    def test_registered_styles_and_alias(self) -> None:
        registry = workflow.load_style_registry()
        self.assertEqual(len(registry["styles"]), 10)
        style = workflow.resolve_style("chalkboard")
        self.assertEqual(style["id"], "dark-chalkboard")
        self.assertEqual(style["renderer"]["hand_mode"], "small-hand")
        self.assertEqual(workflow.resolve_style("黑金科技")["id"], "black-gold-tech")
        self.assertEqual(workflow.resolve_style("规整手绘")["id"], "orderly-color-doodle")

    def test_orderly_color_doodle_contract_uses_human_lines_and_direct_flat_fill(self) -> None:
        style = workflow.resolve_style("orderly-color-doodle")
        contract = style["visual_contract"]
        self.assertEqual(contract["line_weight"], "medium-organic-controlled-varied")
        self.assertEqual(contract["fill"], "selective-clean-opaque-flat-color")
        self.assertEqual(contract["texture"], "none-or-nearly-none")
        self.assertEqual(style["renderer"]["color_fill"], "local-brush")
        self.assertIn("渐变", style["prompt"]["forbid"])
        reference = Path(__file__).parents[1] / style["reference_image"]
        with Image.open(reference) as image:
            self.assertEqual(image.size, (1672, 941))

    def test_style_contracts_use_real_compositions_and_first_party_references(self) -> None:
        visual_director_path = Path(__file__).parents[1] / "scripts" / "visual_director.py"
        spec = importlib.util.spec_from_file_location("visual_director_contract", visual_director_path)
        visual_director = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(visual_director)
        repository = Path(__file__).parents[1]
        for style in workflow.load_style_registry()["styles"]:
            compositions = style["visual_contract"]["compositions"]
            self.assertTrue(compositions, style["id"])
            self.assertTrue(set(compositions) <= visual_director.COMPOSITIONS, style["id"])
            reference = repository / style["reference_image"]
            self.assertTrue(reference.is_file(), f"{style['id']}: {reference}")

    def test_new_styles_are_recommended_for_distinct_topics(self) -> None:
        self.assertEqual(
            workflow.recommend_style("开源软件的智能体协作流程")["style_id"],
            "orderly-color-doodle",
        )
        self.assertEqual(
            workflow.recommend_style("人工智能芯片发布会")["style_id"],
            "black-gold-tech",
        )
        self.assertEqual(
            workflow.recommend_style("如何权衡边界与价值")["style_id"],
            "paper-metaphor-collage",
        )
        self.assertEqual(
            workflow.recommend_style("一份旧报纸里的媒体档案")["style_id"],
            "retro-newspaper",
        )

    def test_refresh_animation_plan_keeps_annotation_canonical_and_derives_render_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "audio").mkdir()
            (root / "boards").mkdir()
            (root / "annotations").mkdir()
            project = {
                "version": 3,
                "style_id": "warm-pencil",
                "scenes": [{
                    "id": "scene-01",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "composition": "center-spoke",
                }],
            }
            words = {"duration_ms": 3000, "words": [
                {"text": "核心结论", "start_ms": 0, "end_ms": 2900},
            ]}
            (root / "audio" / "words.json").write_text(json.dumps(words), encoding="utf-8")
            annotation = {
                "sceneId": "scene-01",
                "canvas": {"width": 200, "height": 180},
                "sceneDurationMs": 3000,
                "drawingPlan": {"minimumColorMs": 700},
                "elements": [{
                    "id": "result",
                    "sequence": 1,
                    "label": "核心结论",
                    "role": "结论",
                    "region": {"x": 20, "y": 20, "width": 150, "height": 100},
                    "reveal": {"startMs": 100, "durationMs": 1900},
                }],
            }
            annotation_path = root / "annotations" / "scene-01.annotation.json"
            annotation_path.write_text(json.dumps(annotation), encoding="utf-8")
            board_path = root / "boards" / "scene-01.png"
            board = Image.new("RGB", (200, 180), (245, 235, 215))
            ImageDraw.Draw(board).rectangle((65, 40, 130, 115), fill=(55, 80, 100))
            board.save(board_path)
            state = {
                "boards": {"scene-01": {
                    "image": "boards/scene-01.png",
                    "annotation": "annotations/scene-01.annotation.json",
                }},
                "render_cache": {"scene-01": {"key": "stale"}},
                "artifacts": {},
                "qa": {"ok": True},
                "final_current": True,
                "final_sha256": "old",
            }
            workflow.refresh_animation_plan(root, project, state)
            saved = workflow.read_json(annotation_path)
            plan = workflow.read_json(root / "animation-plan.json")
            self.assertEqual(saved["elements"][0]["reveal"]["durationMs"], 1900)
            self.assertEqual(plan["timingReservations"], [])
            self.assertEqual(plan["scenes"][0]["events"], [])
            effective = workflow.effective_annotation_for_plan(saved, "scene-01", plan)
            self.assertEqual(effective["elements"][0]["reveal"]["durationMs"], 1900)
            self.assertNotIn("scene-01", state["render_cache"])
            self.assertFalse(state["final_current"])
            self.assertEqual(state["qa"], {})

    def test_render_hand_defaults_to_style_and_allows_override(self) -> None:
        parser = workflow.build_parser()
        default = parser.parse_args(["render", "--project", "demo"])
        override = parser.parse_args(["render", "--project", "demo", "--full-hand"])
        presenter = parser.parse_args(["render", "--project", "demo", "--presenter"])
        self.assertIsNone(default.hand_mode)
        self.assertEqual(override.hand_mode, "full-hand")
        self.assertEqual(presenter.hand_mode, "presenter")

    def test_bookend_command_defaults_to_both_without_auto_merge(self) -> None:
        args = workflow.build_parser().parse_args(["bookend", "--project", "demo"])
        self.assertEqual(args.kind, "both")
        self.assertEqual(args.outro_message, "讲完啦，下次见！")

    def test_v2_storyboard_rejects_repeated_composition(self) -> None:
        storyboard = {
            "version": 2,
            "scenes": [
                {
                    "id": "scene-01", "narration": "先看原因", "start_ms": 0, "end_ms": 1000,
                    "composition": "causal-chain", "elements": [{"label": "原因"}, {"label": "变化"}],
                },
                {
                    "id": "scene-02", "narration": "再看结果", "start_ms": 1000, "end_ms": 2000,
                    "composition": "causal-chain", "elements": [{"label": "结果"}, {"label": "人物"}],
                },
            ],
        }
        with self.assertRaises(workflow.WorkflowError):
            workflow.validate_storyboard(storyboard, require_v2=True)

    def test_duration_plan_extends_last_scene_to_decoded_audio(self) -> None:
        plan = workflow.duration_plan(
            [{"end_ms": 22130}],
            [{"end_ms": 22130}],
            22656,
        )
        self.assertEqual(plan["target_duration_ms"], 22656)
        self.assertEqual(plan["tail_extension_ms"], 526)

    def test_frame_render_plan_preserves_leading_gap_and_exact_total(self) -> None:
        scenes = [
            {"id": "scene-01", "start_ms": 95, "end_ms": 4849},
            {"id": "scene-02", "start_ms": 4849, "end_ms": 15771},
            {"id": "scene-03", "start_ms": 15771, "end_ms": 29501},
            {"id": "scene-04", "start_ms": 29501, "end_ms": 40159},
            {"id": "scene-05", "start_ms": 40159, "end_ms": 51729},
            {"id": "scene-06", "start_ms": 51729, "end_ms": 63706},
        ]
        plan = workflow.frame_render_plan(scenes, 63706, 30)
        self.assertEqual(plan["target_frames"], 1912)
        self.assertEqual(plan["scenes"][0]["lead_frames"], 3)
        self.assertEqual(sum(item["target_frames"] for item in plan["scenes"]), 1912)
        self.assertEqual(plan["scenes"][-1]["end_frame"], 1912)

    def test_scene_render_cache_key_changes_with_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "boards").mkdir()
            (root / "annotations").mkdir()
            image = root / "boards" / "scene-01.png"
            annotation = root / "annotations" / "scene-01.annotation.json"
            image.write_bytes(b"image")
            annotation.write_text('{"sceneDurationMs":1000}', encoding="utf-8")
            record = {"image": "boards/scene-01.png", "annotation": "annotations/scene-01.annotation.json"}
            args = (
                root,
                record,
                {"sceneId": "scene-01", "events": []},
                {"scene_id": "scene-01", "target_frames": 30, "lead_frames": 0},
                {"ink_path": "skeleton", "color_fill": "contour-wipe"},
                30,
                1920,
                "small-hand",
                "renderer-hash",
            )
            first = workflow.scene_render_cache_key(*args)
            annotation.write_text('{"sceneDurationMs":1001}', encoding="utf-8")
            second = workflow.scene_render_cache_key(*args)
            self.assertNotEqual(first, second)

            output = root / "renders" / "scene-01.mp4"
            output.parent.mkdir()
            output.write_bytes(b"video")
            cache = {"key": second, "path": "renders/scene-01.mp4", "sha256": workflow.digest(output)}
            self.assertTrue(workflow.cache_record_current(root, cache, second))
            output.write_bytes(b"tampered")
            self.assertFalse(workflow.cache_record_current(root, cache, second))

    def test_render_board_preflight_reports_pixel_findings_as_advisory(self) -> None:
        expected = {
            "ok": False,
            "errors": ["scene-02：连续前景被多个元素切开"],
        }
        with patch.object(workflow, "run_board_qa", return_value=expected):
            self.assertIs(workflow.require_board_qa_for_render(Path("demo")), expected)

    def test_render_board_preflight_allows_overlap_warnings(self) -> None:
        expected = {
            "ok": True,
            "scenes": [{"scene": "scene-01"}],
            "warnings": ["scene-01：矩形区域重叠"],
        }
        with patch.object(workflow, "run_board_qa", return_value=expected):
            self.assertIs(workflow.require_board_qa_for_render(Path("demo")), expected)

    def test_final_requires_internal_qa_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "deliverables").mkdir(parents=True)
            final = root / "deliverables" / "final.mp4"
            final.write_bytes(b"final-media")
            project = {"version": 1, "title": "旧项目", "scenes": []}
            state = {
                "version": 1,
                "approvals": {"script_voice": {"approved": True}, "boards": {"approved": True}},
                "artifacts": {key: {"path": key} for key in ("script", "storyboard", "audio", "words", "captions")},
                "boards": {},
                "final_current": True,
                "final_sha256": workflow.digest(final),
                "qa": {},
            }
            project["scenes"] = [{"id": "scene-01"}]
            state["boards"] = {
                "scene-01": {"image": "deliverables/final.mp4", "annotation": "deliverables/final.mp4"}
            }
            workflow.write_json(root / "project.json", project)
            workflow.write_json(root / "state.json", state)
            self.assertFalse((root / "visual-plan.json").exists())
            workflow.write_json(root / "deliverables" / "render-receipt.json", {
                "version": 1, "inputs": workflow.render_input_snapshot(root, workflow.read_json(root / "state.json")),
                "final_sha256": workflow.digest(final),
            })
            self.assertEqual(workflow.status(root)["stage"], "await-final-qa")
            workflow.write_json(root / "deliverables" / "qa-report.json", {
                "version": workflow.EXPECTED_QA_REPORT_VERSION,
                "ok": True,
                "final": {"sha256": workflow.digest(final)},
                "visual_review": {"required": True, "accepted": False},
            })
            workflow.write_json(root / "deliverables" / "render-receipt.json", {
                "version": 1,
                "created_at": workflow.now(),
                "inputs": workflow.render_input_snapshot(root, state),
                "final_sha256": workflow.digest(final),
            })
            workflow.accept_final_qa(
                root,
                "人物与构图连续，small-hand 未遮挡主体，字幕清楚，末帧完整。",
            )
            self.assertEqual(workflow.status(root)["stage"], "complete")

            report = workflow.read_json(root / "deliverables" / "qa-report.json")
            report["version"] = workflow.EXPECTED_QA_REPORT_VERSION - 1
            workflow.write_json(root / "deliverables" / "qa-report.json", report)
            self.assertEqual(workflow.status(root)["stage"], "await-final-qa")

    def test_failed_automated_qa_enters_fix_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "deliverables").mkdir(parents=True)
            final = root / "deliverables" / "final.mp4"
            final.write_bytes(b"final-media")
            workflow.write_json(root / "project.json", {
                "version": 1,
                "title": "待修复项目",
                "scenes": [{"id": "scene-01"}],
            })
            workflow.write_json(root / "state.json", {
                "version": 1,
                "approvals": {
                    "script_style": {"approved": True},
                    "script_voice": {"approved": True},
                    "boards": {"approved": True},
                },
                "artifacts": {key: {"path": key} for key in ("script", "storyboard", "audio", "words", "captions")},
                "boards": {"scene-01": {
                    "image": "deliverables/final.mp4",
                    "annotation": "deliverables/final.mp4",
                }},
                "final_current": True,
                "final_sha256": workflow.digest(final),
                "qa": {"automated_ok": False, "manual_approved": False},
            })
            workflow.write_json(root / "deliverables" / "render-receipt.json", {
                "version": 1, "inputs": workflow.render_input_snapshot(root, workflow.read_json(root / "state.json")),
                "final_sha256": workflow.digest(final),
            })
            self.assertEqual(workflow.status(root)["stage"], "fix-required")

    def test_annotation_rejects_overlap(self) -> None:
        data = {
            "canvas": {"width": 100, "height": 100}, "sceneDurationMs": 1000,
            "elements": [
                {"id": '1', "sequence": 1, "region": {"x": 0, "y": 0, "width": 40, "height": 40}, "reveal": {"startMs": 0, "durationMs": 600}},
                {"id": '2', "sequence": 2, "region": {"x": 50, "y": 0, "width": 40, "height": 40}, "reveal": {"startMs": 500, "durationMs": 300}},
            ],
        }
        with self.assertRaises(workflow.WorkflowError):
            workflow.validate_annotation(data)

    def test_annotation_accepts_spatial_overlap_without_manual_protection(self) -> None:
        data = {
            "canvas": {"width": 100, "height": 100}, "sceneDurationMs": 1000,
            "elements": [
                {"id": '1', "label": "主体", "sequence": 1, "region": {"x": 0, "y": 0, "width": 60, "height": 60}, "reveal": {"startMs": 0, "durationMs": 400, "protectedRegions": []}},
                {"id": '2', "label": "结果", "sequence": 2, "region": {"x": 50, "y": 20, "width": 40, "height": 40}, "reveal": {"startMs": 400, "durationMs": 400, "protectedRegions": []}},
            ],
        }
        workflow.validate_annotation(data)

    def test_annotation_accepts_explicitly_protected_spatial_overlap(self) -> None:
        data = {
            "canvas": {"width": 100, "height": 100}, "sceneDurationMs": 1000,
            "elements": [
                {"id": '1', "label": "主体", "sequence": 1, "region": {"x": 0, "y": 0, "width": 60, "height": 60}, "reveal": {"startMs": 0, "durationMs": 400, "protectedRegions": [{"x": 50, "y": 20, "width": 10, "height": 40}]}},
                {"id": '2', "label": "结果", "sequence": 2, "region": {"x": 50, "y": 20, "width": 40, "height": 40}, "reveal": {"startMs": 400, "durationMs": 400, "protectedRegions": []}},
            ],
        }
        workflow.validate_annotation(data)

    def test_storyboard_allows_adaptive_one_to_six_elements(self) -> None:
        for count in (1, 6):
            labels = [f"对象{i}" for i in range(1, count + 1)]
            data = {
                "scenes": [{
                    "id": "scene-01",
                    "narration": "，".join(labels),
                    "start_ms": 0,
                    "end_ms": 2000,
                    "composition": "center-spoke",
                    "elements": [
                        {"id": f"object-{i}", "label": label, "trigger_text": label}
                        for i, label in enumerate(labels, 1)
                    ],
                }],
            }
            self.assertEqual(len(workflow.validate_storyboard(data)), 1)

    def test_storyboard_rejects_decorative_arrow_without_relation_contract(self) -> None:
        data = {
            "version": 3,
            "scenes": [{
                "id": "scene-01",
                "narration": "人物站在画面中央，旁边出现装饰箭头",
                "start_ms": 0,
                "end_ms": 2000,
                "composition": "center-spoke",
                "elements": [
                    {"id": "person", "label": "人物", "trigger_text": "人物站在画面中央"},
                    {"id": "arrow", "label": "装饰箭头", "trigger_text": "装饰箭头"},
                ],
            }],
        }
        with self.assertRaisesRegex(workflow.WorkflowError, "不能把箭头、关系线或分隔线作为绘制对象"):
            workflow.validate_storyboard(data, require_v3=True)

    def test_caption_semantic_split_is_warning_not_blocker(self) -> None:
        script = "因为每条视频，都在给你一个很快的小奖励。奖励来得越快，你越想继续。"
        bad_srt = (
            "1\n00:00:00,000 --> 00:00:01,000\n因为每条视频都在\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\n给你一个很快的小\n\n"
            "3\n00:00:02,000 --> 00:00:03,000\n奖励奖励来得越快\n\n"
            "4\n00:00:03,000 --> 00:00:04,000\n你越想继续\n"
        )
        self.assertEqual(len(workflow.validate_caption_semantics(script, bad_srt)), 4)

    def test_caption_rejects_punctuation_and_multiple_lines(self) -> None:
        script = "因为每条视频，都在给你一个很快的小奖励。奖励来得越快，你越想继续。"
        punctuated_srt = (
            "1\n00:00:00,000 --> 00:00:02,000\n因为每条视频，\n都在给你一个很快的小奖励。\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\n奖励来得越快，\n你越想继续。\n"
        )
        with self.assertRaisesRegex(workflow.WorkflowError, "一行|标点"):
            workflow.validate_caption_semantics(script, punctuated_srt)

    def test_caption_accepts_punctuation_free_single_lines(self) -> None:
        script = "因为每条视频，都在给你一个很快的小奖励。奖励来得越快，你越想继续。"
        srt = (
            "1\n00:00:00,000 --> 00:00:02,000\n因为每条视频都在给你一个很快的小奖励\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\n奖励来得越快你越想继续\n"
        )
        self.assertEqual(len(workflow.validate_caption_semantics(script, srt)), 2)

    def test_words_duration_cannot_end_before_last_word(self) -> None:
        with self.assertRaisesRegex(workflow.WorkflowError, "早于最后一个词"):
            workflow.validate_words({
                "duration_ms": 900,
                "words": [{"text": "测试", "start_ms": 0, "end_ms": 1000}],
            }, require_duration=True)

    def test_prepare_voice_text_requires_user_approved_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "script.md"
            overrides = root / "overrides.json"
            output = root / "tts.txt"
            script.write_text("那就大方地放出来", encoding="utf-8")
            overrides.write_text(json.dumps({
                "version": 1,
                "overrides": [{
                    "written": "大方地",
                    "spoken": "大方的",
                    "reason": "助词地在这里读轻声 de",
                    "approved_by_user": True,
                }],
            }, ensure_ascii=False), encoding="utf-8")
            result = workflow.prepare_voice_text(script, overrides, output)
            self.assertEqual(output.read_text(encoding="utf-8").strip(), "那就大方的放出来")
            self.assertEqual(result["applied"][0]["replacement_count"], 1)

            data = json.loads(overrides.read_text(encoding="utf-8"))
            data["overrides"][0]["approved_by_user"] = False
            overrides.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(workflow.WorkflowError, "尚未获得用户确认"):
                workflow.prepare_voice_text(script, overrides, output)

    def test_elevenlabs_alignment_conversion(self) -> None:
        class Alignment:
            characters = ["你", "好", "，", " ", "世", "界"]
            character_start_times_seconds = [0.0, 0.2, 0.4, 0.5, 0.7, 0.9]
            character_end_times_seconds = [0.2, 0.4, 0.5, 0.7, 0.9, 1.1]

        words = tts_elevenlabs.alignment_to_words(Alignment())
        self.assertEqual([item["text"] for item in words], ["你", "好", "世", "界"])
        self.assertEqual(words[-1]["end_ms"], 1100)


if __name__ == "__main__":
    unittest.main()
