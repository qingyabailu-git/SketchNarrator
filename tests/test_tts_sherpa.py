from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_PATH = SCRIPT_DIR / "tts_sherpa.py"
SPEC = importlib.util.spec_from_file_location("tts_sherpa", MODULE_PATH)
tts_sherpa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(tts_sherpa)


class SherpaTtsTests(unittest.TestCase):
    def test_model_files_reports_incomplete_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(tts_sherpa.SherpaTtsError, "model.onnx"):
                tts_sherpa.model_files(Path(temp))

    def test_write_metadata_records_real_time_factor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sherpa.json"
            tts_sherpa.write_metadata(path, Path("model"), 2.0, 10.0, 44100, 1.0, 4)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["provider"], "sherpa")
            self.assertEqual(payload["rtf"], 0.2)
            self.assertTrue(payload["alignment_required"])


if __name__ == "__main__":
    unittest.main()
