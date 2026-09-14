"""Exercise the actual panel history functions without starting a server."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path


class SceneHistoryTests(unittest.TestCase):
    def test_scene_isolation_drag_grouping_and_saved_history(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        source = (Path(__file__).parents[1] / 'renderer/assets/panel.html').read_text(encoding='utf-8')
        history = source[source.index('const sceneHistories = new Map();'):source.index('function checkDirtyAndDiff()')]
        script = r'''
const assert = require('node:assert/strict');
const buttons = new Map();
const $ = id => { if (!buttons.has(id)) buttons.set(id,{addEventListener(){}}); return buttons.get(id); };
const document = {addEventListener(){}};
const storage = new Map();
const localStorage = {getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};
const canonicalJson = JSON.stringify;
const panelDraftKey = () => 'project';
const enterEditMode = () => {};
const syncSceneFields = () => {};
const renderElementList = () => {};
const renderCanvas = () => {};
const renderTimelineTab = () => {};
const renderAnimationTab = () => {};
const checkDirtyAndDiff = () => {};
const scene = id => ({id,annData:{elements:[{id:id+'-object',region:{x:0}}]}});
const App = {scenes:[scene('a'),scene('b')],activeSceneIdx:0,selectedElemIdx:0,
  originalHashes:{annotation:'original'},storyboardData:{scenes:[{id:'a',elements:[]},{id:'b',elements:[]}]},
  animationPlanData:{scenes:[],transitions:[]}};
''' + history + r'''
restoreSceneHistories();
App.drag = {};
App.scenes[0].annData.elements[0].region.x = 10;
recordSceneHistories();
App.scenes[0].annData.elements[0].region.x = 20;
recordSceneHistories();
App.drag = null;
recordSceneHistories();
assert.equal(sceneHistories.get('a').past.length,1);
App.scenes[1].annData.elements = [];
recordSceneHistories();
stepSceneHistory();
assert.equal(App.scenes[0].annData.elements[0].region.x,0);
assert.equal(App.scenes[1].annData.elements.length,0);
stepSceneHistory(true);
assert.equal(App.scenes[0].annData.elements[0].region.x,20);
App.originalHashes.annotation = 'saved';
restoreSceneHistories();
stepSceneHistory();
assert.equal(App.scenes[0].annData.elements[0].region.x,0);
persistSceneHistories();
sceneHistories.clear();
restoreSceneHistories();
stepSceneHistory(true);
assert.equal(App.scenes[0].annData.elements[0].region.x,20);
'''
        result = subprocess.run([node, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
