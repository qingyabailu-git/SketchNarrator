#!/usr/bin/env python3
"""Create or verify SketchNarrator's deterministic public release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "release-manifest.json"
MANIFEST_VERSION = 3
MANIFEST_LAYOUT = "single-skill-with-bundled-renderer"
EXCLUDED = [
    "private Presenter identity packs",
    "machine-local settings and private media bindings",
    "virtual environments, build products, and caches",
    "rendered or downloaded audio and video except the declared public CC0 SFX preset",
    "release-manifest.json itself",
]

PUBLIC_ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "SKILL.md",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
}
PUBLIC_DIRS = {
    ".github",
    "agents",
    "assets",
    "licenses",
    "references",
    "renderer",
    "scripts",
    "tests",
}
LOCAL_DIR_NAMES = {
    ".git",
    ".local",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".uv-cache",
    "build",
    "dist",
    "g2pW",
}
PRIVATE_PATHS = {
    "renderer/assets/presenter.json",
}
PRIVATE_PREFIXES = (
    "renderer/assets/presenter/",
)
GENERATED_MEDIA_SUFFIXES = {
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".webm",
}
PUBLIC_SUFFIXES = {
    "",
    ".cmd",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".jpg",
    ".jpeg",
    ".json",
    ".md",
    ".png",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".wav",
    ".yaml",
    ".yml",
}
TEXT_SUFFIXES = PUBLIC_SUFFIXES - {".jpg", ".jpeg", ".png", ".wav"}
PORTABLE_ABSOLUTE_PATH_EXAMPLES = {
    "C:" + "\\Windows",
    "C:" + "/Users/example",
    "C:" + "/portable-fonts",
    "D:" + "\\你的项目",
    "D:" + "/my-private-sfx",
}
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s'\"`<>|]+")
POSIX_HOME_PATH = re.compile(
    r"(?<![A-Za-z0-9_])/(?:Users|home)/[^/\s'\"`<>|]+(?:/[^\s'\"`<>|]+)+"
)
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "openai-key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "azure-speech-key": re.compile(
        r"(?i)\bAZURE_SPEECH_KEY\b\s*[:=]\s*['\"]?[A-Za-z0-9+/=_-]{20,}"
    ),
    "elevenlabs-api-key": re.compile(
        r"(?i)\bELEVENLABS_API_KEY\b\s*[:=]\s*['\"]?[A-Za-z0-9_-]{20,}"
    ),
    "bearer-token": re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
}


def whole_tree_private_paths(root: Path = ROOT) -> list[str]:
    """List machine-local content that makes whole-directory ZIP publishing unsafe."""
    root = root.resolve()
    problems: list[str] = []
    for path in root.rglob("*"):
        try:
            relative = _relative(path, root)
        except ValueError:
            continue
        if path.name in LOCAL_DIR_NAMES or path.name.endswith(".egg-info"):
            problems.append(f"machine-local-path:{relative}")
            if path.is_dir():
                # rglob will still enumerate children, so only report the root
                # of each excluded tree to keep the output actionable.
                continue
        if path.is_file() and path.suffix.casefold() in {".pyc", ".pyo"}:
            problems.append(f"machine-local-path:{relative}")
    roots: list[str] = []
    for problem in sorted(set(problems)):
        relative = problem.split(":", 1)[1]
        if any(relative == kept or relative.startswith(kept + "/") for kept in roots):
            continue
        roots.append(relative)
    return [f"machine-local-path:{relative}" for relative in roots]


class DuplicateJsonKey(ValueError):
    """Raised when release-manifest.json contains an ambiguous object."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(key)
        result[key] = value
    return result


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_reparse(path: Path) -> bool:
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


def _is_local_dir(path: Path) -> bool:
    return path.name in LOCAL_DIR_NAMES or path.name.endswith(".egg-info")


def _is_private(relative: str) -> bool:
    return relative in PRIVATE_PATHS or any(
        relative == prefix.rstrip("/") or relative.startswith(prefix)
        for prefix in PRIVATE_PREFIXES
    )


