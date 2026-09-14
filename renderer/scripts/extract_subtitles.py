#!/usr/bin/env python3
"""Extract source audio and source-language captions without calling an LLM.

The output is an intake package for the whiteboard workflow. Native Bilibili
captions are preferred. Other links are downloaded with yt-dlp and local files
are normalised with FFmpeg; when native captions are unavailable the isolated
faster-whisper runtime performs ASR.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from ffmpeg_runtime import runtime_info
from source_privacy import require_public_source, source_label

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}
AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
LANGUAGE_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "zh-tw": "zh",
    "en-us": "en",
    "en-gb": "en",
}
MODEL_REPOSITORIES = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}
MODEL_REUSE_PRIORITY = ("small", "base", "tiny", "medium", "large-v3-turbo", "large-v3")


class ExtractionError(RuntimeError):
    pass


def normalise_language(value: str | None) -> str:
    language = str(value or "").strip().casefold().replace("_", "-")
    if not language or language == "auto":
        return "auto"
    language = LANGUAGE_ALIASES.get(language, language)
    return language.split("-", 1)[0]


def _language_matches(candidate: str, requested: str) -> bool:
    return normalise_language(candidate) == normalise_language(requested)


def cached_asr_models() -> list[str]:
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return []
    try:
        repository_ids = {
            str(repository.repo_id).casefold()
            for repository in scan_cache_dir().repos
            if repository.revisions
        }
    except Exception:
        return []
    return [
        model
        for model in MODEL_REUSE_PRIORITY
        if MODEL_REPOSITORIES[model].casefold() in repository_ids
    ]


def resolve_asr_model(requested_model: str = "auto") -> tuple[str, str, list[str]]:
    requested = str(requested_model or "auto").strip().casefold()
    if requested != "auto" and requested not in MODEL_REPOSITORIES:
        raise ExtractionError(f"不支持的 ASR 模型：{requested_model}")
    cached = cached_asr_models()
    if requested != "auto":
        source = "explicit-cached" if requested in cached else "explicit-download"
        return requested, source, cached
    if cached:
        return cached[0], "cache", cached
    return "small", "default-download", cached


def progress(stage: str, detail: str = "") -> None:
    message = f"EXTRACT_STAGE={stage}"
    if detail:
        message += f" DETAIL={detail}"
    print(message, flush=True)


def _request_json(url: str, referer: str = "") -> dict[str, Any]:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ExtractionError(f"接口未返回 JSON 对象：{url}")
    return value


def _download(url: str, target: Path, referer: str = "") -> Path:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _parse_json3_cues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        if not isinstance(event, dict) or not isinstance(event.get("segs"), list):
            continue
        text = "".join(str(segment.get("utf8", "")) for segment in event["segs"] if isinstance(segment, dict))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if not text:
            continue
        start = int(event.get("tStartMs", 0))
        duration = max(1, int(event.get("dDurationMs", 1)))
        cues.append({"startMs": start, "endMs": start + duration, "text": text})
    return cues


def _parse_vtt_timestamp(value: str) -> int:
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"无效 VTT 时间：{value}")
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


def _parse_vtt_cues(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        start_text, end_part = line.split("-->", 1)
        end_text = end_part.strip().split()[0]
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index].strip())
            index += 1
        cue_text = " ".join(body)
        cue_text = re.sub(r"<[^>]+>", "", cue_text)
        cue_text = re.sub(r"\s+", " ", html.unescape(cue_text)).strip()
        if cue_text:
            cues.append({
                "startMs": _parse_vtt_timestamp(start_text),
                "endMs": _parse_vtt_timestamp(end_text),
                "text": cue_text,
            })
        index += 1
    return cues


def _metadata_language(info: dict[str, Any]) -> str:
    for key in ("language", "original_language"):
        value = normalise_language(info.get(key))
        if value != "auto":
            return value
    for key in ("subtitles", "automatic_captions"):
        tracks = info.get(key)
        if not isinstance(tracks, dict):
            continue
        original = [str(language) for language in tracks if str(language).casefold().endswith("-orig")]
        if len(original) == 1:
            return normalise_language(original[0].rsplit("-", 1)[0])
    subtitles = info.get("subtitles")
    if isinstance(subtitles, dict):
        languages = {normalise_language(language) for language in subtitles}
        languages.discard("auto")
        if len(languages) == 1:
            return next(iter(languages))
    return "auto"


def _select_caption_track(
    info: dict[str, Any], source_language: str = "auto"
) -> tuple[str, str, str, str] | None:
    requested = normalise_language(source_language)
    if requested == "auto":
        requested = _metadata_language(info)
    if requested == "auto":
        return None
    for source_name, key in (("native", "subtitles"), ("automatic", "automatic_captions")):
        tracks = info.get(key)
        if not isinstance(tracks, dict):
            continue
        ordered_languages = [language for language in tracks if _language_matches(str(language), requested)]
        ordered_languages.sort(key=lambda language: (not str(language).casefold().endswith("-orig"), str(language)))
        for language in ordered_languages:
            formats = tracks.get(language)
            if not isinstance(formats, list):
                continue
            for extension in ("json3", "vtt"):
                for candidate in formats:
                    if not isinstance(candidate, dict) or candidate.get("ext") != extension or not candidate.get("url"):
                        continue
                    return str(language), str(candidate["url"]), extension, source_name
    return None


def _select_chinese_track(info: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Compatibility wrapper for callers that explicitly need Chinese captions."""
    return _select_caption_track(info, "zh")


