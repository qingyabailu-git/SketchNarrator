#!/usr/bin/env python3
"""
流式笔迹动画 - 环境引导脚本

职责：
  1. 在 skill 目录下建立隔离的 Python 虚拟环境（已存在则复用）
  2. 核对运行所需的第三方库是否可导入
  3. 自动补齐缺失的库
  4. 末行打印 ENV_PY=<解释器路径>，供上层调用方捕获

用法：
  python prepare_env.py          # 建环境 + 补依赖，输出 ENV_PY
  python prepare_env.py --check  # 仅探测，缺东西就以非零码退出
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import venv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from network_safety import safe_diagnostic

# skill 根目录 = 本脚本向上两级
SKILL_ROOT = Path(__file__).resolve().parent.parent
VENV_ROOT = SKILL_ROOT / ".venv"
READY_STAMP = VENV_ROOT / ".sketchnarrator-ready.json"
MIN_PYTHON = (3, 10)

# 解释器导入名 -> (distribution 名, pip 约束)。约束与根目录 pyproject.toml 同步，
# tests/test_single_skill_layout.py 会阻止两处发生静默漂移。
CORE_DEPS: dict[str, tuple[str, str]] = {
    "cv2": ("opencv-python-headless", "opencv-python-headless>=4.9"),
    "numpy": ("numpy", "numpy>=1.26"),
    "av": ("av", "av>=12"),
    "PIL": ("Pillow", "Pillow>=10"),
    "edge_tts": ("edge-tts", "edge-tts>=7.2"),
}
OPTIONAL_DEPS: dict[str, dict[str, tuple[str, str]]] = {
    "alignment": {
        "faster_whisper": ("faster-whisper", "faster-whisper>=1.1"),
    },
    "extraction": {
        "faster_whisper": ("faster-whisper", "faster-whisper>=1.1"),
        "yt_dlp": ("yt-dlp", "yt-dlp>=2025.1"),
        "opencc": ("opencc-python-reimplemented", "opencc-python-reimplemented>=0.1.7"),
    },
    "ffmpeg": {
        "imageio_ffmpeg": ("imageio-ffmpeg", "imageio-ffmpeg>=0.6"),
    },
    "piper": {
        "piper": ("piper-tts", "piper-tts[zh]>=1.7"),
    },
    "azure": {
        "azure.cognitiveservices.speech": (
            "azure-cognitiveservices-speech",
            "azure-cognitiveservices-speech>=1.51",
        ),
    },
    "elevenlabs": {
        "elevenlabs": ("elevenlabs", "elevenlabs>=2.65"),
    },
    "sherpa": {
        "sherpa_onnx": ("sherpa-onnx", "sherpa-onnx>=1.13.5"),
        "soundfile": ("soundfile", "soundfile>=0.13"),
    },
}
ALIGNMENT_PROVIDERS = {"piper", "sherpa", "voicestudio"}


def interpreter_path() -> Path:
    """虚拟环境里的 python 可执行文件位置（跨平台）。"""
    if sys.platform.startswith("win"):
        return VENV_ROOT / "Scripts" / "python.exe"
    return VENV_ROOT / "bin" / "python"


def supported_python_version(version: tuple[int, int]) -> bool:
    return version >= MIN_PYTHON


def target_python_version(py: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [str(py), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        major, minor = result.stdout.strip().split(".", 1)
        return int(major), int(minor)
    except ValueError:
        return None


def is_reparse_path(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def remove_generated_venv() -> None:
    """Remove only this renderer's real, non-linked ``.venv`` directory."""
    if VENV_ROOT.name != ".venv" or VENV_ROOT.parent.resolve() != SKILL_ROOT.resolve():
        raise RuntimeError(f"拒绝删除非本 Skill 的环境目录：{VENV_ROOT}")
    if is_reparse_path(VENV_ROOT):
        raise RuntimeError(f"拒绝删除符号链接或目录联接形式的环境：{VENV_ROOT}")
    shutil.rmtree(VENV_ROOT)