def _declared_sfx_wavs(root: Path, problems: list[str]) -> set[str]:
    manifest_path = root / "assets" / "sfx" / "classic-light" / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        sources = payload["sources"]
        if not isinstance(sources, list):
            raise TypeError("sources")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        problems.append("invalid-public-sfx-manifest:assets/sfx/classic-light/manifest.json")
        return set()

    declared: set[str] = set()
    for source in sources:
        files = source.get("files") if isinstance(source, dict) else None
        if not isinstance(files, list):
            problems.append("invalid-public-sfx-source:assets/sfx/classic-light/manifest.json")
            continue
        for name in files:
            if not isinstance(name, str) or Path(name).name != name or not name.endswith(".wav"):
                problems.append("invalid-public-sfx-file:assets/sfx/classic-light/manifest.json")
                continue
            declared.add(f"assets/sfx/classic-light/{name}")
    return declared


def scan_release_tree(root: Path = ROOT) -> tuple[list[Path], list[str]]:
    """Return public files and hard hygiene failures without following links."""
    root = root.resolve()
    manifest = root / "release-manifest.json"
    problems: list[str] = []
    files: list[Path] = []
    allowed_wavs = _declared_sfx_wavs(root, problems)

    def visit(directory: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            problems.append(f"unreadable-directory:{_relative(directory, root)}")
            return
        for path in children:
            relative = _relative(path, root)
            parts = Path(relative).parts
            if path == manifest:
                if _is_reparse(path):
                    problems.append("link-or-reparse-point:release-manifest.json")
                continue
            if path.is_dir() and _is_local_dir(path):
                continue
            if _is_private(relative):
                problems.append(f"private-path:{relative}")
                continue
            if _is_reparse(path):
                problems.append(f"link-or-reparse-point:{relative}")
                continue
            if path.is_dir():
                if len(parts) == 1 and path.name not in PUBLIC_DIRS:
                    problems.append(f"unexpected-root-directory:{relative}")
                    continue
                visit(path)
                continue
            if not path.is_file():
                problems.append(f"unsupported-entry:{relative}")
                continue
            if len(parts) == 1 and path.name not in PUBLIC_ROOT_FILES:
                problems.append(f"unexpected-root-file:{relative}")
                continue
            if path.name.startswith(".env"):
                problems.append(f"private-settings:{relative}")
                continue
            suffix = path.suffix.casefold()
            if suffix in GENERATED_MEDIA_SUFFIXES or suffix in {".pyc", ".pyo"}:
                problems.append(f"generated-output:{relative}")
                continue
            if suffix not in PUBLIC_SUFFIXES:
                problems.append(f"unsupported-public-file:{relative}")
                continue
            if suffix == ".wav" and relative not in allowed_wavs:
                problems.append(f"undeclared-public-wav:{relative}")
                continue
            files.append(path)

    visit(root)
    public_wavs = {_relative(path, root) for path in files if path.suffix.casefold() == ".wav"}
    for relative in sorted(allowed_wavs - public_wavs):
        problems.append(f"missing-declared-public-wav:{relative}")
    files.sort(key=lambda path: _relative(path, root))
    return files, sorted(set(problems))


def public_files(root: Path = ROOT) -> list[Path]:
    files, problems = scan_release_tree(root)
    if problems:
        raise ValueError("; ".join(problems))
    return files


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: Path = ROOT) -> list[dict[str, object]]:
    root = root.resolve()
    return [
        {
            "path": _relative(path, root),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in public_files(root)
    ]


def public_hygiene_problems(root: Path = ROOT) -> list[str]:
    """Reject private/generated content, non-LF text, and developer-machine paths."""
    root = root.resolve()
    files, problems = scan_release_tree(root)
    for path in files:
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        relative = _relative(path, root)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            problems.append(f"invalid-utf8:{relative}")
            continue
        if b"\r" in raw:
            problems.append(f"line-ending:{relative}:expected-lf")
        normalized = text.replace("\\\\", "\\")
        for match in WINDOWS_ABSOLUTE_PATH.finditer(normalized):
            value = match.group(0).rstrip(".,;:)]}")
            if any(value.startswith(allowed) for allowed in PORTABLE_ABSOLUTE_PATH_EXAMPLES):
                continue
            problems.append(f"absolute-path:{relative}:{value}")
        for match in POSIX_HOME_PATH.finditer(normalized):
            problems.append(f"absolute-path:{relative}:{match.group(0)}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"secret-pattern:{relative}:{label}")
    return sorted(set(problems))


def manifest_payload(root: Path = ROOT) -> dict[str, object]:
    return {
        "version": MANIFEST_VERSION,
        "project": "SketchNarrator",
        "skillId": "sketch-narrator",
        "layout": MANIFEST_LAYOUT,
        "files": snapshot(root),
        "excluded": EXCLUDED,
    }


def _manifest_structure_problems(payload: object) -> list[str]:
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["schema:top-level-object"]
    expected_keys = {"version", "project", "skillId", "layout", "files", "excluded"}
    if set(payload) != expected_keys:
        problems.append("schema:top-level-keys")
    if payload.get("version") != MANIFEST_VERSION:
        problems.append("schema:version")
    if payload.get("project") != "SketchNarrator":
        problems.append("schema:project")
    if payload.get("skillId") != "sketch-narrator":
        problems.append("schema:skill-id")
    if payload.get("layout") != MANIFEST_LAYOUT:
        problems.append("schema:layout")
    if payload.get("excluded") != EXCLUDED:
        problems.append("schema:excluded")
    rows = payload.get("files")
    if not isinstance(rows, list):
        problems.append("schema:files-list")
        return problems
    seen: set[str] = set()
    order: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "bytes"}:
            problems.append(f"schema:file-row:{index}")
            continue
        relative = row.get("path")
        digest = row.get("sha256")
        size = row.get("bytes")
        if not isinstance(relative, str) or not relative or "\\" in relative or relative.startswith("/"):
            problems.append(f"schema:file-path:{index}")
            continue
        if relative in seen:
            problems.append(f"duplicate:{relative}")
        seen.add(relative)
        order.append(relative)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            problems.append(f"schema:file-sha256:{relative}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            problems.append(f"schema:file-bytes:{relative}")
    if order != sorted(order):
        problems.append("schema:file-order")
    return problems


def write_manifest(root: Path = ROOT, manifest: Path | None = None) -> None:
    root = root.resolve()
    manifest = manifest or root / "release-manifest.json"
    hygiene = public_hygiene_problems(root)
    if hygiene:
        raise ValueError("; ".join(hygiene))
    rendered = json.dumps(manifest_payload(root), ensure_ascii=False, indent=2) + "\n"
    manifest.write_text(rendered, encoding="utf-8", newline="\n")


def check_manifest(root: Path = ROOT, manifest: Path | None = None) -> list[str]:
    root = root.resolve()
    manifest = manifest or root / "release-manifest.json"
    problems = public_hygiene_problems(root)
    if not manifest.is_file():
        return sorted(set(problems + ["missing:release-manifest.json"]))
    try:
        payload = json.loads(
            manifest.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except DuplicateJsonKey as exc:
        return sorted(set(problems + [f"duplicate-json-key:{exc}"]))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return sorted(set(problems + ["invalid:release-manifest.json"]))

    problems.extend(_manifest_structure_problems(payload))
    if not problems:
        expected = manifest_payload(root)
        recorded_rows = payload["files"]
        expected_rows = expected["files"]
        recorded = {row["path"]: row for row in recorded_rows}
        current = {row["path"]: row for row in expected_rows}
        for relative in sorted(current.keys() - recorded.keys()):
            problems.append(f"missing:{relative}")
        for relative in sorted(recorded.keys() - current.keys()):
            problems.append(f"stale:{relative}")
        for relative in sorted(current.keys() & recorded.keys()):
            if recorded[relative]["sha256"] != current[relative]["sha256"]:
                problems.append(f"different:{relative}")
            if recorded[relative]["bytes"] != current[relative]["bytes"]:
                problems.append(f"different-bytes:{relative}")
        for key in ("version", "project", "skillId", "layout", "excluded"):
            if payload[key] != expected[key]:
                problems.append(f"different-metadata:{key}")
    return sorted(set(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--check-whole-tree",
        action="store_true",
        help="检查当前开发目录能否被整目录打包；本机环境或缓存存在时失败",
    )
    args = parser.parse_args()
    if args.check_whole_tree:
        problems = check_manifest() + whole_tree_private_paths()
        problems = sorted(set(problems))
        print(f"WHOLE_TREE_CHECK={'OK' if not problems else 'FAILED'}")
        for problem in problems:
            print(problem)
        return 0 if not problems else 1
    if args.check:
        problems = check_manifest()
        print(f"CHECK={'OK' if not problems else 'FAILED'}")
        for problem in problems:
            print(problem)
        return 0 if not problems else 1

    hygiene = public_hygiene_problems()
    if hygiene:
        print("HYGIENE=FAILED")
        for problem in hygiene:
            print(problem)
        return 1
    write_manifest()
    files = snapshot()
    print(f"MANIFEST={MANIFEST}")
    print(f"FILES={len(files)}")
    print(f"BYTES={sum(int(row['bytes']) for row in files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
