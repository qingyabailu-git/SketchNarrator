#!/usr/bin/env python3
"""Burn Chinese SRT and mux narration using PyAV when ffmpeg.exe is unavailable."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont


RENDERER_SCRIPTS = Path(__file__).resolve().parents[1] / "renderer" / "scripts"
if str(RENDERER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RENDERER_SCRIPTS))

from font_runtime import load_cjk_font  # noqa: E402


TIME_RE = re.compile(
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})"
)


def to_ms(value: str) -> int:
    match = TIME_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"无效 SRT 时间：{value}")
    parts = {key: int(raw) for key, raw in match.groupdict().items()}
    return ((parts["h"] * 60 + parts["m"]) * 60 + parts["s"]) * 1000 + parts["ms"]


def read_srt(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if timing_index is None:
            continue
        start_raw, end_raw = lines[timing_index].split(" --> ", 1)
        caption = " ".join(lines[timing_index + 1 :]).strip()
        if caption:
            cues.append({"start_ms": to_ms(start_raw), "end_ms": to_ms(end_raw), "text": caption})
    if not cues:
        raise ValueError("SRT 中没有有效字幕")
    return cues


def choose_font(size: int) -> ImageFont.FreeTypeFont:
    return load_cjk_font(size)


def caption_for(cues: list[dict], time_ms: int) -> str | None:
    for cue in cues:
        if cue["start_ms"] <= time_ms <= cue["end_ms"]:
            return cue["text"]
    return None


def draw_caption(array: np.ndarray, text: str, font: ImageFont.FreeTypeFont) -> np.ndarray:
    image = Image.fromarray(array)
    draw = ImageDraw.Draw(image)
    box = draw.textbbox((0, 0), text, font=font, stroke_width=3)
    text_width = box[2] - box[0]
    x = max(24, (image.width - text_width) // 2)
    y = image.height - (box[3] - box[1]) - max(34, round(image.height * 0.045))
    draw.text(
        (x, y), text, font=font, fill=(255, 255, 255),
        stroke_width=3, stroke_fill=(24, 24, 24),
    )
    return np.asarray(image)


class SfxPcmReader:
    """Read the renderer's deterministic 48kHz PCM16 effect track in audio-frame chunks."""

    def __init__(self, path: Path):
        self.stream = wave.open(str(path), "rb")
        self.channels = self.stream.getnchannels()
        if (
            self.stream.getframerate() != 48000
            or self.stream.getsampwidth() != 2
            or self.channels not in {1, 2}
        ):
            self.stream.close()
            raise ValueError("音效轨必须是 48000Hz PCM16 mono/stereo WAV")

    def read_stereo(self, samples: int) -> np.ndarray:
        raw = self.stream.readframes(samples)
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if self.channels == 2 and data.size:
            data = data.reshape(-1, 2).T
        elif data.size:
            data = np.vstack([data, data])
        else:
            data = np.zeros((2, 0), dtype=np.float32)
        if data.shape[1] < samples:
            data = np.pad(data, ((0, 0), (0, samples - data.shape[1])))
        return data[:, :samples]

    def close(self) -> None:
        self.stream.close()


