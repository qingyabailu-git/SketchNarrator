from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from script_text import plain_narration_text


class ScriptTextTests(unittest.TestCase):
    def test_markdown_structure_never_reaches_narration(self) -> None:
        source = """---
title: 测试
---
# 口播稿

为什么猫爱钻纸箱？

```json
{"not": "spoken"}
```

- 因为纸箱让它更有安全感。
![配图](cat.png)
"""
        self.assertEqual(
            plain_narration_text(source),
            "为什么猫爱钻纸箱？\n\n因为纸箱让它更有安全感。",
        )

    def test_inline_markdown_keeps_visible_prose(self) -> None:
        self.assertEqual(
            plain_narration_text("这是 **重点**，参见[说明](https://example.com)。"),
            "这是 重点，参见说明。",
        )


if __name__ == "__main__":
    unittest.main()
