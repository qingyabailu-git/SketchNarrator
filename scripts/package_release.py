#!/usr/bin/env python3
"""Build a deterministic public ZIP from release-manifest.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

from update_release_manifest import check_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "release-manifest.json"
ARCHIVE_ROOT = "sketch-narrator"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _project_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml 缺少项目版本号")
    return match.group(1)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_info(relative: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}", FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o100755 if relative.endswith(".sh") else 0o100644
    info.external_attr = (mode & 0xFFFF) << 16
    return info


def build_release(root: Path = ROOT, output: Path | None = None) -> dict[str, object]:
    root = root.resolve()
    manifest_path = root / MANIFEST_NAME
    problems = check_manifest(root, manifest_path)
    if problems:
        raise ValueError("发布清单校验失败：" + "; ".join(problems))

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    rows = {row["path"]: row for row in manifest["files"]}
    archive_paths = sorted([MANIFEST_NAME, *rows])
    if output is None:
        output = root / "dist" / f"sketch-narrator-{_project_version(root)}.zip"
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output.stem + ".",
        suffix=".tmp",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative in archive_paths:
                source = manifest_path if relative == MANIFEST_NAME else root / relative
                data = source.read_bytes()
                if relative != MANIFEST_NAME:
                    row = rows[relative]
                    if len(data) != row["bytes"] or _sha256(data) != row["sha256"]:
                        raise ValueError(f"打包时文件已变化：{relative}")
                archive.writestr(_zip_info(relative), data, compresslevel=9)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    archive_bytes = output.read_bytes()
    return {
        "output": str(output),
        "files": len(archive_paths),
        "sha256": _sha256(archive_bytes),
        "bytes": len(archive_bytes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="输出 ZIP；默认写入 dist/")
    args = parser.parse_args()
    result = build_release(output=args.output)
    print(f"PACKAGE={result['output']}")
    print(f"FILES={result['files']}")
    print(f"SHA256={result['sha256']}")
    print(f"BYTES={result['bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
