#!/usr/bin/env python3
"""Cross-platform CJK font discovery for captions and preview images."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


FONT_ENV = "SKETCHNARRATOR_FONT"


@lru_cache(maxsize=64)
def _has_cjk_glyphs(path: str, modified_ns: int, byte_size: int) -> bool:
    """Reject unreadable fonts and missing-glyph fallbacks using basic Chinese."""
    try:
        font = ImageFont.truetype(path, 32)

        def signature(text: str) -> tuple[tuple[int, int], bytes]:
            mask = font.getmask(text)
            return mask.size, bytes(mask)

        missing = signature("\U0010ffff")
        for character in "中文汉字字幕测试":
            glyph = signature(character)
            if glyph == missing or not any(glyph[1]):
                return False
        return True
    except (OSError, ValueError):
        return False


def _usable_cjk_font(path: Path) -> bool:
    try:
        info = path.stat()
        return path.is_file() and _has_cjk_glyphs(str(path), info.st_mtime_ns, info.st_size)
    except OSError:
        return False


def candidate_fonts(system: str | None = None) -> list[Path]:
    """Return ordered, platform-aware CJK font candidates.

    Open-source Noto/Source Han families are preferred. System proprietary
    fonts are compatibility fallbacks only and are never redistributed.
    """

    current = (system or platform.system()).lower()
    shared = [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    if current == "windows":
        windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        return [
            windows / "NotoSansCJKsc-Regular.otf",
            windows / "SourceHanSansSC-Regular.otf",
            windows / "msyh.ttc",
            windows / "simhei.ttf",
        ] + shared
    if current == "darwin":
        return [
            Path("/Library/Fonts/NotoSansCJKsc-Regular.otf"),
            Path("/Library/Fonts/SourceHanSansSC-Regular.otf"),
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        ] + shared
    return shared


def _fontconfig_match() -> Path | None:
    executable = shutil.which("fc-match")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "-s", "-f", "%{file}\n", "Noto Sans CJK SC,Source Han Sans SC,WenQuanYi Zen Hei"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for raw in result.stdout.splitlines():
        path = Path(raw.strip())
        if _usable_cjk_font(path):
            return path
    return None


def find_cjk_font(explicit: str | Path | None = None) -> Path:
    """Resolve a usable CJK font or raise with a portable remediation hint."""

    requested = explicit or os.environ.get(FONT_ENV)
    if requested:
        path = Path(requested).expanduser()
        if not path.is_file():
            raise RuntimeError(f"指定字体不存在：{path}")
        if not _usable_cjk_font(path):
            raise RuntimeError(f"指定字体不可读取或缺少基本中文字形：{path}")
        return path
    for path in candidate_fonts():
        if _usable_cjk_font(path):
            return path
    matched = _fontconfig_match()
    if matched:
        return matched
    raise RuntimeError(
        "未找到可用的中文字体。请安装 Noto Sans CJK SC 或 Source Han Sans SC，"
        f"也可通过环境变量 {FONT_ENV} 指向本机字体文件。"
    )


def load_cjk_font(size: int, explicit: str | Path | None = None) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(find_cjk_font(explicit)), size)


def cjk_font_identity(explicit: str | Path | None = None) -> tuple[Path, str]:
    """Return the resolved font file and the family name reported by FreeType."""
    path = find_cjk_font(explicit)
    family, _style = ImageFont.truetype(str(path), 24).getname()
    return path, str(family)
