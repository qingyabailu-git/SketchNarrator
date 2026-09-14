from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_PATH = SCRIPT_DIR / "tts_free.py"
SPEC = importlib.util.spec_from_file_location("tts_free", MODULE_PATH)
tts_free = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(tts_free)


class FreeTtsTests(unittest.TestCase):
    def test_edge_default_rate_is_twelve_percent_faster(self) -> None:
        args = tts_free.parser().parse_args(["--script", "a", "--out-dir", "b"])
        self.assertEqual(args.rate, "+12%")

    def test_edge_chunk_to_word_converts_ticks(self) -> None:
        word = tts_free.edge_chunk_to_word(
            {"type": "WordBoundary", "text": "你好", "offset": 2_500_000, "duration": 1_250_000}
        )
        self.assertEqual(word, {"text": "你好", "start_ms": 250, "end_ms": 375})

    def test_edge_chunk_to_word_ignores_audio_and_punctuation(self) -> None:
        self.assertIsNone(tts_free.edge_chunk_to_word({"type": "audio", "data": b"123"}))
        self.assertIsNone(
            tts_free.edge_chunk_to_word(
                {"type": "WordBoundary", "text": "。", "offset": 0, "duration": 10_000}
            )
        )

    def test_write_edge_outputs_creates_shared_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp)
            words_path, captions_path = tts_free.write_edge_outputs(
                [
                    {"text": "这是", "start_ms": 0, "end_ms": 300},
                    {"text": "测试", "start_ms": 320, "end_ms": 700},
                ],
                out_dir,
                tts_free.DEFAULT_EDGE_VOICE,
                14,
                2800,
            )
            self.assertIn('"provider": "edge"', words_path.read_text(encoding="utf-8"))
            self.assertIn("这是测试", captions_path.read_text(encoding="utf-8"))

    def test_write_edge_outputs_maps_spoken_alias_back_to_display_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            words_path, captions_path = tts_free.write_edge_outputs(
                [{"text": "大方的", "start_ms": 0, "end_ms": 700}],
                Path(temp),
                tts_free.DEFAULT_EDGE_VOICE,
                22,
                4200,
                "大方地",
            )
            payload = json.loads(words_path.read_text(encoding="utf-8"))
            self.assertEqual("".join(word["text"] for word in payload["words"]), "大方地")
            self.assertIn("大方地", captions_path.read_text(encoding="utf-8"))
            self.assertNotIn("大方的", captions_path.read_text(encoding="utf-8"))

    def test_run_strips_markdown_heading_before_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "narration.md"
            script.write_text("# 口播稿\n\n真正需要配音的正文。\n", encoding="utf-8")
            captured: dict[str, str] = {}

            async def fake_synthesize(args, text, out_dir):
                captured["text"] = text
                paths = (out_dir / "narration.mp3", out_dir / "words.json", out_dir / "captions.srt")
                for path in paths:
                    path.write_bytes(b"x")
                return paths

            original = tts_free.synthesize_edge
            tts_free.synthesize_edge = fake_synthesize
            try:
                args = tts_free.parser().parse_args(["--script", str(script), "--out-dir", str(root / "audio")])
                self.assertEqual(tts_free.run(args), 0)
            finally:
                tts_free.synthesize_edge = original
            self.assertEqual(captured["text"], "真正需要配音的正文。")


if __name__ == "__main__":
    unittest.main()
