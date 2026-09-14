# 第三方运行时说明

本仓库不分发 FFmpeg 二进制文件。

渲染器可以使用用户自行安装的 FFmpeg，也可以在用户明确同意后，把
`imageio-ffmpeg` 安装到本 Skill 自己的 `.venv`。`imageio-ffmpeg` 是 BSD-2-Clause
许可的 Python 封装，其平台 wheel 通常携带独立的 FFmpeg 可执行文件；实际 FFmpeg
构建所启用的组件和许可条件以该二进制的 `ffmpeg -version` 输出及其随附说明为准。

FFmpeg 默认采用 LGPL 2.1 或更新版本；启用 GPL 组件的构建适用 GPL 2 或更新版本。
若下游项目自行重新分发 FFmpeg 二进制，必须自行履行对应许可证、源码和声明义务。

参考：

- <https://ffmpeg.org/legal.html>
- <https://ffmpeg.org/download.html>
- <https://github.com/imageio/imageio-ffmpeg>

## 字体与手部素材

渲染器不分发操作系统字体。运行时优先探测采用 SIL Open Font License 1.1
的 Noto Sans CJK SC 或 Source Han Sans SC，也允许通过
`SKETCHNARRATOR_FONT` 指向用户有权使用的字体。

公开的 drawing-hand/eraser-hand PNG 是 SketchNarrator 第一方素材，按仓库
MIT License 授权。可选 Presenter/IP 资产不随公开仓库分发，必须服从其独立
资产包的许可。

## cs-board 深色画布检测

`scripts/render_stream_whiteboard.py` 的深色画布分支改写自
ChenShuo2004/cs-board 的四角背景取样、暗色判定及 CLAHE/Canny 亮边提取。
语义笔画规划器和其余渲染流程仍为 SketchNarrator 自有实现。

cs-board 采用 MIT License：

- 随仓库保留的完整版权与许可文本：[`../licenses/cs-board-MIT.txt`](../licenses/cs-board-MIT.txt)
- <https://github.com/ChenShuo2004/cs-board>
- <https://github.com/ChenShuo2004/cs-board/blob/main/LICENSE>

## 示例素材沿革

`examples/scene-01-monkey-mountain.png`、
`examples/scene-01-monkey-mountain-banana.png` 与
`examples/scene-01-monkey-mountain-banana.annotation.json` 曾使用 cs-board 历史示例。
2026-09-08，两张图片仅以 SketchNarrator 自有风格图作为视觉参考重新生成，标注
按新图重新编写；当前文件作为第一方示例，按仓库根目录 MIT License 提供。
旧文件来源记录及当前入口说明见 [`UPSTREAM_README.md`](UPSTREAM_README.md)。
