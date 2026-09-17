#!/usr/bin/env python3
"""Load optional machine-local defaults without bundling them in the Skill."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


LOCAL_SETTINGS_ENV = "SKETCHNARRATOR_LOCAL_SETTINGS"


class LocalDefaultsError(ValueError):
    """Raised when an explicitly configured local default is invalid."""


def _settings_candidates(skill_root: Path) -> list[Path]:
    configured = os.environ.get(LOCAL_SETTINGS_ENV, "").strip()
    if configured:
        return [Path(configured).expanduser()]
    # The default location is beside the public Skill, never inside it.
    return [skill_root.resolve().parent / ".local" / "settings.json"]


def load_local_settings(skill_root: Path) -> tuple[Path | None, dict[str, Any]]:
    """Return the first configured external settings file, if present."""

    candidates = _settings_candidates(skill_root)
    configured = bool(os.environ.get(LOCAL_SETTINGS_ENV, "").strip())
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalDefaultsError(f"本机默认配置无法读取：{path}：{exc}") from exc
        if not isinstance(payload, dict):
            raise LocalDefaultsError(f"本机默认配置顶层必须是对象：{path}")
        return path.resolve(), payload
    if configured:
        raise LocalDefaultsError(f"SKETCHNARRATOR_LOCAL_SETTINGS 指向的文件不存在：{candidates[0]}")
    return None, {}


def _resolve_file(settings_path: Path, raw: Any, label: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise LocalDefaultsError(f"本机默认配置缺少 {label}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = settings_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise LocalDefaultsError(f"本机默认配置中的 {label} 不存在：{path}")
    return path


def apply_local_defaults(project: dict[str, Any], skill_root: Path) -> dict[str, Any]:
    """Apply optional external defaults to a newly initialized private project.

    The returned project may contain resolved private paths because it is a
    production project artifact. No private value is stored in this module.
    Explicit project values always win over local defaults.
    """

    settings_path, settings = load_local_settings(skill_root)
    if settings_path is None:
        return project

    result = copy.deepcopy(project)
    sfx = settings.get("sfx")
    if isinstance(sfx, dict):
        profile = dict(result.get("sfx_profile") or {})
        if not profile.get("manifest_path") and sfx.get("default_manifest"):
            profile["manifest_path"] = str(_resolve_file(settings_path, sfx["default_manifest"], "sfx.default_manifest"))
        if "enabled" in sfx and "enabled" not in profile:
            profile["enabled"] = bool(sfx["enabled"])
        if "gain_db_overrides" in sfx and "gain_db_overrides" not in profile:
            profile["gain_db_overrides"] = copy.deepcopy(sfx["gain_db_overrides"])
        result["sfx_profile"] = profile

    presenter = settings.get("presenter")
    if isinstance(presenter, dict):
        manifest_value = presenter.get("manifest_path") or presenter.get("manifest")
        if manifest_value:
            manifest = _resolve_file(settings_path, manifest_value, "presenter.manifest_path")
            result["presenter_profile"] = {
                **dict(result.get("presenter_profile") or {}),
                "manifest": str(manifest),
                "source": "external-local",
            }
            renderer_profile = dict(result.get("renderer_profile") or {})
            renderer_profile.setdefault("presenter_manifest", str(manifest))
            result["renderer_profile"] = renderer_profile

        caption_font = presenter.get("caption_font")
        font = settings.get("font") if isinstance(settings.get("font"), dict) else caption_font
        if isinstance(font, dict) and font.get("path"):
            font_path = _resolve_file(settings_path, font["path"], "font.path")
            result["font_profile"] = {
                "path": str(font_path),
                "family": str(font.get("family") or ""),
                "style": str(font.get("style") or ""),
                "source": "external-local",
            }

    font = settings.get("font")
    if isinstance(font, dict) and font.get("path"):
        font_path = _resolve_file(settings_path, font["path"], "font.path")
        result["font_profile"] = {
            "path": str(font_path),
            "family": str(font.get("family") or ""),
            "style": str(font.get("style") or ""),
            "source": "external-local",
        }

    bookends = settings.get("bookends")
    if not isinstance(bookends, dict) and isinstance(presenter, dict):
        bookends = presenter.get("default_bookends")
    if isinstance(bookends, dict) and bookends.get("enabled", True):
        intro = _resolve_file(settings_path, bookends.get("intro"), "bookends.intro")
        outro = _resolve_file(settings_path, bookends.get("outro"), "bookends.outro")
        result["bookends"] = {
            "enabled": True,
            "intro": str(intro),
            "outro": str(outro),
            "fit_mode": str(bookends.get("fit_mode") or "contain"),
            "background_color": str(bookends.get("background_color") or "#10141c"),
            "source": "external-local",
        }

    title_cards = settings.get("title_cards")
    if isinstance(title_cards, dict):
        profile = dict(result.get("title_card_profile") or {})
        for key in (
            "enabled",
            "required_per_scene",
            "position",
            "style",
            "accent_palette",
            "max_text_chars",
        ):
            if key in title_cards and key not in profile:
                profile[key] = copy.deepcopy(title_cards[key])
        profile.setdefault("source", "external-local")
        result["title_card_profile"] = profile

    return result
