#!/usr/bin/env python3
"""Turn an authored script file into the exact plain text narration body.

The public workflow accepts Markdown for authoring convenience, but structural
Markdown must never reach a speech engine or caption aligner.  This module is
intentionally dependency-free so every entry point can share the same policy.
"""

from __future__ import annotations

import re
from pathlib import Path


_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
_RULE_RE = re.compile(r"^\s{0,3}(?:[-*_]\s*){3,}$")
_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\([^)]*\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def plain_narration_text(source: str) -> str:
    """Return speakable text while excluding Markdown-only structure.

    Headings, fenced code, horizontal rules, images and HTML comments are
    metadata, not narration.  List and blockquote markers are removed while
    retaining their visible prose.  Blank lines remain paragraph boundaries.
    """

    text = source.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    text = _HTML_COMMENT_RE.sub("", text)
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() in {"---", "..."}:
                lines = lines[index + 1 :]
                break

    output: list[str] = []
    in_fence = False
    for raw_line in lines:
        if _FENCE_RE.match(raw_line):
            in_fence = not in_fence
            continue
        if in_fence or _HEADING_RE.match(raw_line) or _RULE_RE.match(raw_line):
            continue
        line = _BLOCKQUOTE_RE.sub("", raw_line)
        line = _LIST_RE.sub("", line)
        line = _IMAGE_RE.sub("", line)
        line = _LINK_RE.sub(r"\1", line)
        line = re.sub(r"(?<!\\)[*_]{1,3}", "", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            output.append(line)
        elif output and output[-1] != "":
            output.append("")

    while output and output[-1] == "":
        output.pop()
    return "\n".join(output).strip()


def read_narration_text(path: Path) -> str:
    return plain_narration_text(path.read_text(encoding="utf-8"))
