import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "update_release_manifest.py"
SPEC = importlib.util.spec_from_file_location("release_manifest", MODULE_PATH)
release_manifest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(release_manifest)


class ReleaseManifestTests(unittest.TestCase):
    def make_release_root(self, directory: str) -> Path:
        root = Path(directory)
        sfx = root / "assets" / "sfx" / "classic-light"
        sfx.mkdir(parents=True)
        (sfx / "tone.wav").write_bytes(b"RIFF-public-test")
        (sfx / "manifest.json").write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "files": ["tone.wav"],
                            "title": "test",
                            "author": "test",
                            "url": "https://example.invalid/source",
                            "license": "CC0-1.0",
                        }
                    ]
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        (root / "scripts").mkdir()
        (root / "scripts" / "workflow.py").write_text("print('ok')\n", encoding="utf-8", newline="\n")
        (root / "AGENTS.md").write_text("release rules\n", encoding="utf-8", newline="\n")
        return root

    def test_two_builds_are_byte_identical_and_paths_use_stable_posix_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            (root / "agents").mkdir()
            (root / "agents" / "openai.yaml").write_bytes(b"name: test\n")
            manifest = root / "release-manifest.json"

            release_manifest.write_manifest(root, manifest)
            first = manifest.read_bytes()
            release_manifest.write_manifest(root, manifest)
            second = manifest.read_bytes()

            self.assertEqual(first, second)
            payload = json.loads(second)
            self.assertNotIn("generatedAtUtc", payload)
            paths = [row["path"] for row in payload["files"]]
            self.assertEqual(paths, sorted(paths))
            self.assertTrue(all("\\" not in path for path in paths))

    def test_check_validates_metadata_rows_hashes_sizes_duplicates_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            manifest = root / "release-manifest.json"
            release_manifest.write_manifest(root, manifest)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["version"] = 999
            payload["excluded"] = []
            payload["files"][0]["bytes"] = -1
            payload["files"].append(dict(payload["files"][0]))
            manifest.write_text(json.dumps(payload), encoding="utf-8", newline="\n")

            problems = release_manifest.check_manifest(root, manifest)

            self.assertIn("schema:version", problems)
            self.assertIn("schema:excluded", problems)
            self.assertTrue(any(problem.startswith("schema:file-bytes:") for problem in problems))
            self.assertTrue(any(problem.startswith("duplicate:") for problem in problems))
            self.assertIn("schema:file-order", problems)

    def test_check_detects_content_and_byte_count_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            manifest = root / "release-manifest.json"
            release_manifest.write_manifest(root, manifest)
            (root / "scripts" / "workflow.py").write_bytes(b"print('changed and longer')\n")

            problems = release_manifest.check_manifest(root, manifest)

            self.assertIn("different:scripts/workflow.py", problems)
            self.assertIn("different-bytes:scripts/workflow.py", problems)

    def test_tree_audit_rejects_private_generated_unknown_and_undeclared_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            (root / "renderer" / "assets" / "presenter").mkdir(parents=True)
            (root / "renderer" / "assets" / "presenter" / "identity.png").write_bytes(b"private")
            (root / "scripts" / "render.mp4").write_bytes(b"generated")
            (root / "assets" / "sfx" / "classic-light" / "unlisted.wav").write_bytes(b"RIFF")
            (root / "scratch").mkdir()
            (root / "notes.private").write_text("unexpected", encoding="utf-8")

            _, problems = release_manifest.scan_release_tree(root)

            self.assertIn("private-path:renderer/assets/presenter", problems)
            self.assertIn("generated-output:scripts/render.mp4", problems)
            self.assertIn("undeclared-public-wav:assets/sfx/classic-light/unlisted.wav", problems)
            self.assertIn("unexpected-root-directory:scratch", problems)
            self.assertIn("unexpected-root-file:notes.private", problems)

    def test_tree_audit_rejects_invalid_utf8_and_non_lf_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            (root / "scripts" / "crlf.py").write_bytes(b"print('x')\r\n")
            (root / "scripts" / "invalid.txt").write_bytes(b"\xff\xfe")

            problems = release_manifest.public_hygiene_problems(root)

            self.assertIn("line-ending:scripts/crlf.py:expected-lf", problems)
            self.assertIn("invalid-utf8:scripts/invalid.txt", problems)

    def test_tree_audit_rejects_high_confidence_secret_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            (root / "scripts" / "leak.txt").write_bytes(
                b"token=" + b"sk-" + b"proj-" + b"abcdefghijklmnopqrstuvwxyz012345\n"
            )

            problems = release_manifest.public_hygiene_problems(root)

            self.assertIn("secret-pattern:scripts/leak.txt:openai-key", problems)

    def test_hygiene_rejects_forward_slash_paths_and_supported_provider_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            (root / "scripts" / "leak.txt").write_text(
                "source=" + "D:" + "/private/video.mp4\n"
                + "AZURE_SPEECH_KEY" + "=" + "abcdefghijklmnopqrstuvwxyz012345\n"
                + "ELEVENLABS_API_KEY" + "=" + "sk_abcdefghijklmnopqrstuvwxyz012345\n"
                + "Authorization" + ": " + "Bearer " + "abcdefghijklmnopqrstuvwxyz012345\n",
                encoding="utf-8",
                newline="\n",
            )

            problems = release_manifest.public_hygiene_problems(root)

            self.assertTrue(any(item.startswith("absolute-path:scripts/leak.txt:D:/") for item in problems))
            self.assertIn("secret-pattern:scripts/leak.txt:azure-speech-key", problems)
            self.assertIn("secret-pattern:scripts/leak.txt:elevenlabs-api-key", problems)
            self.assertIn("secret-pattern:scripts/leak.txt:bearer-token", problems)

    def test_generated_environments_and_caches_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            (root / ".venv").mkdir()
            (root / ".venv" / "secret.mp4").write_bytes(b"local")
            (root / "renderer" / ".venv").mkdir(parents=True)
            (root / "renderer" / ".venv" / "python.exe").write_bytes(b"local")
            (root / "scripts" / "__pycache__").mkdir()
            (root / "scripts" / "__pycache__" / "module.pyc").write_bytes(b"local")

            files, problems = release_manifest.scan_release_tree(root)

            self.assertEqual(problems, [])
            paths = {_path.relative_to(root).as_posix() for _path in files}
            self.assertFalse(any(".venv" in path or "__pycache__" in path for path in paths))

    def test_whole_tree_check_reports_local_runtime_roots_without_changing_manifest_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            (root / ".venv").mkdir()
            (root / ".venv" / "pyvenv.cfg").write_text("home=C:/Users/example\n", encoding="utf-8")
            (root / "scripts" / "__pycache__").mkdir()
            (root / "scripts" / "__pycache__" / "module.pyc").write_bytes(b"cache")

            _, scan_problems = release_manifest.scan_release_tree(root)
            private = release_manifest.whole_tree_private_paths(root)

            self.assertEqual(scan_problems, [])
            self.assertIn("machine-local-path:.venv", private)
            self.assertIn("machine-local-path:scripts/__pycache__", private)

    def test_symbolic_links_are_never_release_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            target = root / "scripts" / "workflow.py"
            link = root / "scripts" / "linked.py"
            try:
                os.symlink(target, link)
                context = mock.patch.object(
                    release_manifest,
                    "_is_reparse",
                    wraps=release_manifest._is_reparse,
                )
            except (OSError, NotImplementedError):
                link.write_text("linked content\n", encoding="utf-8", newline="\n")
                original = release_manifest._is_reparse
                context = mock.patch.object(
                    release_manifest,
                    "_is_reparse",
                    side_effect=lambda path: path == link or original(path),
                )

            with context:
                files, problems = release_manifest.scan_release_tree(root)

            self.assertIn("link-or-reparse-point:scripts/linked.py", problems)
            self.assertNotIn(link, files)

    def test_duplicate_json_object_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            manifest = root / "release-manifest.json"
            manifest.write_text('{"version":3,"version":3}', encoding="utf-8")

            problems = release_manifest.check_manifest(root, manifest)

            self.assertTrue(any(problem.startswith("duplicate-json-key:") for problem in problems))

    def test_manifest_itself_cannot_be_a_link_or_reparse_point(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_release_root(directory)
            manifest = root / "release-manifest.json"
            manifest.write_text("{}\n", encoding="utf-8", newline="\n")
            original = release_manifest._is_reparse
            with mock.patch.object(
                release_manifest,
                "_is_reparse",
                side_effect=lambda path: path == manifest or original(path),
            ):
                _, problems = release_manifest.scan_release_tree(root)

            self.assertIn("link-or-reparse-point:release-manifest.json", problems)


if __name__ == "__main__":
    unittest.main()
