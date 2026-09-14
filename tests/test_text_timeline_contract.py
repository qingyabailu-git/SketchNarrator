from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import qa_final
from scripts.text_policy import TextPolicyError, validate_caption_contract


class TextTimelineContractTests(unittest.TestCase):
    def test_visual_qa_targets_are_lightweight_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "annotations").mkdir()
            scenes = []
            plan_scenes = []
            for scene_index in range(7):
                start = scene_index * 10000
                scene_id = f"scene-{scene_index + 1:02d}"
                scenes.append({"id": scene_id, "start_ms": start, "end_ms": start + 10000})
                (root / "annotations" / f"{scene_id}.annotation.json").write_text(
                    json.dumps({
                        "elements": [
                            {"sequence": 1, "id": "first", "reveal": {"startMs": 200, "durationMs": 1800}},
                            {"sequence": 2, "id": "last", "reveal": {"startMs": 2400, "durationMs": 2600}},
                        ]
                    }),
                    encoding="utf-8",
                )
                plan_scenes.append({
                    "sceneId": scene_id,
                    "sceneStartMs": start,
                    "events": [{"id": f"event-{scene_index}", "startMs": 5200, "endMs": 6500}],
                    "transition": None if scene_index == 6 else {
                        "eraseStartMs": start + 9400,
                        "eraseEndMs": start + 9800,
                        "nextSceneFirstWordMs": start + 10000,
                        "toSceneId": f"scene-{scene_index + 2:02d}",
                    },
                })
            (root / "animation-plan.json").write_text(
                json.dumps({"scenes": plan_scenes}),
                encoding="utf-8",
            )
            cues = [
                {"start_ms": index * 1000, "end_ms": index * 1000 + 700}
                for index in range(66)
            ]

            targets = qa_final.build_targets({"scenes": scenes}, root, cues, 70000)

            self.assertLessEqual(len(targets), qa_final.MAX_VISUAL_REVIEW_FRAMES)
            self.assertEqual(sum(item["kind"] == "caption-mid" for item in targets), 2)
            self.assertEqual(sum(item["kind"] == "hand-drawn-mid" for item in targets), 7)
            self.assertEqual(sum(item["kind"] == "element-end" for item in targets), 7)
            self.assertEqual(sum(item["kind"] == "animation-mid" for item in targets), 1)
            self.assertFalse(any(item["kind"] in {"animation-25", "animation-75"} for item in targets))
            first_scene_end = next(item for item in targets if item["label"] == "scene-01-end")
            self.assertEqual(first_scene_end["time_ms"], 9275)

    def test_final_caption_contract_is_one_line_and_punctuation_free(self) -> None:
        script = "其实，肠道气体主要有两个来源。"
        valid = "1\n00:00:00,000 --> 00:00:01,000\n其实肠道气体\n\n2\n00:00:01,000 --> 00:00:02,000\n主要有两个来源\n"
        cues, warnings = validate_caption_contract(script, valid)
        self.assertEqual(len(cues), 2)
        self.assertIsInstance(warnings, list)

        with self.assertRaisesRegex(TextPolicyError, "标点"):
            validate_caption_contract(script, valid.replace("其实肠道气体", "其实，肠道气体"))

    def test_space_only_replaces_a_real_source_punctuation_boundary(self) -> None:
        combined = "1\n00:00:00,000 --> 00:00:02,000\n第一句话 第二句话\n"
        cues, _ = validate_caption_contract("第一句话，第二句话。", combined)
        self.assertEqual(cues[0]["text"], "第一句话 第二句话")

        invented = "1\n00:00:00,000 --> 00:00:02,000\n私人飞机 坐的人太少\n"
        with self.assertRaisesRegex(TextPolicyError, "硬拆"):
            validate_caption_contract("私人飞机坐的人太少。", invented)

        short_phrase = "1\n00:00:00,000 --> 00:00:02,000\n天生 尾重头轻\n"
        with self.assertRaisesRegex(TextPolicyError, "硬拆"):
            validate_caption_contract("天生尾重头轻。", short_phrase)

    def test_media_contract_requires_exact_16_9_and_aligned_timeline(self) -> None:
        result = qa_final.media_contract_checks(
            {"aspect_ratio": "16:9"},
            {"duration_ms": 1000, "words": [{"text": "测试", "start_ms": 0, "end_ms": 1000}]},
            [{"start_ms": 0, "end_ms": 1000, "text": "测试", "line_count": 1}],
            {"resolution": [1919, 1080]},
            1000,
            40,
        )
        self.assertIn("不是精确 16:9", "；".join(result["errors"]))

        valid = qa_final.media_contract_checks(
            {"aspect_ratio": "16:9"},
            {"duration_ms": 1000, "words": [{"text": "测试", "start_ms": 0, "end_ms": 1000}]},
            [{"start_ms": 0, "end_ms": 1000, "text": "测试", "line_count": 1}],
            {"resolution": [1920, 1080]},
            1000,
            40,
        )
        self.assertEqual(valid["errors"], [])
        self.assertTrue(valid["metrics"]["exact_16_9"])

    def test_pronunciation_contract_preserves_display_text_and_proves_tts_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "audio").mkdir()
            script = "那就大方地放出来"
            (root / "audio" / "tts-script.txt").write_text("那就大方的放出来\n", encoding="utf-8")
            (root / "audio" / "pronunciation-overrides.json").write_text(json.dumps({
                "version": 1,
                "overrides": [{
                    "written": "大方地",
                    "spoken": "大方的",
                    "reason": "助词地在这里读轻声 de",
                    "approved_by_user": True,
                    "replacement_count": 1,
                }],
            }, ensure_ascii=False), encoding="utf-8")
            result = qa_final.pronunciation_contract_checks(root, script)
            self.assertEqual(result["errors"], [])
            self.assertTrue(result["tts_text_match"])
            self.assertTrue(result["manual_audio_review_required"])


if __name__ == "__main__":
    unittest.main()
