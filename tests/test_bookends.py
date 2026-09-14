from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bookends  # noqa: E402


class BookendTests(unittest.TestCase):
    def test_missing_ffprobe_uses_metadata_backend(self):
        with patch.object(bookends, "_probe_with_pyav", return_value={"duration_ms": 4000}) as fallback:
            result = bookends.probe_media(None, Path("clip.mp4"))
        self.assertEqual(result["duration_ms"], 4000)
        fallback.assert_called_once_with(Path("clip.mp4"))

    def test_pyav_rejects_missing_audio_without_scanning_frames(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        fake_av = MagicMock()
        container = fake_av.open.return_value.__enter__.return_value
        container.streams = SimpleNamespace(video=[object()], audio=[])
        with patch.dict(sys.modules, {"av": fake_av}):
            with self.assertRaises(bookends.BookendError):
                bookends.probe_media(None, Path("clip.mp4"))
        container.decode.assert_not_called()

    def test_prepare_bookend_spec_records_real_clip_durations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            intro = root / "intro.mp4"
            outro = root / "outro.mp4"
            intro.write_bytes(b"intro")
            outro.write_bytes(b"outro")
            with patch.object(bookends, "probe_media", side_effect=[
                {"duration_ms": 4000, "width": 1080, "height": 1920, "fps": 24.0},
                {"duration_ms": 4000, "width": 1080, "height": 1920, "fps": 24.0},
            ]):
                spec = bookends.prepare_bookend_spec({
                    "bookends": {"enabled": True, "intro": str(intro), "outro": str(outro)},
                }, "ffprobe")
            self.assertEqual(spec["prefix_duration_ms"], 4000)
            self.assertEqual(spec["suffix_duration_ms"], 4000)
            self.assertEqual(spec["fit_mode"], "contain")

    def test_compose_bookends_passes_all_three_media_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "bookended.mp4"
            spec = {
                "intro": str(root / "intro.mp4"),
                "outro": str(root / "outro.mp4"),
                "prefix_duration_ms": 4000,
                "suffix_duration_ms": 4000,
                "fit_mode": "contain",
                "background_color": "#10141c",
            }
            for name in ("intro.mp4", "main.mp4", "outro.mp4"):
                (root / name).write_bytes(b"media")

            def fake_run(command, **kwargs):
                output.write_bytes(b"done")
                return type("Result", (), {"returncode": 0, "stderr": ""})()

            with patch.object(bookends.subprocess, "run", side_effect=fake_run) as run:
                total = bookends.compose_bookends(
                    "ffmpeg",
                    root / "main.mp4",
                    output,
                    spec,
                    {"duration_ms": 5000, "width": 1920, "height": 1080, "fps": 30.0},
                    root,
                )
            command = run.call_args.args[0]
            self.assertEqual(total, 13000)
            self.assertEqual(command.count("-i"), 3)
            filters = command[command.index("-filter_complex") + 1]
            self.assertEqual(filters.count("atrim=duration="), 4)
            self.assertEqual(filters.count("trim=duration="), 8)
            self.assertEqual(filters.count("tpad=stop_mode=clone:stop_duration="), 3)
            self.assertEqual(command[command.index("-t") + 1], "13.000000")
            self.assertNotIn("-shortest", command)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
