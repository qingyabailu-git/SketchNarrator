from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


release_manifest = _load("update_release_manifest")
package_release = _load("package_release")


class PackageReleaseTests(unittest.TestCase):
    def _make_root(self, directory: str) -> Path:
        root = Path(directory)
        sfx = root / "assets" / "sfx" / "classic-light"
        sfx.mkdir(parents=True)
        (sfx / "tone.wav").write_bytes(b"RIFF-public-test")
        (sfx / "manifest.json").write_text(
            json.dumps({"sources": [{"files": ["tone.wav"]}]}),
            encoding="utf-8",
            newline="\n",
        )
        (root / "scripts").mkdir()
        (root / "scripts" / "workflow.py").write_text("print('ok')\n", encoding="utf-8", newline="\n")
        (root / "scripts" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")
        (root / "AGENTS.md").write_text("release rules\n", encoding="utf-8", newline="\n")
        (root / "pyproject.toml").write_text(
            '[project]\nname = "sketch-narrator"\nversion = "9.8.7"\n',
            encoding="utf-8",
            newline="\n",
        )
        release_manifest.write_manifest(root, root / "release-manifest.json")
        return root

    def test_manifest_package_is_deterministic_and_self_describing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._make_root(directory)
            first = root / "dist" / "first.zip"
            second = root / "dist" / "second.zip"

            result = package_release.build_release(root, first)
            package_release.build_release(root, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(result["files"], 7)
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                run_mode = archive.getinfo("sketch-narrator/scripts/run.sh").external_attr >> 16
            self.assertEqual(names, sorted(names))
            self.assertIn("sketch-narrator/release-manifest.json", names)
            self.assertEqual(run_mode & 0o777, 0o755)
            self.assertFalse(any(".local/" in name or ".venv/" in name for name in names))


if __name__ == "__main__":
    unittest.main()
