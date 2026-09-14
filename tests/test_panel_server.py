from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
import sys
from pathlib import Path
from unittest import mock
from urllib import error, request


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "panel_server.py"
SCRIPTS_DIR = MODULE_PATH.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location("panel_server", MODULE_PATH)
panel_server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(panel_server)


class PanelServerTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        (root / "audio" / "edge").mkdir(parents=True)
        (root / "boards").mkdir()
        (root / "annotations").mkdir()
        project = {
            "version": 3,
            "title": "自动加载",
            "topic": "测试",
            "style": "暖米黄素描白板",
            "style_id": "warm-pencil",
            "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 1000}],
        }
        storyboard = {"scenes": [{"id": "scene-01", "title": "第一幕", "start_ms": 0, "end_ms": 1000}]}
        annotation = {
            "sceneId": "scene-01",
            "canvas": {"width": 100, "height": 100},
            "sceneDurationMs": 1000,
            "elements": [],
        }
        (root / "project.json").write_text(json.dumps(project), encoding="utf-8")
        (root / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")
        (root / "state.json").write_text(json.dumps({"boards": {"scene-01": {
            "image": "boards/scene-01.png", "annotation": "annotations/scene-01.annotation.json"
        }}}), encoding="utf-8")
        (root / "boards" / "scene-01.png").write_bytes(b"png-placeholder")
        (root / "annotations" / "scene-01.annotation.json").write_text(json.dumps(annotation), encoding="utf-8")

    def test_bootstrap_loads_project_without_folder_picker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_project(root)
            (root / "visual-plan.json").write_text(
                json.dumps({"version": 1, "shots": [{"scene_id": "scene-01"}]}),
                encoding="utf-8",
            )
            payload = panel_server.build_bootstrap(root)
            self.assertEqual(payload["project"]["title"], "自动加载")
            self.assertEqual(payload["scenes"][0]["imageUrl"], "/project/boards/scene-01.png")
            self.assertEqual(payload["scenes"][0]["annotation"]["sceneId"], "scene-01")
            self.assertTrue(payload["boardStatus"]["ready"])
            self.assertTrue(payload["projectSha256"])
            self.assertNotIn("sessionToken", payload)
            self.assertEqual(payload["visualPlan"]["shots"][0]["scene_id"], "scene-01")

    def test_nonfinite_json_never_reaches_the_bootstrap_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_project(root)
            annotation_path = root / "annotations" / "scene-01.annotation.json"
            annotation_path.write_text(
                '{"sceneId":"scene-01","sceneDurationMs":1e999,"elements":[]}',
                encoding="utf-8",
            )

            payload = panel_server.build_bootstrap(root)

            self.assertEqual(payload["scenes"][0]["annotation"]["sceneDurationMs"], 1000)
            json.dumps(payload, ensure_ascii=False, allow_nan=False)
            with self.assertRaisesRegex(ValueError, "非有限数值"):
                panel_server.strict_json_loads('{"duration":1e999}')
            with self.assertRaisesRegex(ValueError, "非有限数值"):
                panel_server.strict_json_loads('{"duration":Infinity}')

    def test_bootstrap_falls_back_to_edge_audio_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_project(root)
            (root / "audio" / "edge" / "words.json").write_text(
                json.dumps({"words": [{"text": "测试", "start_ms": 0, "end_ms": 500}]}),
                encoding="utf-8",
            )
            (root / "audio" / "edge" / "captions.srt").write_text(
                "1\n00:00:00,000 --> 00:00:00,500\n测试\n",
                encoding="utf-8",
            )
            (root / "audio" / "edge" / "narration.mp3").write_bytes(b"audio")
            payload = panel_server.build_bootstrap(root)
            self.assertEqual(payload["words"]["words"][0]["text"], "测试")
            self.assertIn("测试", payload["captions"])
            self.assertEqual(payload["audioUrl"], "/project/audio/edge/narration.mp3")

    def test_bootstrap_does_not_read_board_files_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp)
            root = container / "project"
            root.mkdir()
            self.make_project(root)
            outside_annotation = container / "outside.annotation.json"
            outside_annotation.write_text(
                json.dumps({"sceneId": "outside-secret", "elements": [{"label": "outside"}]}),
                encoding="utf-8",
            )
            outside_image = container / "outside.png"
            outside_image.write_bytes(b"outside-image")
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            state["boards"]["scene-01"] = {
                "image": "../outside.png",
                "annotation": "../outside.annotation.json",
            }
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")

            payload = panel_server.build_bootstrap(root)

            scene = payload["scenes"][0]
            self.assertEqual(scene["imageUrl"], "/project/boards/scene-01.png")
            self.assertEqual(scene["annotation"]["sceneId"], "scene-01")
            self.assertEqual(scene["annotationRelPath"], "annotations/scene-01.annotation.json")
            self.assertNotEqual(scene["annotationSha256"], panel_server.sha256(outside_annotation))
            self.assertEqual(payload["boardStatus"]["recoveredImages"], ["scene-01"])

    def test_bootstrap_reports_missing_board_instead_of_silent_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_project(root)
            (root / "boards" / "scene-01.png").unlink()

            payload = panel_server.build_bootstrap(root)

            self.assertIsNone(payload["scenes"][0]["imageUrl"])
            self.assertFalse(payload["boardStatus"]["ready"])
            self.assertEqual(payload["boardStatus"]["missingImages"], ["scene-01"])

    def test_post_endpoints_require_token_and_dispatch_safe_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            (assets / "panel.html").write_text("ok", encoding="utf-8")
            self.make_project(root)
            token = "test-token"
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0), panel_server.make_handler(assets, root, token)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with self.assertRaises(error.HTTPError) as denied_bootstrap:
                    request.urlopen(base + "/api/bootstrap", timeout=5)
                self.assertEqual(denied_bootstrap.exception.code, 403)
                bootstrap_request = request.Request(
                    base + "/api/bootstrap",
                    headers={"X-Panel-Token": token},
                )
                bootstrap = json.loads(request.urlopen(bootstrap_request, timeout=5).read().decode("utf-8"))
                self.assertNotIn("sessionToken", bootstrap)

                session_request = request.Request(
                    base + "/api/session",
                    data=b"",
                    headers={"X-Panel-Token": token},
                    method="POST",
                )
                session_response = request.urlopen(session_request, timeout=5)
                cookie = session_response.headers.get("Set-Cookie", "").split(";", 1)[0]
                cookie_bootstrap = request.Request(
                    base + "/api/bootstrap",
                    headers={"Cookie": cookie},
                )
                refreshed = json.loads(
                    request.urlopen(cookie_bootstrap, timeout=5).read().decode("utf-8")
                )
                self.assertEqual(refreshed["project"]["title"], "自动加载")

                unauthorized = request.Request(
                    base + "/api/apply-changes",
                    data=json.dumps({"changeSet": {}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(error.HTTPError) as denied:
                    request.urlopen(unauthorized, timeout=5)
                self.assertEqual(denied.exception.code, 403)

                import workflow
                with mock.patch.object(workflow, "apply_panel") as apply_mock:
                    apply_mock.side_effect = [
                        {"dry_run": True, "status": "验证通过，可安全应用"},
                        {"success": True, "applied_files": ["project.json"]},
                    ]
                    authorized = request.Request(
                        base + "/api/apply-changes",
                        data=json.dumps({"changeSet": {"version": 1, "files": [{}]}}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Cookie": cookie},
                        method="POST",
                    )
                    response = json.loads(request.urlopen(authorized, timeout=5).read().decode("utf-8"))
                    self.assertTrue(response["ok"])
                    self.assertEqual(apply_mock.call_count, 2)

                with mock.patch.object(workflow, "render_panel_scene_preview") as preview_mock:
                    preview_mock.return_value = {
                        "scene_id": "scene-01",
                        "path": "previews/panel/scene-01.mp4",
                        "sha256": "abc",
                        "duration_ms": 1000,
                        "cache_hit": True,
                    }
                    preview_request = request.Request(
                        base + "/api/real-preview",
                        data=json.dumps({"sceneId": "scene-01"}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "X-Panel-Token": token},
                        method="POST",
                    )
                    response = json.loads(request.urlopen(preview_request, timeout=5).read().decode("utf-8"))
                    self.assertEqual(response["videoUrl"], "/project/previews/panel/scene-01.mp4")
                    preview_mock.assert_called_once_with(
                        root,
                        "scene-01",
                        fps=30,
                        cap_long_edge=960,
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_non_loopback_bind_requires_explicit_lan_consent(self) -> None:
        self.assertTrue(panel_server.is_loopback_host("127.0.0.1"))
        self.assertTrue(panel_server.is_loopback_host("::1"))
        self.assertFalse(panel_server.is_loopback_host("0.0.0.0"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            (assets / "panel.html").write_text("ok", encoding="utf-8")
            self.make_project(root)
            with self.assertRaisesRegex(ValueError, "allow-lan"):
                panel_server.serve(root, assets, "0.0.0.0", 0, False)

    def test_style_reference_is_served_from_single_repository_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            assets = repo / "renderer" / "assets"
            style_ref = repo / "assets" / "style-references" / "warm-pencil" / "reference.png"
            assets.mkdir(parents=True)
            style_ref.parent.mkdir(parents=True)
            (assets / "panel.html").write_text("ok", encoding="utf-8")
            style_ref.write_bytes(b"first-party-reference")
            project = repo / "project"
            project.mkdir()
            self.make_project(project)
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0), panel_server.make_handler(assets, project, "token")
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/style-reference/warm-pencil/reference.png"
                response = request.urlopen(url, timeout=5)
                self.assertEqual(response.read(), b"first-party-reference")
                self.assertEqual(response.headers.get_content_type(), "image/png")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_static_server_never_exposes_private_presenter_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            (assets / "presenter").mkdir(parents=True)
            (assets / "panel.html").write_text("panel", encoding="utf-8")
            (assets / "style-registry-data.js").write_text("registry", encoding="utf-8")
            (assets / "presenter" / "private-pack.txt").write_text("private", encoding="utf-8")
            (assets / "presenter.json").write_text('{"private":true}', encoding="utf-8")
            self.make_project(root)
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0), panel_server.make_handler(assets, root, "token")
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                self.assertEqual(request.urlopen(base + "/panel.html", timeout=5).read(), b"panel")
                self.assertEqual(
                    request.urlopen(base + "/style-registry-data.js", timeout=5).read(),
                    b"registry",
                )
                for private_path in ("/presenter/private-pack.txt", "/presenter.json"):
                    with self.assertRaises(error.HTTPError) as denied:
                        request.urlopen(base + private_path, timeout=5)
                    self.assertEqual(denied.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_project_media_supports_byte_ranges_for_scene_seeking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            (assets / "panel.html").write_text("ok", encoding="utf-8")
            self.make_project(root)
            media = root / "audio" / "edge" / "narration.mp3"
            media.write_bytes(b"0123456789")
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0), panel_server.make_handler(assets, root, "token")
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with self.assertRaises(error.HTTPError) as denied:
                    request.urlopen(base + "/project/audio/edge/narration.mp3", timeout=5)
                self.assertEqual(denied.exception.code, 403)
                session_request = request.Request(
                    base + "/api/session",
                    data=b"",
                    headers={"X-Panel-Token": "token"},
                    method="POST",
                )
                session_response = request.urlopen(session_request, timeout=5)
                cookie = session_response.headers.get("Set-Cookie", "").split(";", 1)[0]
                ranged = request.Request(
                    base + "/project/audio/edge/narration.mp3",
                    headers={"Range": "bytes=2-5", "Cookie": cookie},
                )
                response = request.urlopen(ranged, timeout=5)
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"2345")
                self.assertEqual(response.headers.get("Accept-Ranges"), "bytes")
                self.assertEqual(response.headers.get("Content-Range"), "bytes 2-5/10")
                self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
                self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
                self.assertEqual(response.headers.get("Cross-Origin-Resource-Policy"), "same-origin")
                self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_lan_project_media_requires_session_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            (assets / "panel.html").write_text("ok", encoding="utf-8")
            self.make_project(root)
            media = root / "audio" / "edge" / "narration.mp3"
            media.write_bytes(b"private-project-audio")
            token = "lan-session-token"
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0),
                panel_server.make_handler(
                    assets,
                    root,
                    token,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            media_url = base + "/project/audio/edge/narration.mp3"
            try:
                with self.assertRaises(error.HTTPError) as denied:
                    request.urlopen(media_url, timeout=5)
                self.assertEqual(denied.exception.code, 403)

                session_request = request.Request(
                    base + "/api/session",
                    data=b"",
                    headers={"X-Panel-Token": token},
                    method="POST",
                )
                session_response = request.urlopen(session_request, timeout=5)
                cookie = session_response.headers.get("Set-Cookie", "")
                self.assertIn("SketchNarratorPanel=lan-session-token", cookie)
                self.assertIn("HttpOnly", cookie)
                self.assertIn("SameSite=Strict", cookie)

                authorized = request.Request(
                    media_url,
                    headers={"Cookie": cookie.split(";", 1)[0]},
                )
                self.assertEqual(request.urlopen(authorized, timeout=5).read(), b"private-project-audio")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_cookie_auth_rejects_cross_origin_browser_contexts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            (assets / "panel.html").write_text("ok", encoding="utf-8")
            self.make_project(root)
            media = root / "audio" / "edge" / "narration.mp3"
            media.write_bytes(b"private-project-audio")
            token = "origin-bound-session-token"
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0), panel_server.make_handler(assets, root, token)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            media_url = base + "/project/audio/edge/narration.mp3"
            cookie = f"SketchNarratorPanel={token}"
            try:
                wrong_origin = request.Request(
                    media_url,
                    headers={"Cookie": cookie, "Origin": "http://127.0.0.1:9"},
                )
                with self.assertRaises(error.HTTPError) as denied_origin:
                    request.urlopen(wrong_origin, timeout=5)
                self.assertEqual(denied_origin.exception.code, 403)

                cross_port_context = request.Request(
                    media_url,
                    headers={"Cookie": cookie, "Sec-Fetch-Site": "same-site"},
                )
                with self.assertRaises(error.HTTPError) as denied_context:
                    request.urlopen(cross_port_context, timeout=5)
                self.assertEqual(denied_context.exception.code, 403)

                same_origin = request.Request(
                    media_url,
                    headers={
                        "Cookie": cookie,
                        "Origin": base,
                        "Referer": base + "/panel.html",
                        "Sec-Fetch-Site": "same-origin",
                    },
                )
                self.assertEqual(request.urlopen(same_origin, timeout=5).read(), b"private-project-audio")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_panel_waits_for_scene_audio_seek_before_playing(self) -> None:
        panel = (Path(__file__).parents[1] / "renderer" / "assets" / "panel.html").read_text(encoding="utf-8")
        self.assertIn("async function seekSceneAudio", panel)
        self.assertIn("waitForMediaEvent(media, 'loadedmetadata')", panel)
        self.assertIn("waitForMediaEvent(media, 'seeked')", panel)
        self.assertIn("Number(sc.startMs || 0) + Math.max(0, Number(localMs || 0))", panel)
        self.assertIn("await Promise.all(seeks)", panel)

    def test_panel_uses_fragment_token_and_does_not_invent_foreground_confidence(self) -> None:
        panel = (Path(__file__).parents[1] / "renderer" / "assets" / "panel.html").read_text(encoding="utf-8")
        self.assertIn("takeSessionTokenFromLocation()", panel)
        self.assertIn("clearSessionTokenFromLocation()", panel)
        self.assertIn("'X-Panel-Token': App.sessionToken", panel)
        self.assertIn("fetch('/api/session'", panel)
        self.assertIn("mediaSessionReady", panel)
        self.assertIn("credentials: 'same-origin'", panel)
        self.assertIn("successful response guarantees refresh-safe authentication", panel)
        self.assertIn("App.sessionToken = null", panel)
        self.assertIn("function validationSaveGate(validation, changeSet)", panel)
        self.assertIn("return {ok: true, blockingChecks: [], hardBlockingChecks: []", panel)
        self.assertNotIn("changedAnnotationSceneIds.has(sceneId)", panel)
        self.assertIn("function updateSaveProjectButton(hasChanges, gate)", panel)
        self.assertIn("text = '💾 无修改'", panel)
        self.assertNotIn("serverProject && App.mediaSessionReady && hasChanges && validation.ok", panel)
        self.assertNotIn("serverProject && App.sessionToken && hasChanges && validation.ok", panel)
        self.assertNotIn("if (!serverProject || !App.sessionToken)", panel)
        self.assertNotIn("if (!sc || !App.sessionToken)", panel)
        self.assertIn("if (!serverProject || !App.mediaSessionReady)", panel)
        self.assertIn("if (!sc || !App.mediaSessionReady)", panel)
        self.assertIn("当前页面修改尚未丢失", panel)
        self.assertIn("App.visualPlanData = payload.visualPlan || null", panel)
        self.assertIn("renderVisualPlanSummary(sid)", panel)
        self.assertNotIn("confidence: 0.95", panel)
        self.assertIn("工作台不能凭 annotation 矩形伪造前景像素置信度", panel)

    def test_panel_renders_project_labels_as_text_or_escaped_markup(self) -> None:
        panel = (Path(__file__).parents[1] / "renderer" / "assets" / "panel.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("sel.replaceChildren(...options)", panel)
        self.assertIn("option.textContent = `${i + 1}. ${s.id} - ${s.title}`", panel)
        self.assertIn("$('anim_target_elem').replaceChildren(...targetOptions)", panel)
        self.assertIn("option.textContent = `${e.label || '未命名'} (${e.id || '无ID'})`", panel)
        self.assertIn("<b>${escapeHtml(c.title)}</b>：${escapeHtml(display.msg)}", panel)
        self.assertIn("${escapeHtml(c.title)} — <span class=\"muted\">${escapeHtml(display.msg)}", panel)
        self.assertNotIn("<option value=\"${i}\">${i + 1}. ${s.id} - ${s.title}</option>", panel)
        self.assertNotIn("<option value=\"${e.id}\">${e.label} (${e.id})</option>", panel)

    def test_rendering_animation_tab_does_not_create_a_scene(self) -> None:
        panel = (Path(__file__).parents[1] / "renderer" / "assets" / "panel.html").read_text(encoding="utf-8")
        render_start = panel.index("function renderAnimationTab()")
        apply_start = panel.index("function applyAnimationChanges()")
        render_body = panel[render_start:apply_start]
        self.assertNotIn("getOrCreateAnimScene(sid)", render_body)


if __name__ == "__main__":
    unittest.main()
