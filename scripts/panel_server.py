#!/usr/bin/env python3
"""Serve the composition panel and one local project over localhost."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import mimetypes
import re
import secrets
import socket
import sys
import tempfile
import threading
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


PUBLIC_PANEL_ASSETS = frozenset({"panel.html", "style-registry-data.js"})
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Avoid Windows SO_REUSEADDR silently sharing an occupied panel port."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"JSON 包含非有限数值：{value}")


def validate_finite_json(value: Any, location: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"JSON 包含非有限数值：{location}")
    if isinstance(value, dict):
        for key, child in value.items():
            validate_finite_json(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_finite_json(child, f"{location}[{index}]")


def strict_json_loads(text: str) -> Any:
    value = json.loads(text, parse_constant=_reject_nonfinite_constant)
    validate_finite_json(value)
    return value


def read_json(path: Path | None, fallback: Any) -> Any:
    if path is None:
        return fallback
    try:
        return strict_json_loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return fallback


def sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_path(project_root: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    try:
        candidate = (project_root / str(relative)).resolve()
        candidate.relative_to(project_root.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def project_file(project_root: Path, relative: str | None) -> Path | None:
    candidate = project_path(project_root, relative)
    if candidate is None:
        return None
    return candidate if candidate.is_file() else None


def first_project_file(project_root: Path, candidates: list[str | None]) -> Path | None:
    for relative in candidates:
        candidate = project_file(project_root, relative)
        if candidate:
            return candidate
    return None


def build_bootstrap(project_root: Path) -> dict[str, Any]:
    project = read_json(project_root / "project.json", None)
    if not isinstance(project, dict):
        raise ValueError(f"项目根目录缺少有效 project.json：{project_root}")
    state = read_json(project_root / "state.json", {})
    storyboard = read_json(project_root / "storyboard.json", {"scenes": []})
    visual_plan = read_json(project_root / "visual-plan.json", None)
    animation = read_json(
        project_root / "animation-plan.json",
        {"version": 3, "planVersion": "3.3", "scenes": []},
    )
    artifacts = state.get("artifacts", {}) if isinstance(state, dict) else {}
    words_path = first_project_file(project_root, [
        (artifacts.get("words") or {}).get("path") if isinstance(artifacts.get("words"), dict) else None,
        "audio/words.json",
        "audio/edge/words.json",
    ])
    captions_path = first_project_file(project_root, [
        (artifacts.get("captions") or {}).get("path") if isinstance(artifacts.get("captions"), dict) else None,
        "audio/captions.srt",
        "audio/edge/captions.srt",
    ])
    audio_path = first_project_file(project_root, [
        (artifacts.get("audio") or {}).get("path") if isinstance(artifacts.get("audio"), dict) else None,
        "audio/narration.mp3",
        "audio/narration.wav",
        "audio/edge/narration.mp3",
        "audio/edge/narration.wav",
    ])
    words = read_json(words_path, None) if words_path else None
    captions = captions_path.read_text(encoding="utf-8-sig") if captions_path else ""
    board_records = state.get("boards", {}) if isinstance(state, dict) else {}
    scenes: list[dict[str, Any]] = []
    missing_images: list[str] = []
    missing_annotations: list[str] = []
    recovered_images: list[str] = []
    for scene_index, scene in enumerate(storyboard.get("scenes", []), start=1):
        scene_id = str(scene.get("id", ""))
        record = board_records.get(scene_id, {}) if isinstance(board_records, dict) else {}
        image_rel = str(record.get("image", ""))
        annotation_rel = str(
            record.get("annotation", f"annotations/{scene_id}.annotation.json")
        )
        image_path = project_file(project_root, image_rel)
        if image_path is None:
            image_path = first_project_file(
                project_root,
                [f"boards/{scene_id}{extension}" for extension in (".png", ".jpg", ".jpeg", ".webp")],
            )
            if image_path is not None:
                recovered_images.append(scene_id)
        image_url_rel = (
            image_path.relative_to(project_root.resolve()).as_posix()
            if image_path else None
        )
        annotation_candidate = project_path(project_root, annotation_rel)
        if annotation_candidate and annotation_candidate.is_file():
            annotation_url_rel = annotation_candidate.relative_to(project_root.resolve()).as_posix()
            annotation_path = annotation_candidate
        else:
            safe_scene_id = re.sub(r"[^A-Za-z0-9._-]+", "-", scene_id).strip(".-")
            if not safe_scene_id:
                safe_scene_id = f"scene-{scene_index:02d}"
            annotation_url_rel = f"annotations/{safe_scene_id}.annotation.json"
            annotation_path = project_file(project_root, annotation_url_rel)
        if image_path is None:
            missing_images.append(scene_id)
        if annotation_path is None:
            missing_annotations.append(scene_id)
        annotation = read_json(
            annotation_path,
            {
                "sceneId": scene_id,
                "canvas": {"width": 1920, "height": 1080},
                "sceneDurationMs": int(scene.get("end_ms", 6000)) - int(scene.get("start_ms", 0)),
                "elements": [],
            },
        )
        scenes.append(
            {
                "id": scene_id,
                "title": scene.get("title", scene_id),
                "narration": scene.get("narration", ""),
                "startMs": int(scene.get("start_ms", 0)),
                "endMs": int(scene.get("end_ms", 6000)),
                "imageUrl": f"/project/{image_url_rel}" if image_url_rel else None,
                "imageReady": image_path is not None,
                "boardStale": bool(record.get("stale")),
                "annotation": annotation,
                "annotationRelPath": annotation_url_rel,
                "annotationSha256": sha256(annotation_path),
            }
        )
    animation_path = project_root / "animation-plan.json"
    return {
        "project": project,
        "projectSha256": sha256(project_root / "project.json"),
        "storyboardSha256": sha256(project_root / "storyboard.json"),
        "state": state,
        "storyboard": storyboard,
        "visualPlan": visual_plan,
        "animationPlan": animation,
        "animationPlanSha256": sha256(animation_path),
        "words": words,
        "captions": captions,
        "audioUrl": (
            "/project/" + str(audio_path.relative_to(project_root)).replace("\\", "/")
            if audio_path else None
        ),
        "scenes": scenes,
        "boardStatus": {
            "ready": not missing_images and not missing_annotations,
            "missingImages": missing_images,
            "missingAnnotations": missing_annotations,
            "recoveredImages": recovered_images,
        },
    }


def make_handler(
    assets_root: Path,
    project_root: Path,
    session_token: str,
):
    style_references_root = assets_root.parents[1] / "assets" / "style-references"

    project_write_lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "SketchNarratorPanel"
        sys_version = ""

        def log_message(self, fmt: str, *args: object) -> None:
            print("PANEL_HTTP=" + (fmt % args))

        def send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
                "media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )

        def send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: int = 200,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_security_headers()
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, target: Path, content_type: str) -> None:
            """Serve one local file with byte-range support for media seeking."""
            size = target.stat().st_size
            range_header = self.headers.get("Range", "").strip()
            start, end, status = 0, max(0, size - 1), 200
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                if not match or size <= 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_security_headers()
                    self.end_headers()
                    return
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = int(right) if right else size - 1
                elif right:
                    suffix = min(size, int(right))
                    start, end = size - suffix, size - 1
                if start < 0 or start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_security_headers()
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206

            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_security_headers()
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with target.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def send_json(
            self,
            value: Any,
            status: int = 200,
            headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_bytes(body, "application/json; charset=utf-8", status, headers)

        def read_request_json(self, max_bytes: int = 5 * 1024 * 1024) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length 无效") from exc
            if length <= 0 or length > max_bytes:
                raise ValueError("请求正文为空或超过 5MB 限制")
            value = strict_json_loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("请求正文必须是 JSON 对象")
            return value

        def same_origin(self) -> bool:
            fetch_site = self.headers.get("Sec-Fetch-Site", "").strip().lower()
            if fetch_site not in {"", "none", "same-origin"}:
                return False
            origin = self.headers.get("Origin")
            host = self.headers.get("Host")
            if not host or (origin and origin != f"http://{host}"):
                return False
            referer = self.headers.get("Referer")
            if referer:
                parsed = urlparse(referer)
                if parsed.scheme != "http" or parsed.netloc != host:
                    return False
            return True

        def authorized_header(self) -> bool:
            return self.same_origin() and secrets.compare_digest(
                self.headers.get("X-Panel-Token", ""), session_token
            )

        def authorized_cookie(self) -> bool:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except ValueError:
                return False
            value = cookie.get("SketchNarratorPanel")
            return bool(
                self.same_origin()
                and value
                and secrets.compare_digest(value.value, session_token)
            )

        def authorized(self) -> bool:
            return self.authorized_header() or self.authorized_cookie()

        def authorized_project_media(self) -> bool:
            return self.authorized()

        def safe_file(self, root: Path, relative: str) -> Path | None:
            try:
                candidate = (root / unquote(relative)).resolve()
                candidate.relative_to(root.resolve())
            except (OSError, RuntimeError, ValueError):
                return None
            return candidate if candidate.is_file() else None

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/bootstrap":
                if not self.authorized():
                    self.send_json({"error": "工作台会话令牌无效"}, 403)
                    return
                try:
                    self.send_json(build_bootstrap(project_root))
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 500)
                return
            if parsed.path == "/api/health":
                self.send_json({"ok": True})
                return
            if parsed.path.startswith("/project/"):
                if not self.authorized_project_media():
                    self.send_json({"error": "工作台媒体会话无效"}, 403)
                    return
                target = self.safe_file(project_root, parsed.path[len("/project/"):])
            elif parsed.path.startswith("/style-reference/"):
                target = self.safe_file(
                    style_references_root,
                    parsed.path[len("/style-reference/"):],
                )
            else:
                relative = "panel.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
                relative = unquote(relative)
                target = (
                    self.safe_file(assets_root, relative)
                    if relative in PUBLIC_PANEL_ASSETS
                    else None
                )
            if target is None:
                self.send_bytes(b"not found", "text/plain; charset=utf-8", 404)
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_file(target, content_type)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/session":
                if not self.authorized_header():
                    self.send_json({"error": "工作台会话令牌无效"}, 403)
                    return
                self.send_json(
                    {"ok": True},
                    headers={
                        "Set-Cookie": (
                            f"SketchNarratorPanel={session_token}; Path=/; "
                            "HttpOnly; SameSite=Strict"
                        )
                    },
                )
                return
            if parsed.path not in {"/api/apply-changes", "/api/real-preview", "/api/prepare-plan"}:
                self.send_json({"error": "not found"}, 404)
                return
            if not self.authorized():
                self.send_json({"error": "工作台会话令牌无效"}, 403)
                return
            try:
                payload = self.read_request_json()
                from workflow import apply_panel, render_panel_scene_preview, rebuild_plan

                if parsed.path == "/api/prepare-plan":
                    with project_write_lock:
                        rebuild_plan(project_root)
                    self.send_json({"ok": True, "status": "needs-approval"})
                    return

                if parsed.path == "/api/apply-changes":
                    change_set = payload.get("changeSet")
                    if not isinstance(change_set, dict):
                        raise ValueError("缺少有效的 changeSet")
                    with tempfile.TemporaryDirectory(prefix="panel_changes_") as temp_dir:
                        changes_path = Path(temp_dir) / "panel-change-set.json"
                        changes_path.write_text(
                            json.dumps(change_set, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                            encoding="utf-8",
                        )
                        with project_write_lock:
                            dry_run = apply_panel(project_root, changes_path, dry_run=True)
                            applied = apply_panel(project_root, changes_path, dry_run=False)
                    self.send_json({"ok": True, "dryRun": dry_run, "applied": applied})
                    return

                scene_id = str(payload.get("sceneId", "")).strip()
                if not scene_id:
                    raise ValueError("缺少 sceneId")
                preview = render_panel_scene_preview(
                    project_root,
                    scene_id,
                    fps=int(payload.get("fps", 30)),
                    cap_long_edge=int(payload.get("capLongEdge", 960)),
                )
                self.send_json({
                    "ok": True,
                    "preview": preview,
                    "videoUrl": "/project/" + preview["path"],
                })
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)

    return Handler


def is_loopback_host(host: str) -> bool:
    """Return whether a bind host is limited to this machine."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve(
    project_root: Path,
    assets_root: Path,
    host: str,
    port: int,
    open_browser: bool,
    allow_lan: bool = False,
) -> None:
    if not is_loopback_host(host) and not allow_lan:
        raise ValueError("工作台默认只允许绑定本机回环地址；如确需局域网访问，请显式传入 --allow-lan")
    session_token = secrets.token_urlsafe(32)
    server = ExclusiveThreadingHTTPServer(
        (host, port),
        make_handler(
            assets_root,
            project_root,
            session_token,
        ),
    )
    actual_port = int(server.server_address[1])
    url = f"http://{host}:{actual_port}/panel.html?server-project=1#token={session_token}"
    print(f"PANEL_URL={url}", flush=True)
    print(f"PANEL_PROJECT={project_root}", flush=True)
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 SketchNarrator 本地编排工作台")
    parser.add_argument("--project", required=True)
    parser.add_argument("--assets", default=str(Path(__file__).parents[1] / "renderer" / "assets"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="默认自动选择可用端口")
    parser.add_argument("--allow-lan", action="store_true", help="明确允许绑定非回环地址")
    parser.add_argument("--no-open", action="store_true", help="不自动打开系统浏览器")
    args = parser.parse_args()
    project_root = Path(args.project).expanduser().resolve()
    assets_root = Path(args.assets).expanduser().resolve()
    build_bootstrap(project_root)
    serve(project_root, assets_root, args.host, args.port, not args.no_open, args.allow_lan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
