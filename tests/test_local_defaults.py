from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from local_defaults import apply_local_defaults  # noqa: E402


class LocalDefaultsTests(unittest.TestCase):
    def test_defaults_are_loaded_from_workspace_private_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            skill_root = workspace / "SketchNarrator"
            settings_root = workspace / ".local"
            settings_root.mkdir()
            sfx = settings_root / "sfx-manifest.json"
            presenter = settings_root / "presenter.json"
            intro = settings_root / "intro.mp4"
            outro = settings_root / "outro.mp4"
            font = settings_root / "caption.ttf"
            for path in (sfx, presenter, intro, outro, font):
                path.write_bytes(b"private-test-asset")
            (settings_root / "settings.json").write_text(json.dumps({
                "sfx": {"default_manifest": "sfx-manifest.json"},
                "presenter": {
                    "manifest_path": "presenter.json",
                    "caption_font": {"family": "Test Family", "style": "Bold", "path": "caption.ttf"},
                },
                "bookends": {"intro": "intro.mp4", "outro": "outro.mp4"},
            }), encoding="utf-8")
            project = {"renderer_profile": {}, "sfx_profile": {"enabled": True}}
            with patch.dict(os.environ, {}, clear=True):
                result = apply_local_defaults(project, skill_root)
            self.assertEqual(result["sfx_profile"]["manifest_path"], str(sfx.resolve()))
            self.assertEqual(result["renderer_profile"]["presenter_manifest"], str(presenter.resolve()))
            self.assertEqual(result["font_profile"]["style"], "Bold")
            self.assertEqual(result["bookends"]["intro"], str(intro.resolve()))
            self.assertNotIn("bookends", project)

    def test_explicit_environment_path_overrides_workspace_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = root / "settings.json"
            settings.write_text(json.dumps({"sfx": {}}), encoding="utf-8")
            with patch.dict(os.environ, {"SKETCHNARRATOR_LOCAL_SETTINGS": str(settings)}):
                path, payload = __import__("local_defaults").load_local_settings(root / "skill")
            self.assertEqual(path, settings.resolve())
            self.assertEqual(payload["sfx"], {})


if __name__ == "__main__":
    unittest.main()
