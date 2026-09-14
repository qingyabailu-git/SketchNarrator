"""Saving incomplete work must never depend on production readiness."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import workflow as wf


class EditorPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scene = {'id': 'scene-01', 'start_ms': 0, 'end_ms': 2000, 'elements': []}
        wf.write_json(self.root / 'project.json', {'version': 1, 'title': 'editor', 'scenes': [self.scene]})
        wf.write_json(self.root / 'storyboard.json', {'scenes': [self.scene]})
        wf.write_json(self.root / 'state.json', {'boards': {}, 'approvals': {'boards': {'approved': True}}})
        self.rel = 'annotations/scene-01.annotation.json'
        wf.write_json(self.root / self.rel, {'sceneId': 'scene-01', 'elements': []})

    def save(self, content):
        changes = {'version': 1, 'projectFingerprint': wf.project_fingerprint(self.root),
                   'files': [{'path': self.rel, 'beforeSha256': wf.digest(self.root / self.rel),
                              'afterContent': content}]}
        wf.write_json(self.root / 'changes.json', changes)
        return wf.apply_panel(self.root, self.root / 'changes.json')

    def test_empty_and_outside_edits_save_without_reading_board_or_planning(self):
        with patch.object(wf, 'require_ownership', side_effect=AssertionError('production called')), \
             patch.object(wf, 'refresh_animation_plan', side_effect=AssertionError('planning called')):
            for elements in ([], [{'id': 'one', 'region': {'x': -45.6, 'y': 9, 'width': 0, 'height': 8000}}]):
                content = {'sceneId': 'scene-01', 'elements': elements}
                self.assertTrue(self.save(content)['success'])
                self.assertEqual(wf.read_json(self.root / self.rel), content)
                self.assertFalse(wf.read_json(self.root / 'state.json')['approvals']['boards']['approved'])

    def test_conflict_preserves_both_versions(self):
        old = wf.digest(self.root / self.rel)
        newer = {'sceneId': 'scene-01', 'elements': [], 'note': 'other editor'}
        wf.write_json(self.root / self.rel, newer)
        edited = {'sceneId': 'scene-01', 'elements': [], 'note': 'my edit'}
        wf.write_json(self.root / 'changes.json', {'version': 1,
            'projectFingerprint': wf.project_fingerprint(self.root),
            'files': [{'path': self.rel, 'beforeSha256': old, 'afterContent': edited}]})
        result = wf.apply_panel(self.root, self.root / 'changes.json')
        self.assertTrue(result['conflict'])
        self.assertEqual(wf.read_json(self.root / self.rel), newer)
        self.assertEqual(wf.read_json(self.root / result['saved_copy'])['files'][0]['afterContent'], edited)

    def test_empty_storyboard_saves_without_audio_or_visual_plan_generation(self):
        for elements in ([{'id': 'new', 'label': '', 'trigger_text': ''}], []):
            storyboard = {'scenes': [dict(self.scene, elements=elements)]}
            wf.write_json(self.root / 'changes.json', {'version': 1,
                'projectFingerprint': wf.project_fingerprint(self.root), 'files': [
                    {'path': 'storyboard.json', 'beforeSha256': wf.digest(self.root / 'storyboard.json'),
                     'afterContent': storyboard}]})
            with patch.object(wf, 'build_visual_plan', side_effect=AssertionError('production called')):
                self.assertTrue(wf.apply_panel(self.root, self.root / 'changes.json')['success'])
            self.assertEqual(wf.read_json(self.root / 'storyboard.json'), storyboard)
            state = wf.read_json(self.root / 'state.json')
            self.assertFalse(state['approvals']['script_voice']['approved'])

    def test_disk_failure_restores_previous_editor_data(self):
        before = (self.root / self.rel).read_bytes()
        original = wf.write_json
        def fail_state(path, value):
            if Path(path) == self.root / 'state.json':
                raise OSError('disk unavailable')
            return original(path, value)
        with patch.object(wf, 'write_json', side_effect=fail_state):
            with self.assertRaisesRegex(wf.WorkflowError, '回滚'):
                self.save({'sceneId': 'scene-01', 'elements': [], 'note': 'new'})
        self.assertEqual((self.root / self.rel).read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
