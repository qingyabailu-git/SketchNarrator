import json
import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def gitignore_matches(relative: str) -> bool:
    """Check the shipped ignore rules without requiring release users to have .git history."""
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory)
        shutil.copy2(ROOT / ".gitignore", repository / ".gitignore")
        initialized = subprocess.run(
            ["git", "init", "--quiet"], cwd=repository, check=False
        )
        if initialized.returncode != 0:
            return False
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", relative],
            cwd=repository,
            check=False,
        )
        return result.returncode == 0


class SingleSkillLayoutTests(unittest.TestCase):
    def test_only_one_skill_entrypoint_exists(self):
        skill_files = [
            path
            for path in ROOT.rglob("SKILL.md")
            if ".venv" not in path.parts and ".git" not in path.parts and ".local" not in path.parts
        ]
        self.assertEqual(skill_files, [ROOT / "SKILL.md"])
        header = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: sketch-narrator", header)

    def test_renderer_is_bundled_without_legacy_runtime_fallback(self):
        workflow = (ROOT / "scripts" / "workflow.py").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "renderer" / "scripts" / "render_stream_whiteboard.py").is_file())
        self.assertNotIn('skills" / "srt-whiteboard-animation"', workflow)
        self.assertNotIn("SRT_WHITEBOARD_SKILL", workflow)

    def test_panel_has_no_unauthenticated_static_redirect_alias(self):
        self.assertTrue((ROOT / "renderer" / "assets" / "panel.html").is_file())
        self.assertFalse((ROOT / "renderer" / "assets" / "preview.html").exists())

    def test_release_manifest_contract_is_available_for_online_release(self):
        manifest = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["skillId"], "sketch-narrator")
        self.assertTrue((ROOT / "scripts" / "update_release_manifest.py").is_file())

    def test_public_launchers_are_cross_platform_and_portable(self):
        windows = (ROOT / "scripts" / "run.cmd").read_text(encoding="utf-8")
        posix = (ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        self.assertIn("renderer\\.venv\\Scripts\\python.exe", windows)
        self.assertIn("renderer/.venv/bin/python", posix)
        for text in (windows, posix):
            self.assertNotIn("C:" + "\\Users", text)
            self.assertNotIn("D:" + "\\", text)
        if os.name != "nt":
            self.assertTrue((ROOT / "scripts" / "run.sh").stat().st_mode & stat.S_IXUSR)

    def test_bootstrap_dependency_specs_match_pyproject(self):
        module_path = ROOT / "renderer" / "scripts" / "prepare_env.py"
        spec = importlib.util.spec_from_file_location("prepare_env_contract", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        dependency_specs = [item[1] for item in module.CORE_DEPS.values()]
        for group in module.OPTIONAL_DEPS.values():
            dependency_specs.extend(item[1] for item in group.values())
        for dependency in dependency_specs:
            self.assertIn(f'"{dependency}"', pyproject)

    def test_public_release_snapshot_contains_default_sfx(self):
        module_path = ROOT / "scripts" / "update_release_manifest.py"
        spec = importlib.util.spec_from_file_location("release_manifest_contract", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        paths = {item["path"] for item in module.snapshot()}
        self.assertIn("assets/sfx/classic-light/writing-pencil.wav", paths)
        self.assertIn("assets/sfx/classic-light/manifest.json", paths)
        self.assertFalse(any(path.startswith(".local/") for path in paths))

    def test_public_release_has_no_developer_absolute_paths(self):
        module_path = ROOT / "scripts" / "update_release_manifest.py"
        spec = importlib.util.spec_from_file_location("release_hygiene_contract", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        self.assertEqual(module.public_hygiene_problems(), [])

    def test_private_presenter_assets_are_ignored_by_git(self):
        candidates = [
            "renderer/assets/presenter.json",
            "renderer/assets/presenter/identity.png",
        ]
        for relative in candidates:
            self.assertTrue(gitignore_matches(relative), relative)

    def test_machine_local_settings_are_ignored_by_git(self):
        self.assertTrue(gitignore_matches(".local/settings.json"))


if __name__ == "__main__":
    unittest.main()