def resolve_generic_captions(
    url: str, out_dir: Path, source_language: str = "auto"
) -> tuple[list[dict[str, Any]], str, str] | None:
    """Return captions in the source language, or None so the caller can run ASR."""

    try:
        import yt_dlp
    except ImportError:
        return None
    progress("native-subtitle-probe")
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
    except Exception as exc:
        progress("native-subtitle-unavailable", str(exc)[-300:])
        return None
    if not isinstance(info, dict):
        progress("native-subtitle-unavailable", "metadata missing")
        return None
    if isinstance(info.get("entries"), list):
        info = next((entry for entry in info["entries"] if isinstance(entry, dict)), info)
    selected = _select_caption_track(info, source_language)
    if selected is None:
        requested = normalise_language(source_language)
        detail = "source language unknown" if requested == "auto" else f"no {requested} track"
        progress("native-subtitle-unavailable", detail)
        return None
    language, subtitle_url, extension, source_name = selected
    target = out_dir / f"native-subtitle.{extension}"
    try:
        _download(subtitle_url, target, str(info.get("webpage_url") or url))
        if extension == "json3":
            payload = json.loads(target.read_text(encoding="utf-8"))
            cues = _parse_json3_cues(payload)
        else:
            cues = _parse_vtt_cues(target.read_text(encoding="utf-8-sig"))
        cues = normalise_cues(cues)
    except (OSError, ValueError, json.JSONDecodeError, ExtractionError) as exc:
        progress("native-subtitle-unavailable", str(exc)[-300:])
        return None
    progress("native-subtitle-ready", f"{source_name}/{language} cues={len(cues)}")
    return cues, f"generic-{source_name}/{language}", normalise_language(language)


def to_simplified(text: str) -> str:
    try:
        import opencc

        return opencc.OpenCC("t2s").convert(text)
    except (ImportError, OSError):
        return text


