# 渲染器上游沿革

本目录由早期 SRT 白板动画渲染器演进而来，并保留了对
[ChenShuo2004/cs-board](https://github.com/ChenShuo2004/cs-board) 局部实现和示例的来源记录。
它是 SketchNarrator 的内部组件，不是第二个 Skill。

当前安装、使用、工作流和安全约束只以仓库根目录的
[`README.md`](../README.md)、[`SKILL.md`](../SKILL.md) 与
[`SECURITY.md`](../SECURITY.md) 为准；本文件不提供可执行入口或旧版操作说明。

## 已替换的上游示例

以下路径曾原样使用 cs-board 主分支提交
[`2c14596177de5ba24f634698dea31ae92dbd92c5`](https://github.com/ChenShuo2004/cs-board/commit/2c14596177de5ba24f634698dea31ae92dbd92c5)
中的同路径文件；该身份记录只适用于替换前的历史版本：

- `examples/scene-01-monkey-mountain.png`
- `examples/scene-01-monkey-mountain-banana.png`
- `examples/scene-01-monkey-mountain-banana.annotation.json`

2026-09-08，两张图片仅参考自有 `orderly-color-doodle` 风格图重新生成，配套标注
按新图重写；沿用文件名以保持示例路径稳定。当前示例按仓库根目录 MIT License
提供，不再包含原上游示例图片。标注中的时间仅演示静态示例的绘制顺序，不能
作为正式视频的音频时间来源。

从 cs-board 改写的代码仍受上游 MIT License 约束；完整版权与许可文本保存在
[`../licenses/cs-board-MIT.txt`](../licenses/cs-board-MIT.txt)，具体改写范围见
[`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
