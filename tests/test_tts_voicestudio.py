from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_PATH = SCRIPT_DIR / "tts_voicestudio.py"
SPEC = importlib.util.spec_from_file_location("tts_voicestudio", MODULE_PATH)
tts_voicestudio = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(tts_voicestudio)


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class VoiceStudioTests(unittest.TestCase):
    def test_authenticated_remote_http_is_blocked_before_request(self):
        request = tts_voicestudio.urllib.request.Request(
            "http://example.invalid/v1/audio/speech", headers={"Authorization": "Bearer fixture"})
        with patch.object(tts_voicestudio.urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                tts_voicestudio.open_request(request, 2)
        opener.assert_not_called()

    def test_service_error_body_is_not_exposed(self):
        import io
        error = tts_voicestudio.urllib.error.HTTPError(
            "https://example.invalid", 401, "unauthorized", {}, io.BytesIO(b'private narration'))
        self.assertNotIn("private narration", tts_voicestudio.error_detail(error))

    def test_redirect_is_not_followed(self):
        with self.assertRaises(tts_voicestudio.VoiceStudioError):
            tts_voicestudio.NoRedirect().redirect_request(
                None, None, 302, "redirect", {}, "https://example.invalid")

    def test_normalize_roots_accepts_host_or_v1(self) -> None:
        self.assertEqual(
            tts_voicestudio.normalize_roots("http://127.0.0.1:3900"),
            ("http://127.0.0.1:3900", "http://127.0.0.1:3900/v1"),
        )
        self.assertEqual(
            tts_voicestudio.normalize_roots("http://127.0.0.1:3900/v1/"),
            ("http://127.0.0.1:3900", "http://127.0.0.1:3900/v1"),
        )

    def test_userinfo_in_base_url_is_rejected_before_any_request_or_metadata_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "script.md"
            script.write_text("测试配音", encoding="utf-8")
            args = argparse.Namespace(
                script=str(script),
                out_dir=str(root / "audio"),
                base_url="http://operator:secret@127.0.0.1:3900/v1",
                timeout=2,
                model="tts-1",
                voice="default",
                speed=1.0,
                language="zh",
                instruct="",
                seed=None,
            )
            with patch.object(tts_voicestudio.urllib.request.OpenerDirector, "open") as request:
                with self.assertRaisesRegex(ValueError, "用户名或密码"):
                    tts_voicestudio.synthesize(args)
            request.assert_not_called()
            self.assertFalse((root / "audio").exists())

    def test_speech_payload_uses_openai_compatible_shape(self) -> None:
        args = argparse.Namespace(
            model="tts-1",
            voice="profile-123",
            speed=1.05,
            language="zh",
            instruct="自然、克制",
            seed=7,
        )
        payload = tts_voicestudio.speech_payload(args, "你好")
        self.assertEqual(payload["input"], "你好")
        self.assertEqual(payload["response_format"], "wav")
        self.assertEqual(payload["voice"], "profile-123")
        self.assertEqual(payload["instruct"], "自然、克制")

    def test_check_service_calls_lightweight_health_only(self) -> None:
        with patch.object(
            tts_voicestudio.urllib.request.OpenerDirector,
            "open",
            return_value=FakeResponse(b'{"status":"ok"}'),
        ) as mocked:
            health = tts_voicestudio.check_service("http://localhost:3900/v1", 2)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(mocked.call_args_list[0].args[0].full_url, "http://localhost:3900/health")

    def test_synthesize_writes_audio_and_non_secret_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "script.md"
            script.write_text("这是一段测试配音。", encoding="utf-8")
            args = argparse.Namespace(
                script=str(script),
                out_dir=str(root / "audio"),
                base_url="http://127.0.0.1:3900",
                timeout=2,
                model="tts-1",
                voice="default",
                speed=1.0,
                language="zh",
                instruct="",
                seed=None,
            )
            with patch.dict(tts_voicestudio.os.environ, {"VOICESTUDIO_API_KEY": "secret"}), patch.object(
                tts_voicestudio.urllib.request.OpenerDirector,
                "open",
                return_value=FakeResponse(b"RIFF-test", "audio/wav"),
            ) as mocked:
                audio_path, metadata_path = tts_voicestudio.synthesize(args)

            request = mocked.call_args.args[0]
            request_body = json.loads(request.data.decode("utf-8"))
            metadata = metadata_path.read_text(encoding="utf-8")
            self.assertEqual(request.full_url, "http://127.0.0.1:3900/v1/audio/speech")
            self.assertEqual(request_body["input"], "这是一段测试配音。")
            self.assertEqual(audio_path.read_bytes(), b"RIFF-test")
            self.assertNotIn("secret", metadata)
            self.assertIn('"alignment_required": true', metadata)


if __name__ == "__main__":
    unittest.main()
