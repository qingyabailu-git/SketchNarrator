#!/usr/bin/env python3
"""Build a deterministic reference-video analysis package.

The analysis flow adapts the useful core of OpenMontage's VideoAnalyzer
(source acquisition, scene/pacing analysis, motion labels and keyframes) to
SketchNarrator's local project contract. It deliberately does not call an LLM:
visual interpretation remains an explicit Codex review step after this script.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

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

VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


class ReferenceAnalysisError(RuntimeError):
    pass


def progress(stage: str, detail: str = "") -> None:
    message = f"REFERENCE_ANALYSIS_STAGE={stage}"
    if detail:
        message += f" DETAIL={detail}"
    print(message, flush=True)


def _display_source(value: str) -> str:
    return source_label(value)


def _yt_dlp_command_available() -> bool:
    if importlib.util.find_spec("yt_dlp"):
        return True
    if shutil.which("yt-dlp") or shutil.which("yt-dlp.exe"):
        return True
    local_bin = Path.home() / ".local" / "bin" / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    return local_bin.is_file()


def download_reference_video(url: str, out_dir: Path) -> tuple[Path, dict[str, Any]]:
    if not _yt_dlp_command_available():
        raise ReferenceAnalysisError("完整视频分析需要 yt-dlp；请先通过公开 extract-source 流程准备隔离环境。")
    try:
        import yt_dlp
    except ImportError as exc:
        raise ReferenceAnalysisError("当前隔离环境无法导入 yt-dlp。") from exc

    runtime = runtime_info()
    options: dict[str, Any] = {
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "reference-video.%(ext)s"),
        "noplaylist": True,
        "overwrites": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "quiet": False,
        "no_warnings": False,
    }
    if runtime.get("path"):
        options["ffmpeg_location"] = str(runtime["path"])
    progress("download-video", "quality<=720p")
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
    except Exception as exc:
        raise ReferenceAnalysisError(f"参考视频下载失败：{str(exc)[-800:]}") from exc
    if isinstance(info, dict) and isinstance(info.get("entries"), list):
        info = next((item for item in info["entries"] if isinstance(item, dict)), info)
    candidates = sorted(
        (
            path
            for path in out_dir.glob("reference-video.*")
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise ReferenceAnalysisError("yt-dlp 没有生成可分析的视频文件。")
    metadata = info if isinstance(info, dict) else {}
    return candidates[0], {
        "title": str(metadata.get("title") or ""),
        "uploader": str(metadata.get("uploader") or metadata.get("channel") or ""),
        "webpage_url": _display_source(str(metadata.get("webpage_url") or url)),
    }


def resolve_video(input_source: str, out_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    require_public_source(input_source)
    value = input_source.strip()
    if value.startswith(("http://", "https://")):
        video_path, metadata = download_reference_video(value, out_dir)
        return video_path, "url", metadata
    video_path = Path(value).expanduser().resolve()
    if not video_path.is_file():
        raise ReferenceAnalysisError(f"本地视频不存在：{video_path}")
    if video_path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ReferenceAnalysisError("完整分析只接受视频；本地音频请选择 transcript 范围。")
    return video_path, "local-video", {"title": video_path.stem, "uploader": "", "webpage_url": ""}


def open_video(path: Path) -> tuple[cv2.VideoCapture, dict[str, Any]]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ReferenceAnalysisError(f"OpenCV 无法打开参考视频：{path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise ReferenceAnalysisError("参考视频缺少可靠的帧率、帧数或尺寸元数据。")
    duration_ms = max(1, round(frame_count / fps * 1000))
    return capture, {
        "fps": round(fps, 6),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_ms": duration_ms,
    }


def _sample_frame(capture: cv2.VideoCapture, time_ms: int) -> np.ndarray | None:
    capture.set(cv2.CAP_PROP_POS_MSEC, max(0, int(time_ms)))
    ok, frame = capture.read()
    return frame if ok and frame is not None else None


def _analysis_gray(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = min(1.0, 320.0 / max(1, width))
    resized = cv2.resize(
        frame,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def motion_sample_interval_ms(duration_ms: int) -> int:
    """Use 4 Hz for ordinary clips and bound analysis samples for long videos."""

    return max(250, math.ceil(duration_ms / 4800 / 50) * 50)


def sample_motion(capture: cv2.VideoCapture, duration_ms: int) -> list[dict[str, float]]:
    # Random seeking for every sample is extremely slow on inter-frame video:
    # each seek may decode again from an earlier keyframe. Decode the stream once
    # in order and retrieve only the frames needed for analysis.
    interval_ms = motion_sample_interval_ms(duration_ms)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps <= 0:
        raise ReferenceAnalysisError("参考视频缺少可靠帧率，无法顺序抽样。")
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    samples: list[dict[str, float]] = []
    previous: np.ndarray | None = None
    frame_index = 0
    next_sample_ms = 0.0
    while next_sample_ms < duration_ms and capture.grab():
        frame_time_ms = frame_index * 1000.0 / fps
        if frame_time_ms + 0.5 >= next_sample_ms:
            ok, frame = capture.retrieve()
            if ok and frame is not None:
                gray = _analysis_gray(frame)
                score = 0.0 if previous is None else float(np.mean(cv2.absdiff(previous, gray)))
                samples.append({"time_ms": float(round(frame_time_ms)), "motion_score": round(score, 4)})
                previous = gray
            next_sample_ms += interval_ms
        frame_index += 1
    if not samples:
        raise ReferenceAnalysisError("参考视频未能抽取任何分析帧。")
    return samples


def detect_scenes(samples: list[dict[str, float]], duration_ms: int) -> tuple[list[dict[str, Any]], float]:
    scores = np.asarray([sample["motion_score"] for sample in samples[1:]], dtype=np.float32)
    if scores.size:
        median = float(np.median(scores))
        mad = float(np.median(np.abs(scores - median)))
        threshold = max(12.0, float(np.percentile(scores, 90)), median + 4.0 * max(1.0, 1.4826 * mad))
    else:
        threshold = 12.0
    cuts: list[int] = []
    minimum_scene_ms = 500
    previous_cut = 0
    for index in range(1, len(samples) - 1):
        score = samples[index]["motion_score"]
        time_ms = int(samples[index]["time_ms"])
        if score < threshold or score < samples[index - 1]["motion_score"] or score < samples[index + 1]["motion_score"]:
            continue
        if time_ms - previous_cut < minimum_scene_ms or duration_ms - time_ms < minimum_scene_ms:
            continue
        cuts.append(time_ms)
        previous_cut = time_ms
    boundaries = [0, *cuts, duration_ms]
    scenes: list[dict[str, Any]] = []
    for index, (start_ms, end_ms) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        internal_scores = [
            float(sample["motion_score"])
            for sample in samples
            if start_ms + 250 <= sample["time_ms"] < end_ms
        ]
        motion_score = float(np.mean(internal_scores)) if internal_scores else 0.0
        if motion_score < 1.5:
            motion_type = "low-frame-difference"
        elif motion_score < 4.5:
            motion_type = "subtle-motion"
        else:
            motion_type = "active-motion"
        scenes.append({
            "scene_id": f"ref-scene-{index:03d}",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "motion_type": motion_type,
            "motion_score": round(motion_score, 4),
            "keyframe_path": None,
            "visual_description": "",
            "transition_in": "cut" if index > 1 else "start",
        })
    return scenes, round(threshold, 4)


def _even_indices(total: int, limit: int) -> list[int]:
    if total <= limit:
        return list(range(total))
    if limit <= 1:
        return [0]
    return sorted({round(index * (total - 1) / (limit - 1)) for index in range(limit)})


def save_keyframes(
    capture: cv2.VideoCapture,
    scenes: list[dict[str, Any]],
    keyframe_dir: Path,
    max_keyframes: int,
) -> list[dict[str, Any]]:
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    duration_ms = int(scenes[-1]["end_ms"])
    # Opening comes first; distribute remaining samples over time, not merely
    # over detected cuts (a long animated board may never register as a cut).
    timestamps = [time_ms for time_ms in (0, 1000, 2000, 3000) if time_ms < duration_ms][:max_keyframes]
    remaining = max_keyframes - len(timestamps)
    candidates = sorted(set(
        [int((scene["start_ms"] + scene["end_ms"]) / 2) for scene in scenes]
        + list(range(5000, duration_ms, 5000))
    ) - set(timestamps))
    timestamps.extend(candidates[index] for index in _even_indices(len(candidates), remaining) if remaining > 0)
    keyframes: list[dict[str, Any]] = []
    for timestamp_ms in sorted(set(timestamps)):
        scene = next(scene for scene in scenes if scene["start_ms"] <= timestamp_ms < scene["end_ms"])
        frame = _sample_frame(capture, timestamp_ms)
        if frame is None:
            continue
        filename = f"{scene['scene_id']}-{timestamp_ms:010d}ms.jpg"
        path = keyframe_dir / filename
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            continue
        path.write_bytes(encoded.tobytes())
        relative = f"keyframes/{filename}"
        scene["keyframe_path"] = relative
        keyframes.append({
            "scene_id": scene["scene_id"],
            "timestamp_ms": timestamp_ms,
            "path": relative,
            "visual_description": "",
        })
    if not keyframes:
        raise ReferenceAnalysisError("参考视频未能生成关键帧。")
    return keyframes


def build_contact_sheet(keyframes: list[dict[str, Any]], out_dir: Path) -> Path:
    columns = min(4, max(1, len(keyframes)))
    tile_width = 320
    caption_height = 34
    loaded: list[tuple[dict[str, Any], Image.Image]] = []
    max_image_height = 1
    for item in keyframes:
        image = Image.open(out_dir / item["path"]).convert("RGB")
        ratio = tile_width / image.width
        resized = image.resize((tile_width, max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
        max_image_height = max(max_image_height, resized.height)
        loaded.append((item, resized))
    rows = math.ceil(len(loaded) / columns)
    sheet = Image.new("RGB", (columns * tile_width, rows * (max_image_height + caption_height)), "#F3F3F3")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (item, image) in enumerate(loaded):
        column = index % columns
        row = index // columns
        x = column * tile_width
        y = row * (max_image_height + caption_height)
        sheet.paste(image, (x, y))
        seconds = item["timestamp_ms"] / 1000.0
        draw.text((x + 8, y + max_image_height + 9), f"{item['scene_id']}  {seconds:.2f}s", fill="#202020", font=font)
    target = out_dir / "contact-sheet.jpg"
    sheet.save(target, format="JPEG", quality=90)
    return target


def load_transcript_summary(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.is_file():
        raise ReferenceAnalysisError(f"字幕提取清单不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "manifest_path": str(path.resolve()),
        "transcript_source": payload.get("transcript_source"),
        "duration_ms": payload.get("duration_ms"),
        "cue_count": payload.get("cue_count"),
        "word_count": payload.get("word_count"),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    source = payload["source"]
    pacing = payload["structure"]["pacing"]
    lines = [
        "# 参考视频分析包",
        "",
        "> 本文件只记录确定性机器分析。Codex 仍需实际查看联系表和关键帧，补充视觉判断后才能用于改写。",
        "",
        "## 来源与范围",
        "",
        f"- 输入：`{source['display']}`",
        f"- 分析范围：完整视频（文案、镜头、画面、节奏）",
        f"- 时长：{source['duration_ms'] / 1000:.3f} 秒",
        f"- 画面：{source['width']} × {source['height']}，{source['fps']:.3f} fps",
        "- 下载限制：网络视频最高 720p；本地视频直接读取",
        "",
        "## 机器节奏摘要",
        "",
        f"- 镜头数：{pacing['scene_count']}",
        f"- 平均镜头：{pacing['average_scene_ms'] / 1000:.3f} 秒",
        f"- 最短 / 最长：{pacing['shortest_scene_ms'] / 1000:.3f} / {pacing['longest_scene_ms'] / 1000:.3f} 秒",
        f"- 每分钟切换：{pacing['cuts_per_minute']:.2f}",
        f"- 镜头检测阈值：{payload['structure']['scene_detection_threshold']:.4f}",
        "",
        "## 镜头表",
        "",
        "| 镜头 | 开始 | 结束 | 时长 | 运动类型 | 关键帧 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for scene in payload["structure"]["scenes"]:
        lines.append(
            f"| {scene['scene_id']} | {scene['start_ms'] / 1000:.2f}s | {scene['end_ms'] / 1000:.2f}s | "
            f"{scene['duration_ms'] / 1000:.2f}s | {scene['motion_type']} | {scene['keyframe_path'] or ''} |"
        )
    lines.extend([
        "",
        "## 待 Codex 实际看图补充",
        "",
        "- 前三秒视觉钩子：",
        "- 主体、背景、色彩、字幕和构图风格：",
        "- 镜头与转场规律：",
        "- 值得保留的表达：",
        "- 需要改写或替换的内容：",
        "- 适配 SketchNarrator 手绘风格的办法：",
        "",
        "关键帧总览见 `contact-sheet.jpg`。逐帧文件位于 `keyframes/`。",
        "",
    ])
    return "\n".join(lines)


def analyze(
    input_source: str,
    out_dir: Path,
    transcript_manifest: Path | None = None,
    max_keyframes: int = 20,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path, input_kind, remote_metadata = resolve_video(input_source, out_dir)
    progress("probe-video", video_path.name)
    capture, metadata = open_video(video_path)
    try:
        progress("scene-detect")
        samples = sample_motion(capture, int(metadata["duration_ms"]))
        scenes, threshold = detect_scenes(samples, int(metadata["duration_ms"]))
        progress("keyframes", f"scenes={len(scenes)} max={max_keyframes}")
        keyframes = save_keyframes(capture, scenes, out_dir / "keyframes", max_keyframes)
    finally:
        capture.release()
    progress("contact-sheet", f"frames={len(keyframes)}")
    contact_sheet = build_contact_sheet(keyframes, out_dir)
    durations = [int(scene["duration_ms"]) for scene in scenes]
    duration_minutes = max(1, int(metadata["duration_ms"])) / 60_000
    pacing = {
        "scene_count": len(scenes),
        "average_scene_ms": round(sum(durations) / len(durations)),
        "shortest_scene_ms": min(durations),
        "longest_scene_ms": max(durations),
        "cuts_per_minute": round(max(0, len(scenes) - 1) / duration_minutes, 4),
    }
    payload: dict[str, Any] = {
        "version": 1,
        "analysis_mode": "full-reference-video",
        "adapted_from": {
            "project": "calesthio/OpenMontage",
            "component": "tools/analysis/video_analyzer.py",
            "snapshot": "cd9f3c1f03368be87b140af494914b8ee4e3c7a4",
        },
        "source": {
            "kind": input_kind,
            "display": _display_source(input_source),
            "analysis_video_path": str(video_path.resolve()),
            **metadata,
            **remote_metadata,
        },
        "transcript": load_transcript_summary(transcript_manifest),
        "structure": {
            "scene_detection_method": "opencv-sequential-sampled-frame-difference",
            "motion_sample_count": len(samples),
            "motion_sample_interval_ms": motion_sample_interval_ms(int(metadata["duration_ms"])),
            "scene_detection_threshold": threshold,
            "pacing": pacing,
            "scenes": scenes,
        },
        "keyframes": keyframes,
        "visual_review": {
            "status": "awaiting-codex-visual-review",
            "opening_hook": "",
            "visual_style": "",
            "subtitle_style": "",
            "camera_and_transition_patterns": [],
            "keep": [],
            "change": [],
            "sketch_narrator_adaptation": [],
            "sampling_limit": "Frame differences do not prove the absence of animation. Review the opening and consecutive motion in long scenes using the local analysis video.",
            "suggested_motion_windows": [
                {"start_ms": int(scene["start_ms"]), "end_ms": min(int(scene["end_ms"]), int(scene["start_ms"]) + 3000)}
                for scene in scenes
            ],
        },
        "resource_notice": {
            "downloaded_full_video": input_kind == "url",
            "maximum_download_height": 720 if input_kind == "url" else None,
            "costs": ["additional network traffic", "disk space", "CPU time", "visual review time"],
        },
    }
    markdown_path = out_dir / "source-analysis.md"
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    manifest_path = out_dir / "source-analysis.json"
    payload["outputs"] = {
        "markdown": str(markdown_path.resolve()),
        "contact_sheet": str(contact_sheet.resolve()),
        "keyframes_dir": str((out_dir / "keyframes").resolve()),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["manifest_path"] = str(manifest_path.resolve())
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成参考视频的镜头、节奏、运动与关键帧分析包（不调用大模型）")
    parser.add_argument("input", help="公开视频链接或本地视频文件")
    parser.add_argument("--out-dir", required=True, help="分析包输出目录")
    parser.add_argument("--transcript-manifest", help="同次 extract-source 生成的 extraction.json")
    parser.add_argument("--max-keyframes", type=int, default=20, help="最多保存的关键帧数量")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.max_keyframes < 1 or args.max_keyframes > 60:
            raise ReferenceAnalysisError("--max-keyframes 必须在 1 到 60 之间。")
        result = analyze(
            args.input,
            Path(args.out_dir).expanduser().resolve(),
            Path(args.transcript_manifest).expanduser().resolve() if args.transcript_manifest else None,
            args.max_keyframes,
        )
    except (ReferenceAnalysisError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 2
    print(
        "REFERENCE_ANALYSIS_STATUS=complete "
        f"SCENES={result['structure']['pacing']['scene_count']} "
        f"MANIFEST={result['manifest_path']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
