from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "prepare_env.py"
SPEC = importlib.util.spec_from_file_location("prepare_env_under_test", MODULE_PATH)
prepare_env = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(prepare_env)


def arguments(*, providers: list[str], alignment: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        with_provider=providers,
        install_alignment=alignment,
        install_ffmpeg=False,
        install_link_tools=False,
        install_extraction=False,
    )


class PrepareEnvironmentTests(unittest.TestCase):
    def test_piper_provider_installs_chinese_extra(self) -> None:
        self.assertEqual(
            prepare_env.OPTIONAL_DEPS["piper"]["piper"][1],
            "piper-tts[zh]>=1.7",
        )

    def test_check_only_never_rewrites_readiness_stamp(self) -> None:
        fake_python = Path("python")
        with patch.object(prepare_env.sys, "argv", ["prepare_env.py", "--check"]), patch.object(
            prepare_env, "ensure_venv", return_value=fake_python
        ), patch.object(prepare_env, "can_import", return_value=True), patch.object(
            prepare_env, "read_stamp", return_value={}
        ), patch.object(prepare_env, "stamp_is_current", return_value=True), patch.object(
            prepare_env, "write_stamp"
        ) as write_stamp, patch.object(prepare_env, "ffmpeg_status", return_value=0):
            prepare_env.main()
        write_stamp.assert_not_called()

    def test_python_minimum_accepts_newer_versions(self) -> None:
        self.assertFalse(prepare_env.supported_python_version((3, 9)))
        self.assertTrue(prepare_env.supported_python_version((3, 10)))
        self.assertTrue(prepare_env.supported_python_version((3, 13)))
        self.assertTrue(prepare_env.supported_python_version((3, 14)))
        self.assertTrue(prepare_env.supported_python_version((3, 20)))

    def test_local_voice_providers_select_alignment_dependency(self) -> None:
        for provider in ("piper", "sherpa", "voicestudio"):
            with self.subTest(provider=provider):
                groups = prepare_env.requested_groups(arguments(providers=[provider]))
                self.assertIn("alignment", groups)
        for provider in ("azure", "elevenlabs"):
            with self.subTest(provider=provider):
                self.assertNotIn("alignment", prepare_env.requested_groups(arguments(providers=[provider])))
        self.assertIn("alignment", prepare_env.requested_groups(arguments(providers=[], alignment=True)))

    def test_check_only_rejects_incompatible_existing_environment_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".venv"
            with patch.object(prepare_env, "VENV_ROOT", root), patch.object(
                prepare_env, "SKILL_ROOT", root.parent
            ):
                python = prepare_env.interpreter_path()
                python.parent.mkdir(parents=True)
                python.touch()
                with patch.object(prepare_env, "target_python_version", return_value=(3, 9)), patch.object(
                    prepare_env.shutil, "rmtree"
                ) as remove:
                    with self.assertRaises(SystemExit) as raised:
                        prepare_env.ensure_venv(check_only=True)
                self.assertEqual(raised.exception.code, 1)
                remove.assert_not_called()

    def test_normal_mode_reuses_future_python_without_speculative_upper_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".venv"
            with patch.object(prepare_env, "VENV_ROOT", root), patch.object(
                prepare_env, "SKILL_ROOT", root.parent
            ):
                python = prepare_env.interpreter_path()
                python.parent.mkdir(parents=True)
                python.touch()
                with patch.object(prepare_env, "target_python_version", return_value=(3, 14)), patch.object(
                    prepare_env.shutil, "rmtree"
                ) as remove, patch.object(prepare_env.venv, "create") as create:
                    selected = prepare_env.ensure_venv(check_only=False)
                self.assertEqual(selected, python)
                remove.assert_not_called()
                create.assert_not_called()

    def test_supported_existing_environment_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".venv"
            with patch.object(prepare_env, "VENV_ROOT", root), patch.object(
                prepare_env, "SKILL_ROOT", root.parent
            ):
                python = prepare_env.interpreter_path()
                python.parent.mkdir(parents=True)
                python.touch()
                with patch.object(prepare_env, "target_python_version", return_value=(3, 13)), patch.object(
                    prepare_env.shutil, "rmtree"
                ) as remove, patch.object(prepare_env.venv, "create") as create:
                    selected = prepare_env.ensure_venv(check_only=False)
                self.assertEqual(selected, python)
                remove.assert_not_called()
                create.assert_not_called()

    def test_rebuild_refuses_a_linked_environment_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".venv"
            root.mkdir()
            with patch.object(prepare_env, "VENV_ROOT", root), patch.object(
                prepare_env, "SKILL_ROOT", root.parent
            ), patch.object(prepare_env, "is_reparse_path", return_value=True), patch.object(
                prepare_env.shutil, "rmtree"
            ) as remove:
                with self.assertRaisesRegex(RuntimeError, "符号链接或目录联接"):
                    prepare_env.remove_generated_venv()
            remove.assert_not_called()

    def test_linked_environment_is_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".venv"
            python = root / ("Scripts/python.exe" if prepare_env.sys.platform.startswith("win") else "bin/python")
            python.parent.mkdir(parents=True)
            python.touch()
            with patch.object(prepare_env, "VENV_ROOT", root), patch.object(
                prepare_env, "SKILL_ROOT", root.parent
            ), patch.object(prepare_env, "is_reparse_path", return_value=True), patch.object(
                prepare_env, "target_python_version"
            ) as version:
                with self.assertRaises(SystemExit) as raised:
                    prepare_env.ensure_venv(check_only=False)
            self.assertEqual(raised.exception.code, 1)
            version.assert_not_called()


if __name__ == "__main__":
    unittest.main()
