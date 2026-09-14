#!/usr/bin/env python3
"""Resolve optional Presenter asset packs without coupling core code to one IP."""

from __future__ import annotations

import json
import os
from pathlib import Path


PRESENTER_ENV = "SKETCHNARRATOR_PRESENTER_MANIFEST"
ASSETS = Path(__file__).resolve().parents[1] / "assets"
SKILL_ROOT = Path(__file__).resolve().parents[2]
LOCAL_DIR = SKILL_ROOT / ".local"
LOCAL_SETTINGS = LOCAL_DIR / "settings.json"
GENERIC_MANIFEST = ASSETS / "presenter.json"


def _local_settings_manifest() -> Path | None:
    if not LOCAL_SETTINGS.is_file():
        return None
    try:
        data = json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8"))
        rel = (data.get("presenter") or {}).get("manifest_path")
        if rel:
            target = (LOCAL_DIR / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
            if target.is_file():
                return target
    except Exception:
        pass
    return None


def resolve_presenter_manifest(explicit: str | Path | None = None) -> Path:
    """Resolve a user-owned Presenter pack from explicit or local configuration."""

    requested = explicit or os.environ.get(PRESENTER_ENV)
    if requested:
        path = Path(requested).expanduser()
        if path.is_file():
            return path
        raise RuntimeError(f"Presenter manifest 不存在：{path}")

    from_settings = _local_settings_manifest()
    if from_settings is not None:
        return from_settings

    for path in (LOCAL_DIR / "assets" / "presenter.json", GENERIC_MANIFEST):
        if path.is_file():
            return path
    raise RuntimeError(
        "未安装 Presenter 资产包。请提供 presenter.json，或通过环境变量 "
        f"{PRESENTER_ENV} 指向有权使用的 Presenter manifest。"
    )
