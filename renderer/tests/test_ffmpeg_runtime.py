from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ffmpeg_runtime.py"
SPEC = importlib.util.spec_from_file_location("ffmpeg_runtime_test", MODULE_PATH)
ffmpeg_runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(ffmpeg_runtime)


class FFmpegRuntimeTests(unittest.TestCase):
    def test_resolver_uses_first_valid_candidate_and_records_invalid_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            invalid = Path(temp) / "invalid.exe"
            valid = Path(temp) / "valid.exe"
            valid.write_bytes(b"placeholder")
            candidates = [("explicit", invalid), ("PATH", valid)]
            with mock.patch.object(ffmpeg_runtime, "_candidate_paths", return_value=candidates), mock.patch.object(
                ffmpeg_runtime,
                "_valid_executable",
                side_effect=lambda path, version_flag="-version": path == valid,
            ):
                path, source, errors = ffmpeg_runtime.resolve_ffmpeg("ignored")
            self.assertEqual(path, valid)
            self.assertEqual(source, "PATH")
            self.assertEqual(len(errors), 1)
            self.assertIn("explicit", errors[0])

    def test_capability_probe_reads_required_features(self) -> None:
        responses = {
            "-version": "ffmpeg version test\n",
            "-encoders": " V..... libx264\n V..... h264_nvenc\n",
            "-filters": " ... subtitles V->V\n ... ass V->V\n",
            "-demuxers": " D  concat\n",
        }

        def fake_run(command, timeout=15):
            key = command[-1]
            return subprocess.CompletedProcess(command, 0, responses[key], "")

        with mock.patch.object(ffmpeg_runtime, "_run", side_effect=fake_run):
            caps = ffmpeg_runtime.probe_capabilities(Path("ffmpeg"))
        self.assertTrue(caps["libx264"])
        self.assertTrue(caps["subtitles"])
        self.assertTrue(caps["ass"])
        self.assertTrue(caps["concat_demuxer"])
        self.assertEqual(caps["hardware_h264_encoders"], ["h264_nvenc"])

    def test_runtime_info_is_safe_when_nothing_is_available(self) -> None:
        with mock.patch.object(ffmpeg_runtime, "resolve_ffmpeg", return_value=(None, None, [])), mock.patch.object(
            ffmpeg_runtime, "resolve_ffprobe", return_value=(None, None)
        ):
            info = ffmpeg_runtime.runtime_info()
        self.assertFalse(info["available"])
        self.assertFalse(info["capabilities"]["libx264"])
        self.assertFalse(info["ffprobe"]["available"])

    def test_imageio_runtime_is_a_first_class_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundled = Path(temp) / "ffmpeg.exe"
            bundled.write_bytes(b"placeholder")
            with mock.patch.object(ffmpeg_runtime, "_imageio_ffmpeg_candidate", return_value=bundled), mock.patch.object(
                ffmpeg_runtime,
                "_valid_executable",
                side_effect=lambda path, version_flag="-version": path == bundled,
            ), mock.patch.object(ffmpeg_runtime.shutil, "which", return_value=None):
                path, source, _errors = ffmpeg_runtime.resolve_ffmpeg()
        self.assertEqual(path, bundled)
        self.assertEqual(source, "imageio-ffmpeg")


if __name__ == "__main__":
    unittest.main()
