#!/usr/bin/env python3
"""
多幕合并：把各场景的白板动画 MP4 按顺序硬切拼接成一条完整视频。

优先用系统 ffmpeg 无损拼接（-c copy，不重编码）；各片尺寸/编码不一致或无
ffmpeg 时，回退到 PyAV 逐帧重编码并缩放补边到第一段尺寸。单片仍保留。

用法：
  <ENV_PY> merge_scenes.py --inputs a.mp4 b.mp4 c.mp4 --output final.mp4
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from ffmpeg_runtime import runtime_info


def _ffmpeg_concat_copy(inputs: list[Path], output: Path) -> bool:
    runtime = runtime_info()
    ffmpeg = runtime.get("path")
    capabilities = runtime.get("capabilities", {})
    if not ffmpeg or not capabilities.get("concat_demuxer"):
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in inputs:
            f.write(f"file '{p.resolve().as_posix()}'\n")
        list_path = Path(f.name)
    try:
        res = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", str(output)],
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            print(f"  FFmpeg 无损拼接完成({runtime.get('source')}): {output}")
            return True
        print(f"  [warn] ffmpeg -c copy 失败，尝试重编码: {res.stderr.strip()[:200]}")
        if not capabilities.get("libx264"):
            print("  [warn] 当前 FFmpeg 不含 libx264，改用 PyAV 合并")
            return False
        res = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", str(output)],
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            print(f"  ffmpeg 重编码拼接完成: {output}")
            return True
        print(f"  [warn] ffmpeg 重编码也失败: {res.stderr.strip()[:200]}")
        return False
    finally:
        list_path.unlink(missing_ok=True)


def _pyav_concat(inputs: list[Path], output: Path) -> bool:
    try:
        import av
    except ImportError:
        return False
    import shutil
    import tempfile

    temp_out = Path(tempfile.gettempdir()) / f"merged_out_{output.stem}.mp4"
    temp_inputs: list[Path] = []
    try:
        for idx, p in enumerate(inputs):
            tmp_in = Path(tempfile.gettempdir()) / f"tmp_in_{idx}_{p.stem}.mp4"
            shutil.copy2(p, tmp_in)
            temp_inputs.append(tmp_in)

        first = av.open(str(temp_inputs[0]))
        vs = first.streams.video[0]
        w, h = vs.codec_context.width, vs.codec_context.height
        rate = vs.average_rate
        first.close()

        out = av.open(str(temp_out), mode="w")
        ostream = out.add_stream("h264", rate=rate)
        ostream.width, ostream.height = w, h
        ostream.pix_fmt = "yuv420p"
        ostream.options = {"crf": "24", "preset": "medium"}
        from fractions import Fraction
        pts_counter = 0
        for p in inputs:
            cont = av.open(str(p))
            for frame in cont.decode(video=0):
                if frame.width != w or frame.height != h:
                    frame = frame.reformat(width=w, height=h)
                frame.time_base = Fraction(1, int(round(float(rate)))) if rate else Fraction(1, 60)
                frame.pts = pts_counter
                pts_counter += 1
                for pkt in ostream.encode(frame):
                    out.mux(pkt)
            cont.close()
        for pkt in ostream.encode(None):
            out.mux(pkt)
        out.close()

        shutil.move(str(temp_out), str(output))
        print(f"  PyAV 拼接完成: {output}")
        return True
    finally:
        for t in temp_inputs:
            t.unlink(missing_ok=True)
        temp_out.unlink(missing_ok=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="按顺序合并多幕白板动画 MP4")
    p.add_argument("--inputs", nargs="+", required=True, help="按播放顺序的 MP4 列表")
    p.add_argument("--output", required=True, help="合并输出路径")
    args = p.parse_args(argv)

    inputs = [Path(x) for x in args.inputs]
    missing = [str(x) for x in inputs if not x.exists()]
    if missing:
        print(f"[err] 缺少输入文件: {', '.join(missing)}", file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if _ffmpeg_concat_copy(inputs, output) or _pyav_concat(inputs, output):
        print(f"OUTPUT={output.resolve()}")
        return 0
    print("[err] 合并失败：隔离环境与系统均无可用 FFmpeg，且 PyAV 不可用", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
