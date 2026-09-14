from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "extract_subtitles.py"
SPEC = importlib.util.spec_from_file_location("extract_subtitles_under_test", MODULE_PATH)
extract_subtitles = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(extract_subtitles)


class ExtractSubtitlesTests(unittest.TestCase):
    def test_auto_model_prefers_cached_small(self) -> None:
        with patch.object(extract_subtitles, "cached_asr_models", return_value=["small", "tiny", "large-v3-turbo"]):
            self.assertEqual(
                extract_subtitles.resolve_asr_model("auto"),
                ("small", "cache", ["small", "tiny", "large-v3-turbo"]),
            )

    def test_auto_model_reuses_an_existing_non_small_model(self) -> None:
        with patch.object(extract_subtitles, "cached_asr_models", return_value=["large-v3-turbo"]):
            self.assertEqual(
                extract_subtitles.resolve_asr_model("auto"),
                ("large-v3-turbo", "cache", ["large-v3-turbo"]),
            )

    def test_first_use_defaults_to_small_download(self) -> None:
        with patch.object(extract_subtitles, "cached_asr_models", return_value=[]):
            self.assertEqual(extract_subtitles.resolve_asr_model("auto"), ("small", "default-download", []))

    def test_explicit_user_model_selection_is_respected(self) -> None:
        with patch.object(extract_subtitles, "cached_asr_models", return_value=["small"]):
            self.assertEqual(
                extract_subtitles.resolve_asr_model("large-v3-turbo"),
                ("large-v3-turbo", "explicit-download", ["small"]),
            )

    def test_explicit_cached_user_model_is_not_marked_for_download(self) -> None:
        with patch.object(extract_subtitles, "cached_asr_models", return_value=["large-v3-turbo"]):
            self.assertEqual(
                extract_subtitles.resolve_asr_model("large-v3-turbo"),
                ("large-v3-turbo", "explicit-cached", ["large-v3-turbo"]),
            )

    def test_opencc_uses_reimplemented_package_config_name(self) -> None:
        class Converter:
            @staticmethod
            def convert(text: str) -> str:
                return text

        opencc = Mock(return_value=Converter())
        with patch.dict(sys.modules, {"opencc": SimpleNamespace(OpenCC=opencc)}):
            self.assertEqual(extract_subtitles.to_simplified("测试"), "测试")
        opencc.assert_called_once_with("t2s")

    def test_selects_native_chinese_subtitles_before_automatic_captions(self) -> None:
        selected = extract_subtitles._select_chinese_track({
            "subtitles": {
                "zh-CN": [{"ext": "vtt", "url": "https://example.test/native.vtt"}],
            },
            "automatic_captions": {
                "zh-Hans": [{"ext": "json3", "url": "https://example.test/auto.json3"}],
            },
        })
        self.assertEqual(
            selected,
            ("zh-CN", "https://example.test/native.vtt", "vtt", "native"),
        )

    def test_selects_native_english_source_track_before_automatic_translation(self) -> None:
        selected = extract_subtitles._select_caption_track({
            "language": "en",
            "subtitles": {
                "en": [{"ext": "vtt", "url": "https://example.test/native-en.vtt"}],
            },
            "automatic_captions": {
                "zh-Hans": [{"ext": "json3", "url": "https://example.test/translated-zh.json3"}],
                "en-orig": [{"ext": "json3", "url": "https://example.test/auto-en.json3"}],
            },
        }, "auto")
        self.assertEqual(
            selected,
            ("en", "https://example.test/native-en.vtt", "vtt", "native"),
        )

    def test_generic_native_caption_path_skips_asr_but_keeps_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp)
            audio = out_dir / "voiceover.m4a"
            audio.write_bytes(b"audio")
            cues = [{"startMs": 0, "endMs": 1000, "text": "原生字幕"}]
            with patch.object(
                extract_subtitles,
                "resolve_generic_captions",
                return_value=(cues, "generic-native/zh-CN", "zh"),
            ), patch.object(
                extract_subtitles,
                "download_generic_audio",
                return_value=audio,
            ), patch.object(extract_subtitles, "transcribe_audio") as transcribe:
                result = extract_subtitles.process("https://example.test/watch/1", out_dir)
            transcribe.assert_not_called()
            self.assertEqual(result["transcript_source"], "generic-native/zh-CN")
            self.assertEqual((out_dir / "script.txt").read_text(encoding="utf-8"), "原生字幕\n")

    def test_generic_missing_captions_downloads_audio_then_runs_asr(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp)
            audio = out_dir / "voiceover.m4a"
            audio.write_bytes(b"audio")
            cues = [{"startMs": 0, "endMs": 1000, "text": "本地转写"}]
            words = [{"text": "本地转写", "start_ms": 0, "end_ms": 1000}]
            with patch.object(extract_subtitles, "resolve_generic_captions", return_value=None), patch.object(
                extract_subtitles,
                "download_generic_audio",
                return_value=audio,
            ), patch.object(
                extract_subtitles,
                "resolve_asr_model",
                return_value=("small", "cache", ["small"]),
            ), patch.object(
                extract_subtitles,
                "transcribe_audio",
                return_value=(cues, words, 1000, "current", "zh", 0.99),
            ) as transcribe:
                result = extract_subtitles.process("https://example.test/watch/2", out_dir)
            transcribe.assert_called_once_with(audio, "small", "cpu", "", "auto")
            self.assertEqual(result["transcript_source"], "local-asr/current")
            self.assertEqual(result["asr_model"], "small")
            self.assertEqual(result["asr_model_source"], "cache")
            self.assertTrue((out_dir / "words.json").is_file())

    def test_english_source_rejects_chinese_gibberish(self) -> None:
        cues = [
            {"startMs": index * 5000, "endMs": index * 5000 + 4000, "text": "肩膀和温暖的温暖"}
            for index in range(12)
        ]
        quality = extract_subtitles.transcript_quality(cues, 60_000, "en", 0.95)
        self.assertEqual(quality["status"], "failed")
        self.assertTrue(any("异常比例的中文" in item for item in quality["errors"]))

    def test_single_promotional_hallucination_rejects_long_video(self) -> None:
        cues = [{
            "startMs": 120_000,
            "endMs": 125_000,
            "text": "请不吝点赞 订阅 转发 打赏支持栏目",
        }]
        quality = extract_subtitles.transcript_quality(cues, 192_135, "zh", 0.98)
        self.assertEqual(quality["status"], "failed")
        self.assertTrue(any("推广话术" in item for item in quality["errors"]))

    def test_complete_english_transcript_passes_quality_gate(self) -> None:
        cues = [
            {
                "startMs": index * 5000,
                "endMs": index * 5000 + 4500,
                "text": f"Segment {index} explains how mosquitoes find people through scent and heat.",
            }
            for index in range(12)
        ]
        quality = extract_subtitles.transcript_quality(cues, 60_000, "en", 0.97)
        self.assertEqual(quality["status"], "passed")

    def test_english_transcript_rejects_a_chinese_middle_section(self) -> None:
        cues = [
            {"startMs": 0, "endMs": 18_000, "text": "The introduction explains mosquito attraction in clear English."},
            {"startMs": 20_000, "endMs": 38_000, "text": "这是一整段与英文源语言不一致的中文内容"},
            {"startMs": 40_000, "endMs": 59_000, "text": "The conclusion returns to the original English explanation."},
        ]
        quality = extract_subtitles.transcript_quality(cues, 60_000, "en", 0.96)
        self.assertEqual(quality["status"], "failed")
        self.assertTrue(any("第 2 段语言" in item for item in quality["errors"]))

    def test_cli_returns_failure_when_quality_gate_fails(self) -> None:
        failed = {
            "manifest_path": "failed-extraction.json",
            "quality": {"status": "failed", "errors": ["字幕数量异常"]},
        }
        with patch.object(extract_subtitles, "process", return_value=failed):
            self.assertEqual(extract_subtitles.main(["input.mp4", "--out-dir", "output"]), 2)

    def test_yt_dlp_receives_the_discovered_isolated_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp)
            ffmpeg = out_dir / "runtime" / "ffmpeg"

            def run(command, **_kwargs):
                (out_dir / "voiceover.m4a").write_bytes(b"audio")
                return Namespace(returncode=0)

            with patch.object(extract_subtitles.importlib.util, "find_spec", return_value=object()), patch.object(
                extract_subtitles, "runtime_info", return_value={"available": True, "path": str(ffmpeg)}
            ), patch.object(extract_subtitles.subprocess, "run", side_effect=run) as invoked:
                result = extract_subtitles.download_generic_audio("https://example.test/watch/1", out_dir)

            command = invoked.call_args.args[0]
            location_index = command.index("--ffmpeg-location")
            self.assertEqual(command[location_index + 1], str(ffmpeg))
            self.assertEqual(result, out_dir / "voiceover.m4a")

    def test_asr_discovery_does_not_probe_an_unrelated_application_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            local_app_data = Path(temp)
            legacy_app = "".join(("Broll", "Video"))
            unrelated = local_app_data / legacy_app / "lib" / "python" / "python.exe"
            unrelated.parent.mkdir(parents=True)
            unrelated.touch()
            probed: list[Path] = []

            def has_module(candidate: Path, _module: str) -> bool:
                probed.append(candidate)
                return candidate == unrelated.resolve()

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}, clear=True), patch.object(
                extract_subtitles, "_python_has_module", side_effect=has_module
            ):
                python, source = extract_subtitles.resolve_asr_python()

            self.assertIsNone(python)
            self.assertIsNone(source)
            self.assertNotIn(unrelated.resolve(), probed)

    def test_explicit_asr_interpreter_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            explicit = Path(temp) / "asr-python"
            explicit.touch()
            with patch.object(extract_subtitles, "_python_has_module", return_value=True):
                python, source = extract_subtitles.resolve_asr_python(str(explicit))
            self.assertEqual(python, explicit.resolve())
            self.assertEqual(source, "explicit")


if __name__ == "__main__":
    unittest.main()