def ensure_venv(check_only: bool) -> Path:
    py = interpreter_path()
    if VENV_ROOT.exists() and is_reparse_path(VENV_ROOT):
        print(
            f"[err] 隔离环境根目录不能是符号链接或目录联接：{VENV_ROOT}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if VENV_ROOT.exists() and py.exists():
        version = target_python_version(py)
        if version is not None and supported_python_version(version):
            print(f"[ok] 复用现有虚拟环境: {VENV_ROOT}")
            return py
        detail = "无法读取版本" if version is None else f"Python {version[0]}.{version[1]}"
        if check_only:
            print(f"[err] 现有虚拟环境不受支持（{detail}）: {VENV_ROOT}")
            raise SystemExit(1)
        print(f"[..] 重建不受支持的虚拟环境（{detail}）: {VENV_ROOT}")
        try:
            remove_generated_venv()
        except RuntimeError as exc:
            print(f"[err] {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    elif VENV_ROOT.exists():
        if check_only:
            print(f"[err] 虚拟环境缺少 Python 解释器: {VENV_ROOT}")
            raise SystemExit(1)
        print(f"[..] 重建损坏的虚拟环境: {VENV_ROOT}")
        try:
            remove_generated_venv()
        except RuntimeError as exc:
            print(f"[err] {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    if check_only:
        print(f"[err] 虚拟环境尚未建立: {VENV_ROOT}")
        raise SystemExit(1)

    print(f"[..] 建立虚拟环境: {VENV_ROOT}")
    venv.create(str(VENV_ROOT), with_pip=True)
    print("[ok] 虚拟环境就绪")
    return py


def ensure_supported_python() -> None:
    if not supported_python_version((sys.version_info.major, sys.version_info.minor)):
        print("[err] SketchNarrator 需要 Python 3.10 或更新版本", file=sys.stderr)
        raise SystemExit(2)


def dependency_signature(deps: dict[str, tuple[str, str]] | None = None) -> str:
    selected = deps or CORE_DEPS
    specs = sorted(item[1] for item in selected.values())
    return hashlib.sha256("\n".join(specs).encode("utf-8")).hexdigest()


def read_stamp() -> dict[str, object]:
    try:
        return json.loads(READY_STAMP.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def target_environment(py: Path, deps: dict[str, tuple[str, str]]) -> tuple[str, dict[str, str]]:
    distributions = sorted({item[0] for item in deps.values()})
    probe = subprocess.run(
        [
            str(py),
            "-c",
            (
                "import importlib.metadata as m,json,sys\n"
                "names=json.loads(sys.argv[1])\n"
                "def version(name):\n"
                "    try:\n"
                "        return m.version(name)\n"
                "    except m.PackageNotFoundError:\n"
                "        return ''\n"
                "print(json.dumps({'python':f'{sys.version_info.major}.{sys.version_info.minor}',"
                "'versions':{name:version(name) for name in names}},sort_keys=True))\n"
            ),
            json.dumps(distributions),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        # Keep the readiness check deterministic without importing target
        # packages into the bootstrap interpreter.
        versions: dict[str, str] = {}
        for distribution in distributions:
            item = subprocess.run(
                [
                    str(py),
                    "-c",
                    "import importlib.metadata as m,sys; print(m.version(sys.argv[1]))",
                    distribution,
                ],
                capture_output=True,
                text=True,
            )
            versions[distribution] = item.stdout.strip() if item.returncode == 0 else ""
        version_probe = subprocess.run(
            [str(py), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
        )
        return version_probe.stdout.strip(), versions
    payload = json.loads(probe.stdout)
    return str(payload["python"]), {str(k): str(v) for k, v in payload["versions"].items()}


def stamp_is_current(
    py: Path,
    stamp: dict[str, object],
    groups: set[str],
    deps: dict[str, tuple[str, str]],
) -> bool:
    python_version, versions = target_environment(py, deps)
    recorded_groups = {str(item) for item in stamp.get("installed_groups", [])}
    signature_deps = dict(CORE_DEPS)
    for group in recorded_groups:
        signature_deps.update(OPTIONAL_DEPS.get(group, {}))
    if stamp.get("dependency_signature") != dependency_signature(signature_deps):
        return False
    if stamp.get("python") != python_version:
        return False
    if not groups.issubset(recorded_groups):
        return False
    recorded_versions = stamp.get("versions")
    return isinstance(recorded_versions, dict) and all(
        recorded_versions.get(name) == version for name, version in versions.items()
    )


def write_stamp(py: Path, groups: set[str], deps: dict[str, tuple[str, str]]) -> None:
    previous = read_stamp()
    installed_groups = {str(item) for item in previous.get("installed_groups", [])} | groups
    all_deps = dict(CORE_DEPS)
    for group in installed_groups:
        all_deps.update(OPTIONAL_DEPS.get(group, {}))
    python_version, versions = target_environment(py, all_deps)
    READY_STAMP.write_text(
        json.dumps(
            {
                "version": 1,
                "python": python_version,
                "dependency_signature": dependency_signature(all_deps),
                "installed_groups": sorted(installed_groups),
                "versions": versions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def can_import(py: Path, import_name: str) -> bool:
    probe = subprocess.run(
        [str(py), "-c", f"import {import_name}"],
        capture_output=True,
    )
    return probe.returncode == 0


def install(py: Path, packages: list[str], upgrade: bool = False) -> bool:
    if not packages:
        return True
    pip_probe = subprocess.run(
        [str(py), "-m", "pip", "--version"],
        capture_output=True,
        text=True,
    )
    if pip_probe.returncode != 0:
        print("[..] 隔离环境缺少 pip，先用 ensurepip 修复")
        bootstrap = subprocess.run(
            [str(py), "-m", "ensurepip", "--upgrade"],
            capture_output=True,
            text=True,
        )
        if bootstrap.returncode != 0:
            print(
                "[err] 无法在隔离环境中建立 pip；请删除损坏的 .venv 后重新准备环境，"
                "或让 Codex 使用 uv --python 指向该解释器安装。\n"
                + safe_diagnostic(bootstrap.stderr)
            )
            return False
    print(f"[..] 安装依赖: {', '.join(packages)}")
    res = subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", "--no-cache-dir", *(["--upgrade"] if upgrade else []), *packages],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"[err] 安装失败:\n{safe_diagnostic(res.stderr)}")
        return False
    print("[ok] 依赖安装完成")
    return True


def ffmpeg_status(py: Path, require: bool = False, encode_check: bool = False) -> int:
    command = [str(py), str(SKILL_ROOT / "scripts" / "ffmpeg_runtime.py")]
    if require:
        command.append("--require")
    if encode_check:
        command.append("--encode-check")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout.strip():
        encoding = sys.stdout.encoding or "utf-8"
        print(result.stdout.rstrip().encode(encoding, errors="replace").decode(encoding), file=sys.stdout)
    if result.stderr.strip():
        encoding = sys.stderr.encoding or "utf-8"
        print(result.stderr.rstrip().encode(encoding, errors="replace").decode(encoding), file=sys.stderr)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="准备白板渲染器的隔离环境")
    parser.add_argument("--upgrade", action="store_true", help="明确升级选中的依赖组；日常启动不自动升级")
    parser.add_argument("--check", action="store_true", help="只检查，不安装任何依赖")
    parser.add_argument(
        "--install-ffmpeg",
        action="store_true",
        help="经用户明确同意后，把 imageio-ffmpeg 安装到 Skill 自己的 .venv",
    )
    parser.add_argument(
        "--require-ffmpeg",
        action="store_true",
        help="FFmpeg 或 libx264/concat 能力缺失时返回非零",
    )
    parser.add_argument(
        "--install-link-tools",
        action="store_true",
        help="兼容参数：安装素材提取依赖",
    )
    parser.add_argument(
        "--install-alignment",
        action="store_true",
        help="安装离线或本地配音所需的 faster-whisper 对齐器",
    )
    parser.add_argument(
        "--install-extraction",
        action="store_true",
        help="安装 yt-dlp、faster-whisper 和 OpenCC",
    )
    parser.add_argument(
        "--with-provider",
        choices=["piper", "azure", "elevenlabs", "sherpa", "voicestudio"],
        action="append",
        default=[],
        help="只安装用户显式选择的可选配音后端；VoiceStudio 不需要额外 Python 依赖",
    )
    return parser


def requested_groups(args: argparse.Namespace) -> set[str]:
    providers = set(args.with_provider)
    groups = providers - {"voicestudio"}
    if providers & ALIGNMENT_PROVIDERS or args.install_alignment:
        groups.add("alignment")
    if args.install_ffmpeg:
        groups.add("ffmpeg")
    if args.install_link_tools or args.install_extraction:
        groups.add("extraction")
    return groups


def main(argv: list[str] | None = None) -> None:
    ensure_supported_python()
    args = build_parser().parse_args(argv)
    check_only = args.check
    if check_only and args.upgrade:
        raise SystemExit("--check 不能与 --upgrade 同时使用")

    py = ensure_venv(check_only)

    groups = requested_groups(args)
    deps = dict(CORE_DEPS)
    for group in groups:
        deps.update(OPTIONAL_DEPS[group])

    missing: list[str] = []
    for import_name, (distribution, pip_spec) in deps.items():
        if can_import(py, import_name):
            print(f"[ok] {distribution}")
        else:
            print(f"[miss] {distribution}")
            missing.append(pip_spec)

    stamp = read_stamp()
    readiness_current = stamp_is_current(py, stamp, groups, deps)

    if missing or not readiness_current or args.upgrade:
        if check_only:
            if missing:
                print(f"\n缺 {len(missing)} 个依赖: {', '.join(missing)}")
            else:
                print("\n环境就绪记录与当前 Python 或依赖版本不一致")
            sys.exit(1)
        packages = [item[1] for item in deps.values()]
        if not (install(py, packages, upgrade=True) if args.upgrade else install(py, packages)):
            sys.exit(1)
    if not check_only:
        # A successful resolver exit is insufficient to declare this environment ready.
        broken = [name for name in deps if not can_import(py, name)]
        consistency = subprocess.run([str(py), "-m", "pip", "check"], capture_output=True, text=True)
        if broken or consistency.returncode:
            print("[err] 安装后的导入或依赖一致性检查失败；未登记就绪状态", file=sys.stderr)
            print(safe_diagnostic(", ".join(broken) or consistency.stdout or consistency.stderr), file=sys.stderr)
            raise SystemExit(1)
        write_stamp(py, groups, deps)

    ffmpeg_code = ffmpeg_status(py, require=args.require_ffmpeg or args.install_ffmpeg, encode_check=args.install_ffmpeg)
    if ffmpeg_code != 0:
        print(
            "[err] 高速渲染所需 FFmpeg 能力不可用。可在明确同意后运行 "
            "SketchNarrator 的公开 setup --install-ffmpeg 流程。",
            file=sys.stderr,
        )
        sys.exit(ffmpeg_code)

    # 末行：供调用方捕获的约定输出
    print(f"\nENV_PY={py}")


if __name__ == "__main__":
    main()
