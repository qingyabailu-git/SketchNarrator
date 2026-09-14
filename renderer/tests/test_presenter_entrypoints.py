import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import make_presenter_preview
import render_presenter_bookend


class PresenterEntrypointTests(unittest.TestCase):
    def test_generic_bookend_module_owns_rendering_implementation(self):
        self.assertTrue(callable(render_presenter_bookend.render))
        self.assertEqual(render_presenter_bookend.render.__module__, "render_presenter_bookend")

    def test_generic_preview_uses_generic_output_names(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            with (
                patch.object(make_presenter_preview, "anchor_preview") as anchor,
                patch.object(make_presenter_preview, "bookend_preview") as bookend,
            ):
                outputs = make_presenter_preview.create_previews({}, out_dir)

        expected = (
            out_dir / "presenter-anchor-preview.png",
            out_dir / "presenter-bookend-preview.png",
        )
        self.assertEqual(outputs, expected)
        anchor.assert_called_once_with({}, expected[0])
        bookend.assert_called_once_with({}, expected[1])

if __name__ == "__main__":
    unittest.main()
