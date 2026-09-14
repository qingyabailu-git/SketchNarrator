from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_PATH = SCRIPT_DIR / "tts_azure.py"
SPEC = importlib.util.spec_from_file_location("tts_azure", MODULE_PATH)
tts_azure = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(tts_azure)


class AzureTtsTests(unittest.TestCase):
    def test_required_config_reports_only_missing_field_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "AZURE_SPEECH_KEY,AZURE_SPEECH_REGION"):
            tts_azure.required_config({})

    def test_build_ssml_escapes_text_and_attributes(self) -> None:
        ssml = tts_azure.build_ssml('甲 & <乙> "丙"', 'zh-CN-Test"Voice', "+8%", "+0%", "+0Hz")
        self.assertIn("甲 &amp; &lt;乙&gt; \"丙\"", ssml)
        root = ET.fromstring(ssml)
        voice = root.find("{http://www.w3.org/2001/10/synthesis}voice")
        self.assertIsNotNone(voice)
        self.assertEqual(voice.attrib["name"], 'zh-CN-Test"Voice')

    def test_azure_event_to_word_converts_ticks_and_duration(self) -> None:
        event = SimpleNamespace(
            boundary_type=SimpleNamespace(name="Word"),
            text="你好",
            audio_offset=2_500_000,
            duration=timedelta(milliseconds=125),
        )
        self.assertEqual(
            tts_azure.azure_event_to_word(event),
            {"text": "你好", "start_ms": 250, "end_ms": 375},
        )

    def test_azure_event_to_word_ignores_sentence_and_punctuation(self) -> None:
        sentence = SimpleNamespace(
            boundary_type=SimpleNamespace(name="Sentence"),
            text="你好。",
            audio_offset=0,
            duration=timedelta(seconds=1),
        )
        punctuation = SimpleNamespace(
            boundary_type=SimpleNamespace(name="Word"),
            text="。",
            audio_offset=0,
            duration=timedelta(milliseconds=10),
        )
        self.assertIsNone(tts_azure.azure_event_to_word(sentence))
        self.assertIsNone(tts_azure.azure_event_to_word(punctuation))

    def test_write_outputs_uses_shared_contract_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp)
            words_path, captions_path = tts_azure.write_outputs(
                [
                    {"text": "这是", "start_ms": 0, "end_ms": 1},
                    {"text": "测试", "start_ms": 320, "end_ms": 700},
                ],
                out_dir,
                "zh-CN-XiaoxiaoNeural",
                "eastasia",
                900,
                14,
                2800,
            )
            payload = json.loads(words_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["provider"], "azure")
            self.assertEqual(payload["words"][0]["end_ms"], 320)
            self.assertNotIn("key", words_path.read_text(encoding="utf-8").lower())
            self.assertIn("这是测试", captions_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
