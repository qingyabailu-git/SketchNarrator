"""Boundary cases for public contracts; no private assets required."""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "renderer" / "scripts")]
from qa_final import object_color_errors
from phase_budget import phase_frames

class PublicContractTests(unittest.TestCase):
    def test_extra_sweeps_cannot_hide_another_object_deficit(self):
        records = [{"elementId": "a", "colorPixels": 50, "sweeps": 6, "baseColorFrames": 20},
                   {"elementId": "b", "colorPixels": 50, "sweeps": 1, "baseColorFrames": 8}]
        self.assertTrue(object_color_errors({"object_records": records}, {"a", "b"}))

    def test_ink_only_is_not_a_failed_colored_object(self):
        record = {"elementId": "ink", "colorPixels": 0, "sweeps": 0, "baseColorFrames": 8}
        self.assertEqual(object_color_errors({"object_records": [record]}, {"ink"}), [])
        self.assertTrue(object_color_errors({"object_records": []}, {"ink"}))

    def test_short_budget_fails_instead_of_shortening_protected_color(self):
        with self.assertRaises(ValueError):
            phase_frames(8)
        base = phase_frames(60)
        shortened = phase_frames(57, 60)
        self.assertEqual(base["base_color"], shortened["base_color"])
        self.assertEqual(base["recognition"], shortened["recognition"])

class ProjectLockTests(unittest.TestCase):
    def test_write_sections_serialize_but_render_lease_does_not_block_edits(self):
        import tempfile
        import threading
        from project_lock import project_lock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started, entered = threading.Event(), threading.Event()
            def writer():
                started.set()
                with project_lock(root):
                    entered.set()
            with project_lock(root, "render"):
                with project_lock(root):
                    thread = threading.Thread(target=writer)
                    thread.start()
                    self.assertTrue(started.wait(2))
                    self.assertFalse(entered.wait(0.05))
                self.assertTrue(entered.wait(2))
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_exception_releases_lock(self):
        import tempfile
        from project_lock import project_lock
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                with project_lock(directory):
                    raise RuntimeError("intentional")
            with project_lock(directory):
                pass


if __name__ == "__main__":
    unittest.main()
