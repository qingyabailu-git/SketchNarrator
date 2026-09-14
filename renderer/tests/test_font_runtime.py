import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import font_runtime


class FontRuntimeTests(unittest.TestCase):
    def test_platform_candidates_are_not_windows_only(self):
        linux = font_runtime.candidate_fonts("linux")
        mac = font_runtime.candidate_fonts("darwin")
        self.assertTrue(any("NotoSansCJK" in str(path) for path in linux))
        self.assertTrue(any("PingFang" in str(path) for path in mac))

    def test_explicit_font_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            font = Path(directory) / "custom-font.ttf"
            font.write_bytes(b"font-placeholder")
            with patch.dict(os.environ, {font_runtime.FONT_ENV: str(font)}):
                with patch.object(font_runtime, "_usable_cjk_font", return_value=True):
                    self.assertEqual(font_runtime.find_cjk_font(), font)

    def test_missing_explicit_font_is_actionable(self):
        with self.assertRaisesRegex(RuntimeError, "指定字体不存在"):
            font_runtime.find_cjk_font("definitely-missing-font.ttf")


if __name__ == "__main__":
    unittest.main()
