from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_PATH = SCRIPT_DIR / "align_words.py"
SPEC = importlib.util.spec_from_file_location("align_words", MODULE_PATH)
align_words = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(align_words)


class AlignWordsTests(unittest.TestCase):
    def test_align_script_words_keeps_locked_script_text(self) -> None:
        recognized = [
            {"text": "画面也感", "start_ms": 1000, "end_ms": 1800},
            {"text": "更稳", "start_ms": 1900, "end_ms": 2300},
        ]
        aligned = align_words.align_script_words("画面也赶。更稳！", recognized)
        self.assertEqual("".join(item["text"] for item in aligned), "画面也赶更稳")
        self.assertEqual(aligned[0]["start_ms"], 1000)
        self.assertGreater(aligned[-1]["end_ms"], aligned[-1]["start_ms"])

    def test_align_script_words_inserts_missing_script_characters(self) -> None:
        recognized = [
            {"text": "先做", "start_ms": 0, "end_ms": 400},
            {"text": "声音", "start_ms": 800, "end_ms": 1200},
        ]
        aligned = align_words.align_script_words("先做好声音", recognized)
        self.assertEqual("".join(item["text"] for item in aligned), "先做好声音")
        self.assertTrue(all(a["end_ms"] <= b["start_ms"] for a, b in zip(aligned, aligned[1:])))

    def test_semantic_cues_follow_authored_chinese_clauses(self) -> None:
        script = (
            "你有没有发现，短视频一刷，半小时就没了？"
            "你本来只想看一条，手指却滑到了下一条。"
            "因为每条视频，都在给你一个很快的小奖励："
            "笑点、新鲜消息，或者意外反转。"
        )
        characters = [char for char in script if align_words.normalized(char)]
        words = [
            {"text": char, "start_ms": index * 180, "end_ms": index * 180 + 150}
            for index, char in enumerate(characters)
        ]
        cues = align_words.build_semantic_cues(script, words, max_chars=22, max_ms=4200, max_line_chars=14)
        texts = [cue["text"] for cue in cues]
        self.assertGreater(len(cues), 4)
        self.assertEqual(
            align_words.normalized("".join(texts)),
            align_words.normalized(script),
        )
        self.assertTrue(all("\n" not in text for text in texts))
        self.assertTrue(all(not any(char in align_words.PUNCTUATION for char in text) for text in texts))
        reward = next(text for text in texts if "小奖励" in text)
        self.assertIn("小奖励", reward)

    def test_caption_generator_does_not_invent_phrase_spaces(self) -> None:
        script = "私人飞机坐的人太少。天生尾重头轻。"
        characters = [char for char in script if align_words.normalized(char)]
        words = [
            {"text": char, "start_ms": index * 140, "end_ms": index * 140 + 120}
            for index, char in enumerate(characters)
        ]
        texts = [cue["text"] for cue in align_words.build_semantic_cues(script, words)]
        self.assertTrue(all(" " not in text for text in texts))
        self.assertEqual(align_words.normalized("".join(texts)), align_words.normalized(script))


if __name__ == "__main__":
    unittest.main()
