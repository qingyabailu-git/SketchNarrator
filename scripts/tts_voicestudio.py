#!/usr/bin/env python3
"""Use a running VoiceStudio instance as a local TTS provider.

VoiceStudio stays outside this Skill. The adapter stores generated audio and
non-secret provenance only. Word timing remains the responsibility of
``align_words.py`` because the public compatibility endpoint returns audio,
not word boundaries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from network_safety import safe_diagnostic, require_credential_transport


DEFAULT_BASE_URL = "http://127.0.0.1:3900"
DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "default"
MAX_INPUT_CHARS = 4096


class VoiceStudioError(RuntimeError):
    """A concise, user-facing VoiceStudio connection or API failure."""


def normalize_roots(base_url: str) -> tuple[str, str]:
    """Return ``(server_root, api_root)`` for either host or ``/v1`` input."""

    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("VoiceStudio 地址必须是 http:// 或 https:// 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("VoiceStudio 地址不能包含用户名或密码；令牌只能通过环境变量提供")
    if parsed.query or parsed.fragment:
        raise ValueError("VoiceStudio 地址不能包含查询参数或锚点")
    if value.endswith("/v1"):
        return value[:-3].rstrip("/"), value
    return value, f"{value}/v1"


def request_headers(*, json_body: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json, audio/wav"}
    if json_body:
        headers["Content-Type"] = "application/json; charset=utf-8"
    api_key = os.environ.get("VOICESTUDIO_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def error_detail(error: urllib.error.HTTPError) -> str:
    # Server bodies may echo submitted text or authentication headers.
    return "服务端拒绝请求；响应正文已省略"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise VoiceStudioError("服务地址发生重定向；请直接配置最终服务地址")


def open_request(request: urllib.request.Request, timeout: float) -> tuple[bytes, str]:
    require_credential_transport(request.full_url, bool(request.get_header("Authorization")))
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        raise VoiceStudioError(f"VoiceStudio 返回 HTTP {error.code}：{error_detail(error)}") from error
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise VoiceStudioError(f"无法连接 VoiceStudio：{safe_diagnostic(reason)}") from error
    except TimeoutError as error:
        raise VoiceStudioError("VoiceStudio 请求超时；首次加载模型时请先在应用中确认下载已完成") from error


def get_json(url: str, timeout: float) -> dict:
    body, _ = open_request(
        urllib.request.Request(url, headers=request_headers(), method="GET"), timeout
    )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VoiceStudioError(f"VoiceStudio 返回了无效 JSON：{url}") from error
    if not isinstance(payload, dict):
        raise VoiceStudioError(f"VoiceStudio 返回格式不正确：{url}")
    return payload


def check_service(base_url: str, timeout: float) -> dict:
    """Check liveness without triggering VoiceStudio's expensive engine scan.

    VoiceStudio 0.5.0 builds ``/v1/audio/voices`` by probing every registered
    engine. On a CPU-only Windows machine that endpoint can take minutes even
    though the backend is already healthy. A readiness check should therefore
    use only the dedicated, lightweight ``/health`` endpoint.
    """

    server_root, _ = normalize_roots(base_url)
    return get_json(f"{server_root}/health", timeout)


def speech_payload(args: argparse.Namespace, text: str) -> dict:
    payload: dict[str, object] = {
        "model": args.model,
        "input": text,
        "voice": args.voice,
        "response_format": "wav",
        "speed": args.speed,
        "language": args.language,
    }
    if args.instruct:
        payload["instruct"] = args.instruct
    if args.seed is not None:
        payload["seed"] = args.seed
    return payload


def synthesize(args: argparse.Namespace) -> tuple[Path, Path]:
    script = Path(args.script).resolve()
    if not script.is_file():
        raise ValueError(f"口播稿不存在：{script}")
    text = script.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("口播稿为空")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(
            f"口播稿有 {len(text)} 个字符，超过 VoiceStudio 单次 {MAX_INPUT_CHARS} 字符限制；请先按场景拆分"
        )

    server_root, api_root = normalize_roots(args.base_url)
    request = urllib.request.Request(
        f"{api_root}/audio/speech",
        data=json.dumps(speech_payload(args, text), ensure_ascii=False).encode("utf-8"),
        headers=request_headers(json_body=True),
        method="POST",
    )
    audio_bytes, content_type = open_request(request, args.timeout)
    if not audio_bytes:
        raise VoiceStudioError("VoiceStudio 没有返回音频")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "narration.wav"
    audio_path.write_bytes(audio_bytes)
    metadata_path = out_dir / "voicestudio.json"
    metadata_path.write_text(
        json.dumps(
            {
                "version": 1,
                "provider": "voicestudio",
                "base_url": server_root,
                "model": args.model,
                "voice": args.voice,
                "language": args.language,
                "speed": args.speed,
                "instruct": args.instruct or None,
                "seed": args.seed,
                "response_format": "wav",
                "content_type": content_type,
                "bytes": len(audio_bytes),
                "alignment_required": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return audio_path, metadata_path


def run(args: argparse.Namespace) -> int:
    try:
        if args.command == "check":
            health = check_service(args.base_url, args.timeout)
            print("SERVICE=voicestudio")
            print("STATUS=ready")
            print("HEALTH=responded")
            return 0

        audio_path, metadata_path = synthesize(args)
        print("PROVIDER=voicestudio")
        print(f"AUDIO={audio_path}")
        print(f"METADATA={metadata_path}")
        print("ALIGNMENT_REQUIRED=true")
        return 0
    except ValueError as error:
        print(f"[err] {safe_diagnostic(error)}", file=sys.stderr)
        return 2
    except (OSError, VoiceStudioError) as error:
        print(f"[not-ready] {safe_diagnostic(error)}", file=sys.stderr)
        return 4


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="连接本地 VoiceStudio 生成中文配音")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-url",
        default=os.environ.get("VOICESTUDIO_BASE_URL", DEFAULT_BASE_URL),
    )
    common.add_argument("--timeout", type=float, default=1200.0)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("check", parents=[common], help="检查 VoiceStudio 服务状态")
    synth = commands.add_parser("synthesize", parents=[common], help="生成 narration.wav")
    synth.add_argument("--script", required=True)
    synth.add_argument("--out-dir", required=True)
    synth.add_argument("--model", default=os.environ.get("VOICESTUDIO_MODEL", DEFAULT_MODEL))
    synth.add_argument("--voice", default=os.environ.get("VOICESTUDIO_VOICE_ID", DEFAULT_VOICE))
    synth.add_argument("--language", default="zh")
    synth.add_argument("--speed", type=float, default=1.0)
    synth.add_argument("--instruct", default="")
    synth.add_argument("--seed", type=int)
    return root


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
