import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "qa_final.py"
SPEC = importlib.util.spec_from_file_location("qa_final_stroke", MODULE_PATH)
qa_final = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(qa_final)


def scene_evidence(root):
    """Valid unrelated contracts let each test reach its intended art assertion."""
    import hashlib
    import numpy as np
    from PIL import Image, ImageDraw
    from pixel_contract import require_ownership, PIXEL_POLICY
    from phase_budget import compile_budget
    (root / "boards").mkdir(exist_ok=True)
    (root / "annotations").mkdir(exist_ok=True)
    image = Image.new("RGB", (100,100), "white")
    ImageDraw.Draw(image).rectangle((30,30,60,60),fill="black")
    image.save(root / "boards/scene-01.png")
    annotation = {"canvas":{"width":100,"height":100},"elements":[
        {"id":"subject","sequence":1,"region":{"x":20,"y":20,"width":60,"height":60},
         "reveal":{"startMs":0,"durationMs":1000}}]}
    (root / "annotations/scene-01.annotation.json").write_text(json.dumps(annotation),encoding="utf-8")
    scene = {"sceneId":"scene-01","events":[]}
    (root / "animation-plan.json").write_text(json.dumps({"scenes":[scene]}),encoding="utf-8")
    path = root / "renders/scene-01-profile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    _,_,ownership = require_ownership(np.asarray(image),annotation)
    profile.setdefault("pixel_ownership",PIXEL_POLICY)
    profile.setdefault("ownership_metrics",ownership)
    profile.setdefault("scene_plan_sha256",hashlib.sha256(json.dumps(scene,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest())
    budget=compile_budget(0,1000,1000,30)
    profile.setdefault("phase_budget_metrics",[dict(budget,elementId="subject",actualFrames=30,actualPhases=budget["phases"])])
    path.write_text(json.dumps(profile),encoding="utf-8")
    return {"boards":{"scene-01":{"image":"boards/scene-01.png","annotation":"annotations/scene-01.annotation.json"}}}


class StrokeArtQualityTests(unittest.TestCase):
    def test_semantic_profile_requires_real_scene_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "renders").mkdir()
            project = {
                "renderer_profile": {"stroke_planner": "semantic-v2", "color_fill": "local-brush"},
                "scenes": [{"id": "scene-01", "elements": [{"id": "subject"}]}],
            }
            profile = {
                "stroke_planner": "semantic-v2",
                "stroke_strategy": "fast-semantic-trace-v1",
                "color_fill": "local-brush",
                "color_schedule": "object-progressive-v1",
                "stroke_metrics": [{
                    "planner": "semantic-v2", "components": 2, "outline_strokes": 3,
                    "detail_strokes": 4, "texture_strokes": 5, "direction_bins": 4,
                    "identity_strokes": 3, "support_strokes": 1,
                    "total_strokes": 12,
                    "visible_hand_strokes": 7,
                    "traced_hand_strokes": 12,
                    "deferred_strokes": 0,
                    "recognition_duration_ms": 1000,
                    "estimated_hand_strokes_per_sec": 6.0,
                    "estimated_hand_path_short_edges_per_sec": 1.1,
                }],
                "color_metrics": {
                    "mode": "local-brush", "schedule": "object-progressive-v1",
                    "passes": 18, "pen_lifts": 1, "objects_colored": 2,
                    "color_sweeps": 8,
                    "object_records": [{"elementId": "subject", "colorPixels": 900, "sweeps": 4, "baseColorFrames": 8}],
                    "texture_frames": 6, "max_identity_ready_ratio": 0.82,
                    "max_finalize_residual_ratio": 0.02,
                },
                "hand_motion_qa": {
                    "version": "hand-motion-qa-v1", "status": "passed",
                    "frames": 120, "visible_frames": 72,
                    "max_step_short_edges": 0.017,
                    "p95_step_short_edges": 0.012,
                    "violation_count": 0, "worst_events": [],
                },
            }
            (root / "renders" / "scene-01-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            result = qa_final._stroke_art_quality_checks(root, project, scene_evidence(root))
            self.assertTrue(result["ok"])
            self.assertFalse(result["warnings"])

    def test_missing_local_brush_passes_fail_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "renders").mkdir()
            project = {
                "renderer_profile": {"stroke_planner": "semantic-v2", "color_fill": "local-brush"},
                "scenes": [{"id": "scene-01", "elements": [{"id": "subject"}]}],
            }
            (root / "renders" / "scene-01-profile.json").write_text(json.dumps({
                "stroke_planner": "semantic-v2",
                "stroke_strategy": "fast-semantic-trace-v1",
                "color_fill": "local-brush",
                "color_schedule": "object-progressive-v1",
                "stroke_metrics": [{
                    "total_strokes": 4, "traced_hand_strokes": 4,
                    "deferred_strokes": 0, "direction_bins": 2,
                }],
                "color_metrics": {
                    "passes": 0,
                    "object_records": [{"elementId": "subject", "colorPixels": 900, "sweeps": 0, "baseColorFrames": 0}],
                },
            }), encoding="utf-8")
            result = qa_final._stroke_art_quality_checks(root, project, scene_evidence(root))
            self.assertFalse(result["ok"])
            self.assertTrue(any("基础色未完成" in item for item in result["errors"]))

    def test_fast_trace_is_allowed_when_every_semantic_stroke_is_drawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "renders").mkdir()
            project = {
                "renderer_profile": {"stroke_planner": "semantic-v2", "color_fill": "local-brush"},
                "scenes": [{"id": "scene-01", "elements": [{"id": "subject"}]}],
            }
            profile = {
                "stroke_planner": "semantic-v2",
                "stroke_strategy": "fast-semantic-trace-v1",
                "color_fill": "local-brush",
                "color_schedule": "object-progressive-v1",
                "stroke_metrics": [{
                    "detail_strokes": 2, "identity_strokes": 1, "support_strokes": 1,
                    "total_strokes": 30, "direction_bins": 3,
                    "traced_hand_strokes": 30, "deferred_strokes": 0,
                    "estimated_hand_strokes_per_sec": 28.0,
                    "estimated_hand_path_short_edges_per_sec": 2.4,
                }],
                "color_metrics": {
                    "passes": 4, "objects_colored": 1, "color_sweeps": 4, "texture_frames": 0,
                    "object_records": [{"elementId": "subject", "colorPixels": 900, "sweeps": 4, "baseColorFrames": 8}],
                    "max_identity_ready_ratio": 0.7, "max_finalize_residual_ratio": 0.0,
                },
            }
            (root / "renders" / "scene-01-profile.json").write_text(json.dumps(profile), encoding="utf-8")
            result = qa_final._stroke_art_quality_checks(root, project, scene_evidence(root))
            self.assertTrue(result["ok"])
            no_hand = qa_final._stroke_art_quality_checks(
                root,
                project,
                {**scene_evidence(root), "render_metrics": {"hand_mode": "no-hand"}},
            )
            self.assertTrue(no_hand["ok"])
            self.assertEqual(no_hand["hand_mode"], "no-hand")

    def test_failed_frame_coordinate_qa_blocks_visible_hand(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "renders").mkdir()
            project = {
                "renderer_profile": {"stroke_planner": "semantic-v2", "color_fill": "local-brush"},
                "scenes": [{"id": "scene-01", "elements": [{"id": "subject"}]}],
            }
            profile = {
                "stroke_planner": "semantic-v2",
                "stroke_strategy": "fast-semantic-trace-v1",
                "color_fill": "local-brush",
                "color_schedule": "object-progressive-v1",
                "stroke_metrics": [{
                    "detail_strokes": 2, "identity_strokes": 1, "support_strokes": 1,
                    "total_strokes": 4, "direction_bins": 3,
                    "traced_hand_strokes": 4, "deferred_strokes": 0,
                    "estimated_hand_strokes_per_sec": 4.0,
                    "estimated_hand_path_short_edges_per_sec": 0.5,
                }],
                "color_metrics": {
                    "passes": 4, "objects_colored": 1, "color_sweeps": 4, "texture_frames": 0,
                    "object_records": [{"elementId": "subject", "colorPixels": 900, "sweeps": 4, "baseColorFrames": 8}],
                    "max_identity_ready_ratio": 0.7, "max_finalize_residual_ratio": 0.0,
                },
                "hand_motion_qa": {
                    "version": "hand-motion-qa-v1", "status": "failed",
                    "max_step_short_edges": 0.041, "violation_count": 2,
                },
            }
            (root / "renders" / "scene-01-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            result = qa_final._stroke_art_quality_checks(root, project, scene_evidence(root))
            self.assertFalse(result["ok"])
            self.assertTrue(any("逐帧运动不连续" in item for item in result["errors"]))

    def test_missing_traced_strokes_blocks_image_reveal_shortcut(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "renders").mkdir()
            project = {
                "renderer_profile": {"stroke_planner": "semantic-v2", "color_fill": "local-brush"},
                "scenes": [{"id": "scene-01", "elements": [{"id": "subject"}]}],
            }
            profile = {
                "stroke_planner": "semantic-v2",
                "stroke_strategy": "fast-semantic-trace-v1",
                "color_fill": "local-brush",
                "color_schedule": "object-progressive-v1",
                "stroke_metrics": [{
                    "detail_strokes": 2, "identity_strokes": 1, "support_strokes": 1,
                    "total_strokes": 16, "direction_bins": 3,
                    "traced_hand_strokes": 4, "deferred_strokes": 12,
                    "estimated_hand_strokes_per_sec": 2.0,
                    "estimated_hand_path_short_edges_per_sec": 0.4,
                }],
                "color_metrics": {
                    "passes": 3, "objects_colored": 1, "color_sweeps": 3, "texture_frames": 12,
                    "object_records": [{"elementId": "subject", "colorPixels": 900, "sweeps": 3, "baseColorFrames": 8}],
                    "max_identity_ready_ratio": 0.72, "max_finalize_residual_ratio": 0.0,
                },
                "hand_motion_qa": {
                    "version": "hand-motion-qa-v1", "status": "passed",
                    "max_step_short_edges": 0.014, "violation_count": 0,
                },
            }
            (root / "renders" / "scene-01-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            result = qa_final._stroke_art_quality_checks(root, project, scene_evidence(root))
            self.assertFalse(result["ok"])
            self.assertTrue(any("未由手部实际描绘" in item for item in result["errors"]))

    def test_exclusive_ownership_requires_metrics_and_unique_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "renders").mkdir()
            project = {
                "renderer_profile": {
                    "stroke_planner": "semantic-v2",
                    "color_fill": "local-brush",
                    "pixel_ownership": "exclusive-nearest-v1",
                },
                "scenes": [{"id": "scene-01", "elements": [{"id": "subject"}]}],
            }
            profile = {
                "stroke_planner": "semantic-v2",
                "stroke_strategy": "fast-semantic-trace-v1",
                "color_fill": "local-brush",
                "color_schedule": "object-progressive-v1",
                "pixel_ownership": "exclusive-nearest-v1",
                "ownership_metrics": {
                    "mode": "exclusive-nearest-v1",
                    "elements": [{
                        "candidate_foreground_pixels": 500,
                        "unique_anchor_foreground_pixels": 0,
                    }],
                },
                "stroke_metrics": [{
                    "detail_strokes": 0, "total_strokes": 3, "direction_bins": 3,
                    "traced_hand_strokes": 3, "deferred_strokes": 0,
                }],
                "color_metrics": {
                    "passes": 3, "objects_colored": 1, "color_sweeps": 3,
                    "object_records": [{"elementId": "subject", "colorPixels": 900, "sweeps": 3, "baseColorFrames": 8}],
                    "texture_frames": 0, "max_finalize_residual_ratio": 0.0,
                },
            }
            (root / "renders" / "scene-01-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )

            result = qa_final._stroke_art_quality_checks(
                root, project, {**scene_evidence(root), "render_metrics": {"hand_mode": "no-hand"}}
            )

            self.assertFalse(result["ok"])
            self.assertTrue(any("缺少统一的语义像素归属契约" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
