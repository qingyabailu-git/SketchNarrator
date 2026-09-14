#!/usr/bin/env python3
"""Create real word timestamps and readable Chinese SRT from one narration file."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from audio_probe import decoded_audio_duration_ms


PUNCTUATION = "，。！？；：、,.!?;:《》“”‘’（）()【】[]—…"
MAJOR_BOUNDARIES = "。！？!?；;\n"
MINOR_BOUNDARIES = "，,、：:"
SEMANTIC_BOUNDARIES = MAJOR_BOUNDARIES + MINOR_BOUNDARIES
DEFAULT_MAX_CAPTION_CHARS = 18
DEFAULT_MAX_CAPTION_MS = 3600
DEFAULT_MAX_LINE_CHARS = 14

BAD_LINE_ENDINGS = (
    "的", "地", "得", "和", "与", "或", "及", "但", "却", "而", "把", "被", "给", "在", "从", "向", "对", "为",
)
BAD_LINE_STARTS = (
    "，", "。", "！", "？", "；", "：", "、", ",", ".", "!", "?", ";", ":", "的", "地", "得",
)
BREAK_BEFORE_WORDS = (
    "但是", "不过", "所以", "因此", "同时", "而且", "然后", "另外", "其实", "也可能", "或者", "再加上", "想停",
)


def srt_time(ms: int) -> str:
    hours, rem = divmod(max(0, ms), 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def normalized(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


def clean_caption(text: str) -> str:
    return text.translate(str.maketrans("", "", PUNCTUATION)).strip()


def timing_characters(words: list[dict]) -> list[dict]:
    """Expand recognized chunks into character-level timing anchors."""

    anchors: list[dict] = []
    for word in words:
        characters = [char for char in word["text"] if normalized(char)]
        if not characters:
            continue
        start_ms = int(word["start_ms"])
        end_ms = max(start_ms + len(characters), int(word["end_ms"]))
        span = end_ms - start_ms
        for index, char in enumerate(characters):
            char_start = round(start_ms + span * index / len(characters))
            char_end = round(start_ms + span * (index + 1) / len(characters))
            anchors.append({
                "key": normalized(char),
                "start_ms": char_start,
                "end_ms": max(char_start + 1, char_end),
            })
    return anchors


def align_script_words(script_text: str, recognized_words: list[dict]) -> list[dict]:
    """Project locked script characters onto ASR-derived timing anchors."""

    expected = [char for char in script_text if normalized(char)]
    anchors = timing_characters(recognized_words)
    if not expected or not anchors:
        return []

    mapped: list[dict | None] = [None] * len(expected)
    matcher = difflib.SequenceMatcher(
        None,
        [normalized(char) for char in expected],
        [item["key"] for item in anchors],
        autojunk=False,
    )
    for tag, expected_start, expected_end, actual_start, actual_end in matcher.get_opcodes():
        count = expected_end - expected_start
        if tag == "equal":
            for offset in range(count):
                anchor = anchors[actual_start + offset]
                mapped[expected_start + offset] = {
                    "text": expected[expected_start + offset],
                    "start_ms": anchor["start_ms"],
                    "end_ms": anchor["end_ms"],
                }
        elif tag == "replace" and count and actual_end > actual_start:
            start_ms = anchors[actual_start]["start_ms"]
            end_ms = anchors[actual_end - 1]["end_ms"]
            span = max(count, end_ms - start_ms)
            for offset in range(count):
                char_start = round(start_ms + span * offset / count)
                char_end = round(start_ms + span * (offset + 1) / count)
                mapped[expected_start + offset] = {
                    "text": expected[expected_start + offset],
                    "start_ms": char_start,
                    "end_ms": max(char_start + 1, char_end),
                }

    index = 0
    audio_end_ms = anchors[-1]["end_ms"]
    while index < len(mapped):
        if mapped[index] is not None:
            index += 1
            continue
        run_start = index
        while index < len(mapped) and mapped[index] is None:
            index += 1
        run_end = index
        count = run_end - run_start
        left_end = mapped[run_start - 1]["end_ms"] if run_start else anchors[0]["start_ms"]
        right_start = mapped[run_end]["start_ms"] if run_end < len(mapped) else audio_end_ms
        span = max(count, right_start - left_end)
        for offset in range(count):
            char_start = round(left_end + span * offset / count)
            char_end = round(left_end + span * (offset + 1) / count)
            mapped[run_start + offset] = {
                "text": expected[run_start + offset],
                "start_ms": char_start,
                "end_ms": max(char_start + 1, char_end),
            }

    aligned: list[dict] = []
    cursor = 0
    for item in mapped:
        assert item is not None
        start_ms = max(cursor, item["start_ms"])
        end_ms = max(start_ms + 1, item["end_ms"])
        aligned.append({"text": item["text"], "start_ms": start_ms, "end_ms": end_ms})
        cursor = end_ms
    return aligned


def _timing_cues(words: list[dict], max_chars: int, max_ms: int) -> list[dict]:
    """Fallback for providers that do not have the locked narration text."""

    cues: list[dict] = []
    current: list[dict] = []
    for word in words:
        if current:
            chars = len(clean_caption("".join(x["text"] for x in current + [word])))
            duration = word["end_ms"] - current[0]["start_ms"]
            pause = word["start_ms"] - current[-1]["end_ms"]
            if chars > max_chars or duration > max_ms or pause > 420:
                text = clean_caption("".join(x["text"] for x in current))
                if text:
                    cues.append({"start_ms": current[0]["start_ms"], "end_ms": current[-1]["end_ms"], "text": text})
                current = []
        current.append(word)
    if current:
        text = clean_caption("".join(x["text"] for x in current))
        if text:
            cues.append({"start_ms": current[0]["start_ms"], "end_ms": current[-1]["end_ms"], "text": text})
    return cues


def visible_length(text: str) -> int:
    return len(normalized(text))


def _script_atoms(script_text: str) -> list[dict]:
    """Split the locked script only at authored punctuation and retain it."""

    atoms: list[dict] = []
    raw_start = 0
    content_start = 0
    content_cursor = 0
    for index, char in enumerate(script_text):
        if normalized(char):
            content_cursor += 1
        if char not in SEMANTIC_BOUNDARIES:
            continue
        text = script_text[raw_start:index + 1].strip()
        if visible_length(text):
            atoms.append({
                "text": text,
                "start_index": content_start,
                "end_index": content_cursor,
                "major": char in MAJOR_BOUNDARIES,
            })
        raw_start = index + 1
        content_start = content_cursor
    tail = script_text[raw_start:].strip()
    if visible_length(tail):
        atoms.append({
            "text": tail,
            "start_index": content_start,
            "end_index": content_cursor,
            "major": True,
        })
    if atoms and not atoms[-1]["major"]:
        atoms[-1]["major"] = True
    return atoms


def _slice_visible(text: str, start: int, end: int) -> str:
    """Slice by speakable-character indexes while keeping adjacent punctuation."""

    total = visible_length(text)
    seen = 0
    raw_start = 0
    raw_end = len(text)
    for index, char in enumerate(text):
        if normalized(char):
            if seen == start:
                raw_start = index
            seen += 1
            if seen == end:
                raw_end = index + 1
                break
    if start == 0:
        raw_start = 0
    if end >= total:
        raw_end = len(text)
    return text[raw_start:raw_end].strip()


def _split_long_atom(atom: dict, aligned: list[dict], max_chars: int, max_ms: int) -> list[dict]:
    """Split an unusually long unpunctuated clause at the best real pause."""

    pieces: list[dict] = []
    start_index = int(atom["start_index"])
    atom_end = int(atom["end_index"])
    local_start = 0
    while start_index < atom_end:
        furthest = min(atom_end, start_index + max_chars)
        while furthest > start_index + 1 and aligned[furthest - 1]["end_ms"] - aligned[start_index]["start_ms"] > max_ms:
            furthest -= 1
        if furthest >= atom_end:
            end_index = atom_end
        else:
            minimum = min(furthest, start_index + max(4, max_chars // 3))
            candidates: list[tuple[float, int]] = []
            plain = normalized(atom["text"])
            for boundary in range(minimum, furthest + 1):
                gap = aligned[boundary]["start_ms"] - aligned[boundary - 1]["end_ms"] if boundary < atom_end else 0
                local_boundary = boundary - int(atom["start_index"])
                connector_bonus = 0
                if any(plain[local_boundary:].startswith(word) for word in BREAK_BEFORE_WORDS):
                    connector_bonus = 900
                distance_penalty = abs(furthest - boundary) * 12
                candidates.append((gap * 3 + connector_bonus - distance_penalty, boundary))
            end_index = max(start_index + 1, max(candidates)[1] if candidates else furthest)
        local_end = local_start + end_index - start_index
        piece_text = _slice_visible(atom["text"], local_start, local_end)
        pieces.append({
            "text": piece_text,
            "start_index": start_index,
            "end_index": end_index,
            "major": bool(atom["major"] and end_index == atom_end),
        })
        local_start = local_end
        start_index = end_index
    return pieces


def _group_fits(atoms: list[dict], aligned: list[dict], max_chars: int, max_ms: int) -> bool:
    if not atoms:
        return True
    chars = sum(int(atom["end_index"]) - int(atom["start_index"]) for atom in atoms)
    duration = aligned[int(atoms[-1]["end_index"]) - 1]["end_ms"] - aligned[int(atoms[0]["start_index"])]["start_ms"]
    return chars <= max_chars and duration <= max_ms


def _pack_sentence(atoms: list[dict], aligned: list[dict], max_chars: int, max_ms: int) -> list[list[dict]]:
    """Prefer authored clause changes instead of filling one long caption."""

    if len(atoms) <= 1:
        return [atoms] if atoms else []
    groups: list[list[dict]] = []
    current: list[dict] = []
    for atom in atoms:
        if current:
            current_chars = sum(visible_length(item["text"]) for item in current)
            atom_chars = visible_length(atom["text"])
            should_keep_together = current_chars < 5 or atom_chars < 4
            if str(current[-1]["text"]).rstrip().endswith(("：", ":")):
                should_keep_together = False
            if not should_keep_together or not _group_fits(current + [atom], aligned, max_chars, max_ms):
                groups.append(current)
                current = []
        current.append(atom)
    if current:
        groups.append(current)
    return [group for group in groups if group]


def wrap_caption(text: str, max_line_chars: int = DEFAULT_MAX_LINE_CHARS) -> str:
    """Keep one punctuation-free line while preserving deliberate word spaces."""

    del max_line_chars
    return re.sub(r"[ \t]+", " ", clean_caption(text)).strip()


def build_semantic_cues(
    script_text: str,
    words: list[dict],
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_ms: int = DEFAULT_MAX_CAPTION_MS,
    max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
) -> list[dict]:
    """Build cues from authored Chinese clauses, then project them onto real audio time."""

    aligned = align_script_words(script_text, words)
    atoms = _script_atoms(script_text)
    if not aligned or not atoms or atoms[-1]["end_index"] > len(aligned):
        return _timing_cues(words, max_chars, max_ms)

    split_atoms: list[dict] = []
    for atom in atoms:
        split_atoms.extend(_split_long_atom(atom, aligned, max_chars, max_ms))

    sentences: list[list[dict]] = []
    current: list[dict] = []
    for atom in split_atoms:
        current.append(atom)
        if atom["major"]:
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)

    cues: list[dict] = []
    for sentence in sentences:
        for group in _pack_sentence(sentence, aligned, max_chars, max_ms):
            start_index = int(group[0]["start_index"])
            end_index = int(group[-1]["end_index"])
            text = clean_caption("".join(atom["text"] for atom in group).strip())
            cues.append({
                "start_ms": aligned[start_index]["start_ms"],
                "end_ms": aligned[end_index - 1]["end_ms"],
                "text": wrap_caption(text, max_line_chars),
            })
    return cues


def build_cues(
    words: list[dict],
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_ms: int = DEFAULT_MAX_CAPTION_MS,
    script_text: str | None = None,
    max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
) -> list[dict]:
    if script_text and normalized(script_text):
        return build_semantic_cues(script_text, words, max_chars, max_ms, max_line_chars)
    return _timing_cues(words, max_chars, max_ms)


def write_srt(cues: list[dict], target: Path) -> None:
    lines: list[str] = []
    for index, cue in enumerate(cues, 1):
        lines.extend([
            str(index),
            f"{srt_time(cue['start_ms'])} --> {srt_time(cue['end_ms'])}",
            cue["text"],
            "",
        ])
    target.write_text("\n".join(lines), encoding="utf-8")


def transcribe(args: argparse.Namespace) -> int:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[err] 缺少 faster-whisper。先在隔离环境中安装 faster-whisper。", file=sys.stderr)
        return 2

    audio = Path(args.audio).resolve()
    if not audio.is_file():
        print(f"[err] 配音文件不存在：{audio}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    segments, info = model.transcribe(
        str(audio), language=args.language, word_timestamps=True, vad_filter=True, beam_size=5
    )
    recognized_words: list[dict] = []
    transcript_parts: list[str] = []
    for segment in segments:
        transcript_parts.append(segment.text)
        for word in segment.words or []:
            if word.start is None or word.end is None:
                continue
            text = word.word.strip()
            if not text:
                continue
            recognized_words.append({
                "text": text,
                "start_ms": round(word.start * 1000),
                "end_ms": round(word.end * 1000),
            })
    if not recognized_words:
        print("[err] 没有得到逐词时间。", file=sys.stderr)
        return 1

    script_match = None
    script_text = None
    words = recognized_words
    source = "faster-whisper"
    if args.script:
        expected = Path(args.script).read_text(encoding="utf-8")
        script_text = expected
        actual = "".join(transcript_parts)
        script_match = difflib.SequenceMatcher(None, normalized(expected), normalized(actual)).ratio()
        aligned_words = align_script_words(expected, recognized_words)
        if aligned_words:
            words = aligned_words
            source = "faster-whisper-script-aligned"

    duration_ms = max(decoded_audio_duration_ms(audio) or 0, max(word["end_ms"] for word in words))
    payload = {
        "version": 1,
        "source": source,
        "model": args.model,
        "language": info.language,
        "duration_ms": duration_ms,
        "words": words,
    }
    if script_match is not None:
        payload["script_match"] = round(script_match, 3)
    words_path = out_dir / "words.json"
    words_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    captions_path = out_dir / "captions.srt"
    write_srt(
        build_cues(words, args.max_chars, args.max_caption_ms, script_text, args.max_line_chars),
        captions_path,
    )

    if script_match is not None:
        print(f"SCRIPT_MATCH={script_match:.3f}")
        if script_match < args.min_script_match:
            print("[warn] 识别文本与原稿差异较大；第一次确认前必须人工核对。")

    print(f"WORDS={words_path}")
    print(f"CAPTIONS={captions_path}")
    print(f"DURATION_MS={duration_ms}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="为中文配音生成逐词时间和 SRT")
    p.add_argument("audio")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--script")
    p.add_argument("--model", default="small")
    p.add_argument("--language", default="zh")
    p.add_argument("--device", default="cpu")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CAPTION_CHARS)
    p.add_argument("--max-caption-ms", type=int, default=DEFAULT_MAX_CAPTION_MS)
    p.add_argument("--max-line-chars", type=int, default=DEFAULT_MAX_LINE_CHARS)
    p.add_argument("--min-script-match", type=float, default=0.78)
    return p


if __name__ == "__main__":
    raise SystemExit(transcribe(parser().parse_args()))
