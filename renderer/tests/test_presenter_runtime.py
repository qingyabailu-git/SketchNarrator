import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import presenter_runtime


class PresenterRuntimeTests(unittest.TestCase):
    def test_explicit_generic_presenter_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "presenter.json"
            manifest.write_text("{}", encoding="utf-8")
            self.assertEqual(presenter_runtime.resolve_presenter_manifest(manifest), manifest)

    def test_environment_manifest_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "my-presenter.json"
            manifest.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {presenter_runtime.PRESENTER_ENV: str(manifest)}):
                self.assertEqual(presenter_runtime.resolve_presenter_manifest(), manifest)


if __name__ == "__main__":
    unittest.main()