class OverlayReader:
    """Decode VP9 alpha with libvpx; PyAV's default VP9 decoder drops alpha."""

    def __init__(self, path: Path, width: int, height: int, fps: float, ffmpeg_path: Path):
        with av.open(str(path)) as container:
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            video_streams = [stream for stream in container.streams if stream.type == "video"]
            if len(video_streams) != 1 or audio_streams:
                raise ValueError("透明覆盖层必须且只能包含一个无声视频轨")
            stream = video_streams[0]
            if int(stream.width) != width or int(stream.height) != height:
                raise ValueError(
                    f"透明覆盖层分辨率 {stream.width}x{stream.height} 与底片 {width}x{height} 不一致"
                )
            alpha_mode = str(stream.metadata.get("ALPHA_MODE") or stream.metadata.get("alpha_mode") or "")
            if alpha_mode != "1":
                raise ValueError("透明覆盖层缺少 ALPHA_MODE=1")
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_bytes = width * height * 4
        self.index = -1
        self.current: np.ndarray | None = None
        self.process = subprocess.Popen(
            [
                str(ffmpeg_path), "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path),
                "-an", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if self.process.stdout is None:
            raise ValueError("无法启动透明覆盖层解码器")

    def _decode_next(self) -> np.ndarray | None:
        chunks: list[bytes] = []
        remaining = self.frame_bytes
        while remaining:
            chunk = self.process.stdout.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return np.frombuffer(b"".join(chunks), dtype=np.uint8).reshape(self.height, self.width, 4).copy()

    def frame_at(self, time_ms: int) -> np.ndarray | None:
        target = max(0, round(time_ms * self.fps / 1000))
        while self.index < target:
            frame = self._decode_next()
            if frame is None:
                break
            self.current = frame
            self.index += 1
        return self.current

    def close(self) -> None:
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=10)


def composite_overlay(base: np.ndarray, rgba: np.ndarray | None) -> np.ndarray:
    if rgba is None:
        return base
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    if not np.any(alpha > 0):
        return base
    foreground = rgba[..., :3].astype(np.float32)
    background = base.astype(np.float32)
    return np.clip(foreground * alpha + background * (1.0 - alpha), 0, 255).astype(np.uint8)


def mix_sfx(item: av.AudioFrame, reader: SfxPcmReader | None) -> av.AudioFrame:
    if reader is None:
        return item
    narration = item.to_ndarray().astype(np.float32, copy=False)
    effects = reader.read_stereo(item.samples)
    mixed = np.clip(narration + effects, -0.98, 0.98).astype(np.float32, copy=False)
    frame = av.AudioFrame.from_ndarray(mixed, format="fltp", layout="stereo")
    frame.sample_rate = 48000
    return frame


def compose(
    video_path: Path,
    audio_path: Path,
    captions_path: Path,
    output_path: Path,
    duration_ms: int = 0,
    sfx_path: Path | None = None,
    overlay_path: Path | None = None,
    overlay_ffmpeg_path: Path | None = None,
) -> None:
    cues = read_srt(captions_path)
    video_input = av.open(str(video_path))
    audio_input = av.open(str(audio_path))
    video_in = video_input.streams.video[0]
    audio_in = audio_input.streams.audio[0]
    rate = video_in.average_rate or Fraction(30, 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = av.open(str(output_path), mode="w")

    video_out = output.add_stream("libx264", rate=rate)
    video_out.width = video_in.width
    video_out.height = video_in.height
    video_out.pix_fmt = "yuv420p"
    video_out.options = {"crf": "19", "preset": "medium"}
    # All streams must exist before the first packet is muxed; the first mux
    # writes the MP4 header and fixes each stream's time base.
    audio_out = output.add_stream("aac", rate=48000)
    audio_out.bit_rate = 160_000
    audio_out.layout = "stereo"
    font = choose_font(max(32, round(video_in.height * 0.055)))
    overlay_reader = (
        OverlayReader(overlay_path, video_in.width, video_in.height, float(rate), overlay_ffmpeg_path)
        if overlay_path and overlay_ffmpeg_path else None
    )
    if overlay_path is not None and overlay_reader is None:
        raise ValueError("透明覆盖层合成必须显式提供支持 libvpx-vp9 的 FFmpeg")

    for frame in video_input.decode(video_in):
        image = frame.to_ndarray(format="rgb24")
        time_ms = round(float(frame.pts * frame.time_base) * 1000) if frame.pts is not None else 0
        if overlay_reader is not None:
            image = composite_overlay(image, overlay_reader.frame_at(time_ms))
        caption = caption_for(cues, time_ms)
        if caption:
            image = draw_caption(image, caption, font)
        output_frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        output_frame.pts = frame.pts
        output_frame.time_base = frame.time_base
        for packet in video_out.encode(output_frame):
            output.mux(packet)
    for packet in video_out.encode():
        output.mux(packet)

    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
    sfx_reader = SfxPcmReader(sfx_path) if sfx_path is not None else None
    audio_pts = 0
    for frame in audio_input.decode(audio_in):
        resampled = resampler.resample(frame)
        frames = resampled if isinstance(resampled, list) else [resampled]
        for item in frames:
            if item is None:
                continue
            item = mix_sfx(item, sfx_reader)
            item.pts = audio_pts
            item.time_base = Fraction(1, 48000)
            audio_pts += item.samples
            for packet in audio_out.encode(item):
                output.mux(packet)
    flushed = resampler.resample(None)
    frames = flushed if isinstance(flushed, list) else [flushed]
    for item in frames:
        if item is None:
            continue
        item = mix_sfx(item, sfx_reader)
        item.pts = audio_pts
        item.time_base = Fraction(1, 48000)
        audio_pts += item.samples
        for packet in audio_out.encode(item):
            output.mux(packet)
    target_samples = round(duration_ms / 1000 * 48000) if duration_ms > 0 else audio_pts
    while audio_pts < target_samples:
        sample_count = min(1024, target_samples - audio_pts)
        tail = sfx_reader.read_stereo(sample_count) if sfx_reader is not None else np.zeros(
            (2, sample_count), dtype=np.float32
        )
        silence = av.AudioFrame.from_ndarray(
            tail,
            format="fltp",
            layout="stereo",
        )
        silence.sample_rate = 48000
        silence.pts = audio_pts
        silence.time_base = Fraction(1, 48000)
        audio_pts += sample_count
        for packet in audio_out.encode(silence):
            output.mux(packet)
    for packet in audio_out.encode():
        output.mux(packet)

    output.close()
    video_input.close()
    audio_input.close()
    if sfx_reader is not None:
        sfx_reader.close()
    if overlay_reader is not None:
        overlay_reader.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="用 PyAV 合成手绘画面、配音和中文字幕")
    parser.add_argument("--video", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--captions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration-ms", type=int, default=0)
    parser.add_argument("--sfx", help="可选：48000Hz PCM16 WAV 音效轨")
    parser.add_argument("--overlay", help="可选：与底片同尺寸、同时间轴、无声的透明 WebM 覆盖层")
    parser.add_argument("--overlay-ffmpeg", help="透明 WebM 的 libvpx-vp9 解码器")
    args = parser.parse_args()
    try:
        compose(
            Path(args.video),
            Path(args.audio),
            Path(args.captions),
            Path(args.output),
            args.duration_ms,
            Path(args.sfx) if args.sfx else None,
            Path(args.overlay) if args.overlay else None,
            Path(args.overlay_ffmpeg) if args.overlay_ffmpeg else None,
        )
    except Exception as exc:
        print(f"[err] PyAV 合成失败：{exc}", file=sys.stderr)
        return 1
    print(f"OUTPUT={Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