def timestamp(value_ms: int, vtt: bool = False) -> str:
    value_ms = max(0, int(value_ms))
    hours, remainder = divmod(value_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    separator = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def normalise_cues(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous_end = 0
    for cue in cues:
        text = to_simplified(str(cue.get("text", "")).strip())
        if not text:
            continue
        start = max(previous_end, int(cue.get("startMs", 0)))
        end = max(start + 1, int(cue.get("endMs", start + 1)))
        result.append({
            "index": len(result) + 1,
            "startMs": start,
            "endMs": end,
            "durMs": end - start,
            "text": text,
        })
        previous_end = end
    if not result:
        raise ExtractionError("没有提取到有效字幕")
    return result


def transcript_quality(
    cues: list[dict[str, Any]],
    duration_ms: int,
    language: str,
    language_probability: float | None = None,
) -> dict[str, Any]:
    text = "\n".join(str(cue.get("text", "")) for cue in cues)
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    english_words = len(re.findall(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)*\b", text))
    language = normalise_language(language)
    duration_minutes = max(duration_ms / 60_000, 1 / 60)
    units = cjk_count if language == "zh" else english_words
    errors: list[str] = []
    warnings: list[str] = []

    if language == "auto":
        errors.append("无法确定源语言")
    if language_probability is not None and language_probability < 0.50:
        errors.append(f"语言判断置信度过低：{language_probability:.2f}")
    if language == "en" and cjk_count > max(4, round(latin_count * 0.15)):
        errors.append("英文源中出现了异常比例的中文内容")
    if language == "zh" and cjk_count == 0 and latin_count >= 20:
        errors.append("中文源中没有可识别的中文内容")

    duration_third = max(duration_ms / 3, 1)
    for third_index in range(3):
        third_text = " ".join(
            str(cue.get("text", ""))
            for cue in cues
            if third_index * duration_third <= (int(cue.get("startMs", 0)) + int(cue.get("endMs", 0))) / 2
            < (third_index + 1) * duration_third
        )
        third_cjk = len(re.findall(r"[\u3400-\u9fff]", third_text))
        third_latin = len(re.findall(r"[A-Za-z]", third_text))
        if language == "en" and third_cjk > max(4, round(third_latin * 0.35)):
            errors.append(f"第 {third_index + 1} 段语言与英文源不一致")
        if language == "zh" and third_latin >= 20 and third_cjk == 0:
            errors.append(f"第 {third_index + 1} 段语言与中文源不一致")

    if duration_ms >= 30_000:
        minimum_cues = max(2, round(duration_ms / 45_000))
        if len(cues) < minimum_cues:
            errors.append(f"字幕数量异常：{duration_ms / 1000:.1f} 秒音频只有 {len(cues)} 条字幕")
        units_per_minute = units / duration_minutes
        if units_per_minute < 8:
            errors.append(f"转写内容过少：每分钟约 {units_per_minute:.1f} 个有效语言单位")
        span_ratio = max(0, cues[-1]["endMs"] - cues[0]["startMs"]) / duration_ms
        if span_ratio < 0.35:
            errors.append(f"字幕时间覆盖异常：仅覆盖音频跨度的 {span_ratio:.0%}")
        elif span_ratio < 0.60:
            warnings.append(f"字幕时间覆盖偏低：覆盖音频跨度的 {span_ratio:.0%}")

    cue_keys = [re.sub(r"\W+", "", str(cue.get("text", "")).casefold()) for cue in cues]
    cue_keys = [value for value in cue_keys if value]
    repeated = len(cue_keys) - len(set(cue_keys))
    if len(cue_keys) >= 6 and repeated / len(cue_keys) > 0.35:
        errors.append("字幕存在异常的大量重复")

    compact = re.sub(r"\s+", "", text).casefold()
    promotion_markers = ("点赞", "订阅", "转发", "打赏", "likeandsubscribe")
    if units < 40 and any(marker in compact for marker in promotion_markers):
        errors.append("短转写中出现疑似推广话术幻觉")

    return {
        "status": "failed" if errors else "passed",
        "language": language,
        "language_probability": round(language_probability, 3) if language_probability is not None else None,
        "metrics": {
            "cue_count": len(cues),
            "cjk_count": cjk_count,
            "latin_count": latin_count,
            "english_word_count": english_words,
            "transcript_span_ratio": round(max(0, cues[-1]["endMs"] - cues[0]["startMs"]) / max(duration_ms, 1), 3),
        },
        "errors": errors,
        "warnings": warnings,
    }


def save_outputs(
    cues: list[dict[str, Any]],
    words: list[dict[str, Any]],
    out_dir: Path,
    audio_path: Path,
    duration_ms: int,
    source: str,
    transcript_source: str,
    transcript_language: str,
    quality: dict[str, Any],
) -> dict[str, Any]:
    cues = normalise_cues(cues)
    duration_ms = max(duration_ms, cues[-1]["endMs"])
    srt_blocks: list[str] = []
    vtt_blocks = ["WEBVTT", ""]
    for cue in cues:
        srt_blocks.append(
            f"{cue['index']}\n{timestamp(cue['startMs'])} --> {timestamp(cue['endMs'])}\n{cue['text']}\n"
        )
        vtt_blocks.append(
            f"{timestamp(cue['startMs'], True)} --> {timestamp(cue['endMs'], True)}\n{cue['text']}\n"
        )
    outputs = {
        "srt": out_dir / "voiceover.srt",
        "vtt": out_dir / "voiceover.vtt",
        "text": out_dir / "script.txt",
        "cues": out_dir / "cues.json",
    }
    outputs["srt"].write_text("\n".join(srt_blocks), encoding="utf-8")
    outputs["vtt"].write_text("\n".join(vtt_blocks), encoding="utf-8")
    outputs["text"].write_text("\n".join(cue["text"] for cue in cues) + "\n", encoding="utf-8")
    outputs["cues"].write_text(
        json.dumps({"version": 1, "language": transcript_language, "duration_ms": duration_ms, "cues": cues}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    words_path: Path | None = None
    if words:
        words_path = out_dir / "words.json"
        words_path.write_text(
            json.dumps({"version": 1, "duration_ms": duration_ms, "words": words}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    manifest = {
        "version": 1,
        "source": source_label(source),
        "transcript_source": transcript_source,
        "transcript_language": transcript_language,
        "quality": quality,
        "audio_path": str(audio_path.resolve()),
        "duration_ms": duration_ms,
        "cue_count": len(cues),
        "word_count": len(words),
        "outputs": {name: str(path.resolve()) for name, path in outputs.items()},
        "words_path": str(words_path.resolve()) if words_path else None,
        "next_step": "审核并改写 script.txt；确认最终口播后再生成正式配音与逐词时间。",
    }
    manifest_path = out_dir / "extraction.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path.resolve())
    return manifest


def _bilibili_id(url: str) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        resolved = response.geturl()
    match = re.search(r"(BV[a-zA-Z0-9]+)", resolved)
    if not match:
        raise ExtractionError(f"无法从链接中识别 BVID：{resolved}")
    return match.group(1)


def resolve_bilibili(url: str, out_dir: Path) -> tuple[list[dict[str, Any]] | None, Path, int]:
    bvid = _bilibili_id(url)
    view = _request_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    if view.get("code") != 0:
        raise ExtractionError(f"B 站视频信息获取失败：{view.get('message', '未知错误')}")
    data = view.get("data", {})
    pages = data.get("pages") or []
    cid = int(pages[0]["cid"] if pages else data["cid"])
    duration_ms = round(float(data.get("duration", 0)) * 1000)
    player = _request_json(f"https://api.bilibili.com/x/player/v2?cid={cid}&bvid={bvid}")
    subtitles = player.get("data", {}).get("subtitle", {}).get("subtitles", [])
    cues: list[dict[str, Any]] | None = None
    if subtitles:
        subtitle_url = str(subtitles[0].get("subtitle_url", ""))
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url
        body = _request_json(subtitle_url, f"https://www.bilibili.com/video/{bvid}/").get("body", [])
        cues = [
            {
                "startMs": round(float(item["from"]) * 1000),
                "endMs": round(float(item["to"]) * 1000),
                "text": str(item.get("content", "")),
            }
            for item in body
        ]
    play = _request_json(
        f"https://api.bilibili.com/x/player/playurl?cid={cid}&bvid={bvid}&fnval=16",
        f"https://www.bilibili.com/video/{bvid}/",
    )
    audio_streams = play.get("data", {}).get("dash", {}).get("audio", [])
    if not audio_streams:
        raise ExtractionError("B 站没有返回可下载的独立音频流")
    audio_url = audio_streams[0].get("baseUrl") or audio_streams[0].get("base_url")
    audio_path = _download(str(audio_url), out_dir / "voiceover.m4a", f"https://www.bilibili.com/video/{bvid}/")
    return cues, audio_path, duration_ms


def download_generic_audio(url: str, out_dir: Path) -> Path:
    if importlib.util.find_spec("yt_dlp"):
        yt_dlp_command = [sys.executable, "-m", "yt_dlp"]
    else:
        yt_dlp_executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
        if not yt_dlp_executable:
            local_bin = Path.home() / ".local" / "bin" / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
            yt_dlp_executable = str(local_bin) if local_bin.is_file() else None
        yt_dlp_command = [yt_dlp_executable] if yt_dlp_executable else []
    if not yt_dlp_command:
        raise ExtractionError(
            "该链接需要 yt-dlp。请通过 SketchNarrator 的公开 extract-source 或 setup --extraction 流程准备隔离环境。"
        )
    template = out_dir / "voiceover.%(ext)s"
    runtime = runtime_info()
    command = [
        *yt_dlp_command,
        "--no-playlist",
        "--newline",
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "-x",
        "--audio-format",
        "m4a",
    ]
    if runtime.get("path"):
        command.extend(["--ffmpeg-location", str(runtime["path"])])
    command.extend([
        "-o",
        str(template),
        url,
    ])
    progress("audio-download")
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise ExtractionError(f"yt-dlp 下载失败，退出码 {result.returncode}")
    candidates = sorted(out_dir.glob("voiceover.*"), key=lambda path: path.stat().st_mtime, reverse=True)
    candidates = [path for path in candidates if path.suffix.lower() not in {".json", ".srt", ".txt", ".vtt"}]
    if not candidates:
        raise ExtractionError("yt-dlp 没有生成音频文件")
    return candidates[0]


def normalise_local_audio(source: Path, out_dir: Path) -> Path:
    if source.suffix.lower() in AUDIO_SUFFIXES:
        target = out_dir / f"voiceover{source.suffix.lower()}"
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target
    runtime = runtime_info()
    ffmpeg = runtime.get("path")
    if not ffmpeg:
        raise ExtractionError("本地视频提取音轨需要 FFmpeg；请先完成隔离 FFmpeg 准备。")
    target = out_dir / "voiceover.m4a"
    result = subprocess.run(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn", "-c:a", "aac", "-b:a", "128k", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ExtractionError("FFmpeg 提取本地视频音轨失败：" + result.stderr.strip()[-800:])
    return target


def _python_has_module(python: Path, module: str) -> bool:
    try:
        result = subprocess.run(
            [str(python), "-c", f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec('{module}') else 1)"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def resolve_asr_python(explicit: str = "") -> tuple[Path | None, str | None]:
    candidates: list[tuple[str, Path]] = []
    if explicit:
        candidates.append(("explicit", Path(explicit).expanduser()))
    if os.environ.get("WHITEBOARD_ASR_PYTHON"):
        candidates.append(("WHITEBOARD_ASR_PYTHON", Path(os.environ["WHITEBOARD_ASR_PYTHON"]).expanduser()))
    candidates.append(("current", Path(sys.executable)))
    seen: set[str] = set()
    for source, candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and _python_has_module(candidate, "faster_whisper"):
            return candidate, source
    return None, None


def _transcribe_current(
    audio_path: Path, model_size: str, device: str, source_language: str = "auto"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, str, float | None]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ExtractionError(
            "缺少 faster-whisper。请通过 SketchNarrator 的公开 extract-source 或 setup --extraction 流程准备隔离环境。"
        ) from exc
    compute_type = "int8" if device == "cpu" else "float16"
    progress("asr-model-load", f"model={model_size} device={device}")
    model = WhisperModel(model_size, device=device, compute_type=compute_type, cpu_threads=8)
    progress("asr-transcribe", audio_path.name)
    requested_language = normalise_language(source_language)
    forced_language = None if requested_language == "auto" else requested_language
    initial_prompt = "以下是普通话简体中文内容，请保留自然标点。" if forced_language == "zh" else None
    segments, info = model.transcribe(
        str(audio_path),
        language=forced_language,
        initial_prompt=initial_prompt,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        word_timestamps=True,
        language_detection_segments=3,
        language_detection_threshold=0.50,
    )
    cues: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    detected_language = normalise_language(getattr(info, "language", None))
    transcript_language = forced_language or detected_language
    for segment in segments:
        text = segment.text.strip()
        if transcript_language == "zh":
            text = to_simplified(text)
        if text:
            cues.append({"startMs": round(segment.start * 1000), "endMs": round(segment.end * 1000), "text": text})
        for word in segment.words or []:
            word_text = str(word.word).strip()
            if transcript_language == "zh":
                word_text = to_simplified(word_text)
            if word_text:
                words.append({"text": word_text, "start_ms": round(word.start * 1000), "end_ms": round(word.end * 1000)})
    duration_ms = round(float(getattr(info, "duration", 0.0)) * 1000)
    probability_value = getattr(info, "language_probability", None)
    probability = float(probability_value) if probability_value is not None else None
    return cues, words, duration_ms, transcript_language, probability


def transcribe_audio(
    audio_path: Path,
    model_size: str,
    device: str,
    asr_python: str = "",
    source_language: str = "auto",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, str, str, float | None]:
    python, source = resolve_asr_python(asr_python)
    if python is None:
        raise ExtractionError(
            "没有找到 faster-whisper。可用 --asr-python 显式指定已有环境，"
            "或通过 SketchNarrator 的公开 extract-source 或 setup --extraction 流程准备隔离环境。"
        )
    if python == Path(sys.executable).resolve():
        cues, words, duration_ms, language, probability = _transcribe_current(
            audio_path, model_size, device, source_language
        )
        return cues, words, duration_ms, str(source), language, probability
    worker_output = audio_path.parent / ".asr-worker.json"
    worker_output.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            str(python),
            str(Path(__file__).resolve()),
            "--asr-worker",
            str(audio_path),
            "--worker-output",
            str(worker_output),
            "--model",
            model_size,
            "--device",
            device,
            "--source-language",
            source_language,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode != 0 or not worker_output.is_file():
        detail = (result.stderr.strip() or result.stdout.strip())[-1200:]
        raise ExtractionError(f"外部 faster-whisper 运行失败：{detail}")
    try:
        payload = json.loads(worker_output.read_text(encoding="utf-8"))
    finally:
        worker_output.unlink(missing_ok=True)
    return (
        payload["cues"],
        payload["words"],
        int(payload["duration_ms"]),
        str(source),
        normalise_language(payload.get("transcript_language")),
        payload.get("language_probability"),
    )


def process(
    input_source: str,
    out_dir: Path,
    model_size: str = "auto",
    device: str = "cpu",
    force_asr: bool = False,
    asr_python: str = "",
    source_language: str = "auto",
) -> dict[str, Any]:
    require_public_source(input_source)
    out_dir.mkdir(parents=True, exist_ok=True)
    source = input_source.strip()
    cues: list[dict[str, Any]] | None = None
    words: list[dict[str, Any]] = []
    duration_ms = 0
    transcript_source = "local-asr"
    transcript_language = normalise_language(source_language)
    language_probability: float | None = None
    asr_runtime_source: str | None = None
    resolved_model: str | None = None
    model_source = "not-used"
    cached_models: list[str] = []
    if source.startswith(("http://", "https://")):
        host = urllib.parse.urlparse(source).netloc.casefold()
        if "bilibili.com" in host or "b23.tv" in host:
            progress("bilibili-resolve")
            cues, audio_path, duration_ms = resolve_bilibili(source, out_dir)
            transcript_source = "bilibili-native" if cues and not force_asr else "local-asr"
            if cues and transcript_language == "auto":
                transcript_language = "zh"
        else:
            if not force_asr:
                native = resolve_generic_captions(source, out_dir, source_language)
                if native is not None:
                    cues, transcript_source, transcript_language = native
            audio_path = download_generic_audio(source, out_dir)
            if cues is None:
                transcript_source = "local-asr"
    else:
        local = Path(source).expanduser().resolve()
        if not local.is_file():
            raise ExtractionError(f"本地文件不存在：{local}")
        progress("local-audio-normalise", local.name)
        audio_path = normalise_local_audio(local, out_dir)
    if cues is None or force_asr:
        progress("asr-start")
        resolved_model, model_source, cached_models = resolve_asr_model(model_size)
        progress(
            "asr-model-select",
            f"requested={model_size} resolved={resolved_model} source={model_source}",
        )
        cues, words, asr_duration, asr_runtime_source, transcript_language, language_probability = transcribe_audio(
            audio_path, resolved_model, device, asr_python, source_language
        )
        duration_ms = max(duration_ms, asr_duration)
        transcript_source = f"local-asr/{asr_runtime_source}"
    progress("write-package")
    normalised = normalise_cues(cues)
    quality = transcript_quality(normalised, duration_ms, transcript_language, language_probability)
    result = save_outputs(
        normalised,
        words,
        out_dir,
        audio_path,
        duration_ms,
        source,
        transcript_source,
        transcript_language,
        quality,
    )
    result["asr_runtime_source"] = asr_runtime_source
    result["requested_asr_model"] = model_size
    result["asr_model"] = resolved_model
    result["asr_model_source"] = model_source
    result["cached_asr_models"] = cached_models
    result["requested_language"] = normalise_language(source_language)
    manifest_path = Path(result["manifest_path"])
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def doctor(asr_python: str = "") -> dict[str, Any]:
    runtime = runtime_info()
    asr, asr_source = resolve_asr_python(asr_python)
    yt_dlp_executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if not yt_dlp_executable:
        local_bin = Path.home() / ".local" / "bin" / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
        yt_dlp_executable = str(local_bin) if local_bin.is_file() else None
    return {
        "ffmpeg": {"available": bool(runtime.get("available")), "path": runtime.get("path"), "source": runtime.get("source")},
        "faster_whisper": {
            "available": asr is not None,
            "python": str(asr) if asr else None,
            "source": asr_source,
            "cached_models": cached_asr_models() if asr is not None else [],
        },
        "yt_dlp": {
            "available": importlib.util.find_spec("yt_dlp") is not None or bool(yt_dlp_executable),
            "source": "python-module" if importlib.util.find_spec("yt_dlp") is not None else "executable" if yt_dlp_executable else None,
            "path": yt_dlp_executable,
        },
        "opencc": {"available": importlib.util.find_spec("opencc") is not None},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从视频链接或本地音视频提取音频、字幕和文案（不调用大模型）")
    parser.add_argument("input", nargs="?", default="", help="B站/抖音/YouTube 等链接，或本地音视频文件")
    parser.add_argument("--out-dir", "-o", default="output", help="输出目录")
    parser.add_argument(
        "--model",
        "-m",
        default="auto",
        choices=["auto", "tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"],
        help="auto=优先复用已缓存模型；没有缓存时首次使用 small。其他值表示用户明确选择的型号",
    )
    parser.add_argument("--device", "-d", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument(
        "--source-language",
        default="auto",
        help="源音频语言代码，例如 en、zh；auto 会读取字幕元数据或由 ASR 判断",
    )
    parser.add_argument("--force-asr", action="store_true", help="即使存在 B 站原生字幕，也重新本地识别并生成逐词时间")
    parser.add_argument("--asr-python", default="", help="显式指定已安装 faster-whisper 的 Python")
    parser.add_argument("--doctor", action="store_true", help="只检查提取环境，不下载、不转写")
    parser.add_argument("--asr-worker", help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.doctor:
            print(json.dumps(doctor(args.asr_python), ensure_ascii=False, indent=2))
            return 0
        if args.asr_worker:
            if not args.worker_output:
                raise ExtractionError("ASR worker 缺少 --worker-output")
            cues, words, duration_ms, language, probability = _transcribe_current(
                Path(args.asr_worker).resolve(), args.model, args.device, args.source_language
            )
            Path(args.worker_output).write_text(
                json.dumps({
                    "cues": cues,
                    "words": words,
                    "duration_ms": duration_ms,
                    "transcript_language": language,
                    "language_probability": probability,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            return 0
        if not args.input:
            raise ExtractionError("缺少视频链接或本地音视频文件")
        result = process(
            args.input,
            Path(args.out_dir).expanduser().resolve(),
            args.model,
            args.device,
            args.force_asr,
            args.asr_python,
            args.source_language,
        )
        if result["quality"]["status"] != "passed":
            print(f"MANIFEST={result['manifest_path']}")
            raise ExtractionError("转写质量检查未通过：" + "；".join(result["quality"]["errors"]))
    except (ExtractionError, OSError, ValueError, KeyError) as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 2
    print(f"[ok] 提取完成：{result['cue_count']} 条字幕，来源 {result['transcript_source']}")
    print(f"MANIFEST={result['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
