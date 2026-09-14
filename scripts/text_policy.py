#!/usr/bin/env python3
"""Shared text, caption, and pronunciation policy for SketchNarrator."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


SRT_TIME_RE = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})")
CAPTION_BAD_ENDINGS = ("的", "地", "得", "和", "与", "或", "及", "但", "却", "而", "把", "被", "给", "在", "从", "向")
CAPTION_BAD_STARTS = ("的", "地", "得", "和", "与", "或", "及", "但", "却", "而")


class TextPolicyError(ValueError):
    pass


def normalized_text(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).casefold()


def contains_punctuation(text: str) -> bool:
    return any(unicodedata.category(char).startswith("P") for char in text)


def strip_caption_punctuation(text: str) -> str:
    """Remove punctuation while preserving readable characters and word spaces."""

    without_punctuation = "".join(
        char for char in text if not unicodedata.category(char).startswith("P")
    )
    return re.sub(r"[ \t]+", " ", without_punctuation).strip()


def srt_time_ms(value: str) -> int:
    match = SRT_TIME_RE.fullmatch(value.strip())
    if not match:
        raise TextPolicyError(f"无效 SRT 时间：{value}")
    parts = {key: int(raw) for key, raw in match.groupdict().items()}
    return ((parts["h"] * 60 + parts["m"]) * 60 + parts["s"]) * 1000 + parts["ms"]


def parse_srt_cues(srt_text: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    previous_end = -1
    normalized = srt_text.replace("\r\n", "\n").strip()
    for block in re.split(r"\n\s*\n", normalized):
        raw_lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(raw_lines) if " --> " in line), None)
        if timing_index is None:
            continue
        start_raw, end_raw = raw_lines[timing_index].split(" --> ", 1)
        start_ms, end_ms = srt_time_ms(start_raw), srt_time_ms(end_raw)
        text_lines = raw_lines[timing_index + 1 :]
        if not text_lines:
            raise TextPolicyError("SRT 包含空字幕")
        if start_ms < 0 or end_ms <= start_ms:
            raise TextPolicyError("SRT 包含无效时间范围")
        if start_ms < previous_end:
            raise TextPolicyError("SRT 字幕时间发生重叠或倒序")
        previous_end = end_ms
        cues.append(
            {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": "\n".join(text_lines),
                "line_count": len(text_lines),
            }
        )
    if not cues:
        raise TextPolicyError("SRT 中没有有效字幕")
    return cues


def source_punctuation_boundaries(script_text: str) -> set[int]:
    """Return speakable-character offsets that are separated by source punctuation."""

    boundaries: set[int] = set()
    offset = 0
    for char in script_text:
        if normalized_text(char):
            offset += 1
        elif unicodedata.category(char).startswith("P") and offset:
            boundaries.add(offset)
    return boundaries


def invalid_caption_spaces(cues: list[dict[str, Any]], script_text: str) -> list[str]:
    """Reject invented Chinese phrase breaks while allowing real sentence boundaries."""

    allowed_boundaries = source_punctuation_boundaries(script_text)
    errors: list[str] = []
    global_offset = 0
    for index, cue in enumerate(cues, 1):
        text = str(cue["text"])
        local_offset = 0
        for char_index, char in enumerate(text):
            if normalized_text(char):
                local_offset += 1
                continue
            if not char.isspace():
                continue
            left = text[char_index - 1] if char_index else ""
            right = text[char_index + 1] if char_index + 1 < len(text) else ""
            english_word_space = left.isascii() and right.isascii() and left.isalnum() and right.isalnum()
            boundary = global_offset + local_offset
            if not english_word_space and boundary not in allowed_boundaries:
                errors.append(
                    f"第 {index} 条字幕的空格不对应原稿标点边界，疑似把连续词语硬拆开"
                )
        global_offset += len(normalized_text(text))
    return errors


def caption_semantic_warnings(cues: list[dict[str, Any]], script_text: str = "") -> list[str]:
    """Return editorial warnings without blocking an otherwise valid subtitle file."""

    warnings: list[str] = []
    for index, cue in enumerate(cues, 1):
        compact = str(cue["text"]).replace("\n", "").strip()
        if compact.endswith(CAPTION_BAD_ENDINGS):
            warnings.append(f"第 {index} 条字幕可能在虚词“{compact[-1]}”后截断")
        if compact.startswith(CAPTION_BAD_STARTS):
            warnings.append(f"第 {index} 条字幕可能从虚词“{compact[0]}”开始")
        if len(normalized_text(compact)) > 18:
            warnings.append(f"第 {index} 条字幕超过 18 个可读字符，需人工确认阅读速度")
    if script_text:
        warnings.extend(invalid_caption_spaces(cues, script_text))
    return warnings


def validate_caption_contract(script_text: str, srt_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Enforce final caption invariants and return non-blocking semantic warnings."""

    cues = parse_srt_cues(srt_text)
    if normalized_text("".join(str(cue["text"]) for cue in cues)) != normalized_text(script_text):
        raise TextPolicyError("字幕正文与锁定口播稿不一致")
    for index, cue in enumerate(cues, 1):
        if int(cue["line_count"]) != 1:
            raise TextPolicyError(f"第 {index} 条字幕必须只有一行")
        if contains_punctuation(str(cue["text"])):
            raise TextPolicyError(f"第 {index} 条字幕含有标点，最终字幕必须无标点")
    invalid_spaces = invalid_caption_spaces(cues, script_text)
    if invalid_spaces:
        raise TextPolicyError("；".join(invalid_spaces))
    return cues, caption_semantic_warnings(cues, script_text)


def empty_pronunciation_overrides() -> dict[str, Any]:
    return {"version": 1, "overrides": []}


def validate_pronunciation_overrides(data: dict[str, Any], display_text: str) -> list[dict[str, Any]]:
    if data.get("version") != 1:
        raise TextPolicyError("pronunciation-overrides.json version 必须为 1")
    overrides = data.get("overrides")
    if not isinstance(overrides, list):
        raise TextPolicyError("pronunciation-overrides.json 必须包含 overrides 数组")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, item in enumerate(overrides, 1):
        if not isinstance(item, dict):
            raise TextPolicyError(f"第 {index} 条发音覆盖必须是对象")
        written = str(item.get("written", "")).strip()
        spoken = str(item.get("spoken", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not written or not spoken or written == spoken:
            raise TextPolicyError(f"第 {index} 条发音覆盖的 written/spoken 无效")
        if written in seen:
            raise TextPolicyError(f"发音覆盖 written 重复：{written}")
        if written not in display_text:
            raise TextPolicyError(f"发音覆盖未命中展示文案：{written}")
        if not reason:
            raise TextPolicyError(f"第 {index} 条发音覆盖缺少 reason")
        if item.get("approved_by_user") is not True:
            raise TextPolicyError(f"第 {index} 条发音覆盖尚未获得用户确认")
        seen.add(written)
        validated.append(
            {
                "written": written,
                "spoken": spoken,
                "reason": reason,
                "approved_by_user": True,
            }
        )
    return validated


def apply_pronunciation_overrides(display_text: str, data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    overrides = validate_pronunciation_overrides(data, display_text)
    tts_text = display_text
    applied: list[dict[str, Any]] = []
    for item in overrides:
        count = tts_text.count(item["written"])
        tts_text = tts_text.replace(item["written"], item["spoken"])
        applied.append({**item, "replacement_count": count})
    return tts_text, applied
