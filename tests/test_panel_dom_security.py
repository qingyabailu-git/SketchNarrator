import importlib.util
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "panel_server.py"
SPEC = importlib.util.spec_from_file_location("panel_server_dom_security", MODULE_PATH)
panel_server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(panel_server)


def browser_binary() -> str | None:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome.exe"),
        shutil.which("msedge.exe"),
        Path("C:" + "/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:" + "/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


class PanelDomSecurityTests(unittest.TestCase):
    def test_refresh_uses_verified_cookie_and_can_save_without_fragment_token(self) -> None:
        browser = browser_binary()
        if browser is None:
            self.skipTest("Chromium-compatible headless browser is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            profile = Path(directory) / "browser-profile"
            assets = Path(directory) / "release" / "renderer" / "assets"
            assets.mkdir(parents=True)
            panel_source = (ROOT / "renderer" / "assets" / "panel.html").read_text(encoding="utf-8")
            panel_source = panel_source.replace(
                "</body>",
                """<script>
setTimeout(() => {
  const refreshPhase = sessionStorage.getItem('panel-refresh-cookie-test');
  if (!refreshPhase) {
    sessionStorage.setItem('panel-refresh-cookie-test', 'reloaded');
    document.body.dataset.initialSessionReady = String(App.mediaSessionReady);
    window.location.reload();
    return;
  }
  document.body.dataset.refreshSessionReady = String(App.mediaSessionReady);
  document.body.dataset.refreshSessionToken = String(App.sessionToken);
  App.projectData.renderer_profile = {
    ...(App.projectData.renderer_profile || {}),
    hand_mode: 'no-hand'
  };
  App.scenes[0].annData.elements[0].region.x = 11;
  checkDirtyAndDiff();
  document.body.dataset.refreshExistingAnimationError = String(
    runValidations().checks.some(check => !check.pass && check.saveScope === 'animation-plan')
  );
  const saveButton = document.getElementById('saveProjectBtn');
  document.body.dataset.refreshSaveEnabled = String(!saveButton.disabled);
  saveButton.click();
  setTimeout(() => {
    document.body.dataset.refreshSaveDone = '1';
    document.body.dataset.refreshSaveToast = document.getElementById('toast').textContent;
    App.animationPlanData.note = 'changed-invalid-plan';
    checkDirtyAndDiff();
    document.body.dataset.refreshInvalidAnimationButtonEnabled = String(!saveButton.disabled);
    saveButton.click();
    setTimeout(() => {
      document.body.dataset.refreshInvalidAnimationSaved = String(
        document.getElementById('toast').textContent.includes('已保存')
      );
    }, 150);
  }, 2200);
}, 1600);
</script>
</body>""",
            )
            (assets / "panel.html").write_text(panel_source, encoding="utf-8")
            shutil.copyfile(
                ROOT / "renderer" / "assets" / "style-registry-data.js",
                assets / "style-registry-data.js",
            )
            (project / "boards").mkdir(parents=True)
            (project / "annotations").mkdir()
            (project / "project.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "title": "刷新后保存测试",
                        "topic": "工作台 Cookie 会话",
                        "style": "暖米黄素描白板",
                        "style_id": "warm-pencil",
                        "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 2000}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (project / "storyboard.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "scenes": [
                            {
                                "id": "scene-01",
                                "title": "第一幕",
                                "narration": "测试",
                                "start_ms": 0,
                                "end_ms": 2000,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (project / "state.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "stage": "await-boards-approval",
                        "approvals": {
                            "script_style": {"approved": True, "at": "test"},
                            "script_voice": {"approved": True, "at": "test"},
                            "boards": {"approved": False, "at": None},
                        },
                        "artifacts": {},
                        "boards": {
                            "scene-01": {
                                "image": "boards/scene-01.png",
                                "annotation": "annotations/scene-01.annotation.json",
                            }
                        },
                        "render_cache": {},
                        "final_current": False,
                        "qa": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            Image.new("RGB", (100, 100), "white").save(project / "boards" / "scene-01.png")
            (project / "annotations" / "scene-01.annotation.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "sceneId": "scene-01",
                        "canvas": {"width": 100, "height": 100},
                        "sceneDurationMs": 2000,
                        "drawingPlan": {
                            "version": 2,
                            "mode": "layered",
                            "strokePlanner": "semantic-v2",
                            "colorSchedule": "object-progressive-v1",
                            "colorReserveRatio": 0.32,
                            "minimumColorMs": 500,
                        },
                        "elements": [
                            {
                                "id": "subject",
                                "label": "测试主体",
                                "role": "subject",
                                "sequence": 1,
                                "region": {"x": 10, "y": 10, "width": 50, "height": 50},
                                "reveal": {
                                    "mode": "direction",
                                    "direction": "left-to-right",
                                    "startMs": 0,
                                    "durationMs": 800,
                                    "protectedRegions": [],
                                },
                            },
                            {
                                "id": "overlapping-result",
                                "label": "重叠结果",
                                "role": "result",
                                "sequence": 2,
                                "region": {"x": 40, "y": 30, "width": 50, "height": 50},
                                "reveal": {
                                    "mode": "direction",
                                    "direction": "left-to-right",
                                    "startMs": 850,
                                    "durationMs": 650,
                                    "protectedRegions": [],
                                },
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (project / "animation-plan.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "planVersion": "3.3",
                        "scenes": [
                            {
                                "sceneId": "scene-01",
                                "sceneStartMs": 0,
                                "sceneEndMs": 2000,
                                "events": [
                                    {
                                        "effect": "unknown-existing-effect",
                                        "targetElementId": "subject",
                                        "startMs": 900,
                                        "endMs": 1200,
                                        "persistUntilMs": 1500,
                                    }
                                ],
                            }
                        ],
                        "transitions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            token = "refresh-cookie-token"
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0), panel_server.make_handler(assets, project, token)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = (
                f"http://127.0.0.1:{server.server_address[1]}/panel.html"
                f"?server-project=1#token={token}"
            )
            try:
                result = subprocess.run(
                    [
                        browser,
                        "--headless=new",
                        "--disable-gpu",
                        "--no-sandbox",
                        "--no-proxy-server",
                        f"--user-data-dir={profile}",
                        "--virtual-time-budget=10000",
                        "--dump-dom",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=40,
                    check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn('data-refresh-session-ready="true"', result.stdout)
            self.assertIn('data-refresh-session-token="null"', result.stdout)
            self.assertIn('data-refresh-existing-animation-error="true"', result.stdout)
            self.assertIn('data-refresh-save-enabled="true"', result.stdout)
            self.assertIn('data-refresh-save-done="1"', result.stdout)
            self.assertIn("预览和渲染需另行检查", result.stdout)
            self.assertIn('data-refresh-invalid-animation-button-enabled="true"', result.stdout)
            self.assertIn('data-refresh-invalid-animation-saved="true"', result.stdout)
            saved_project = json.loads((project / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_project["renderer_profile"]["hand_mode"], "no-hand")
            saved_annotation = json.loads(
                (project / "annotations" / "scene-01.annotation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_annotation["elements"][0]["region"]["x"], 11)
            self.assertTrue(any((project / "panel-backups").glob("*/project.json")))

    def test_project_markup_is_displayed_as_text_in_a_real_browser(self) -> None:
        browser = browser_binary()
        if browser is None:
            self.skipTest("Chromium-compatible headless browser is unavailable")

        marker = "xss-" + uuid4().hex
        payload = (
            f'<img id="{marker}" src="missing" '
            f'onerror="document.title=\'{marker}\';document.body.dataset.xss=\'1\'">'
        )
        attribute_payload = (
            f'0\"><img src="missing" onerror="document.title=\'{marker}\'">'
            '<div data-time="0'
        )
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            profile = Path(directory) / "browser-profile"
            assets = Path(directory) / "release" / "renderer" / "assets"
            assets.mkdir(parents=True)
            panel_source = (ROOT / "renderer" / "assets" / "panel.html").read_text(encoding="utf-8")
            panel_source = panel_source.replace(
                "</body>",
                """<script>
setTimeout(() => {
  document.querySelector('[data-tab="tab-timeline"]').click();
  const presenterMode = document.getElementById('ren_hand_mode');
  presenterMode.value = 'presenter';
  presenterMode.dispatchEvent(new Event('change', {bubbles: true}));
  document.body.dataset.presenterMode = presenterMode.value;
  document.body.dataset.timelineMax = document.getElementById('timelineSlider').max;
  document.body.dataset.timelineLabel = document.getElementById('timeLabel').textContent;
  document.body.dataset.timelineSecurityTest = 'clicked';
}, 1500);
</script>
</body>""",
            )
            (assets / "panel.html").write_text(panel_source, encoding="utf-8")
            shutil.copyfile(
                ROOT / "renderer" / "assets" / "style-registry-data.js",
                assets / "style-registry-data.js",
            )
            (project / "boards").mkdir(parents=True)
            (project / "annotations").mkdir()
            (project / "audio" / "edge").mkdir(parents=True)
            (project / "project.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "title": payload,
                        "topic": payload,
                        "style": "test",
                        "style_id": "warm-pencil",
                        "scenes": [{"id": "scene-01", "start_ms": 0, "end_ms": 1000}],
                    }
                ),
                encoding="utf-8",
            )
            (project / "storyboard.json").write_text(
                json.dumps(
                    {
                        "scenes": [
                            {
                                "id": "scene-01",
                                "title": payload,
                                "narration": payload,
                                "start_ms": 0,
                                "end_ms": 1000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (project / "state.json").write_text(
                json.dumps(
                    {
                        "boards": {
                            "scene-01": {
                                "image": "boards/scene-01.png",
                                "annotation": "annotations/scene-01.annotation.json",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            Image.new("RGB", (100, 100), "white").save(project / "boards" / "scene-01.png")
            (project / "annotations" / "scene-01.annotation.json").write_text(
                json.dumps(
                    {
                        "sceneId": "scene-01",
                        "canvas": {"width": 100, "height": 100},
                        "sceneDurationMs": "Infinity",
                        "elements": [
                            {
                                "id": payload,
                                "label": payload,
                                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                                "reveal": {"startMs": attribute_payload, "durationMs": 500},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            token = "browser-security-token"
            server = panel_server.ExclusiveThreadingHTTPServer(
                ("127.0.0.1", 0), panel_server.make_handler(assets, project, token)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = (
                f"http://127.0.0.1:{server.server_address[1]}/panel.html"
                f"?server-project=1#token={token}"
            )
            try:
                result = subprocess.run(
                    [
                        browser,
                        "--headless=new",
                        "--disable-gpu",
                        "--no-sandbox",
                        "--no-proxy-server",
                        f"--user-data-dir={profile}",
                        "--virtual-time-budget=5000",
                        "--dump-dom",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertNotIn(f"<title>{marker}</title>", result.stdout)
            self.assertNotIn('data-xss="1"', result.stdout)
            self.assertIn('data-timeline-security-test="clicked"', result.stdout)
            self.assertIn('data-presenter-mode="presenter"', result.stdout)
            self.assertIn('data-timeline-max="1000"', result.stdout)
            self.assertIn('data-timeline-label="0.00s / 1.00s"', result.stdout)
            self.assertNotIn('Infinitys', result.stdout)
            self.assertIn("&lt;img", result.stdout)


if __name__ == "__main__":
    unittest.main()
