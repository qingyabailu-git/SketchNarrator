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

## Python 运行时依赖

根目录 MIT License 只覆盖 SketchNarrator 自身代码与明确标为第一方的素材，
不改变通过 `pip` 安装的第三方包许可证。下表按 `pyproject.toml` 的直接依赖列出
上游包元数据中的许可证；具体安装版本、传递依赖和随 wheel 携带的本机库仍以该次
安装包内的 `METADATA`、`LICENSE*` 和 `NOTICE*` 为准。

| 依赖 | 用途 | 上游许可证或分类 |
| --- | --- | --- |
| [PyAV](https://pypi.org/project/av/) | 音视频读写 | BSD-3-Clause |
| [NumPy](https://pypi.org/project/numpy/) | 数值计算 | BSD-3-Clause；发行包还声明 0BSD、MIT、Zlib、CC0-1.0 组件 |
| [opencv-python-headless](https://pypi.org/project/opencv-python-headless/) | 图像处理 | Apache-2.0 |
| [Pillow](https://pypi.org/project/Pillow/) | 图像读写 | HPND/MIT-CMU 系许可证；以安装版本随附文本为准 |
| [edge-tts](https://pypi.org/project/edge-tts/) | 默认 Edge 配音 | LGPL-3.0 |
| [piper-tts](https://pypi.org/project/piper-tts/) | 可选本地配音 | GPL-3.0-or-later |
| [Azure Speech SDK](https://pypi.org/project/azure-cognitiveservices-speech/) | 可选 Azure 配音 | 上游包标记为专有许可证 |
| [ElevenLabs SDK](https://pypi.org/project/elevenlabs/) | 可选 ElevenLabs 配音 | MIT |
| [sherpa-onnx](https://pypi.org/project/sherpa-onnx/) | 可选本地配音 | Apache-2.0 |
| [SoundFile](https://pypi.org/project/soundfile/) | Sherpa 音频写入 | BSD-3-Clause；其本机库另有各自许可 |
| [faster-whisper](https://pypi.org/project/faster-whisper/) | 可选转写与对齐 | MIT |
| [yt-dlp](https://pypi.org/project/yt-dlp/) | 可选来源提取 | Unlicense |
| [opencc-python-reimplemented](https://pypi.org/project/opencc-python-reimplemented/) | 可选中文文本规范化 | Apache-2.0 |
| [imageio-ffmpeg](https://pypi.org/project/imageio-ffmpeg/) | 可选 FFmpeg 获取与定位 | BSD-2-Clause；所带 FFmpeg 二进制另行适用其构建许可证 |

Piper、Sherpa、Whisper 等下载模型不随本仓库分发。模型权重、声音、词典和其他
运行时下载物可能具有独立许可证或使用限制，安装者必须在下载和再分发前单独核对。

## 中文字体

本仓库不分发微软雅黑、苹方或其他操作系统字体，也不要求某个商业字体。
运行时优先探测 Noto Sans CJK SC 或 Source Han Sans SC；用户也可通过
`SKETCHNARRATOR_FONT` 指向其有权使用的本机字体文件。Noto 与 Source Han
字体采用 SIL Open Font License 1.1；只有在下游实际重新分发字体文件时，
才需要同时附带对应字体包的许可证文本。

- <https://fonts.google.com/noto/specimen/Noto+Sans+SC>
- <https://github.com/adobe-fonts/source-han-sans>
- <https://openfontlicense.org/>

## 通用手部 PNG 素材

`assets/drawing-hand*.png`、`assets/eraser-hand*.png`、
`assets/hand-assets-preview*.png` 及 `renderer/assets/` 中对应副本是本项目
制作的第一方素材，随本仓库按根目录 MIT License 授权。它们不是从第三方
图库或字体包提取的素材。

可选 Presenter/IP 资产不在公开仓库内，也不由根目录 MIT License 自动授权；
安装者必须只加载自己有权使用的 Presenter 资产包及其独立许可说明。

## cs-board 深色画布笔迹适配

`renderer/scripts/render_stream_whiteboard.py` 中的深色画布分支改写自
ChenShuo2004/cs-board 的四角背景取样、暗色判定及 CLAHE/Canny 亮边提取思路。
SketchNarrator 只保留这段局部算法，并继续使用自己的语义笔画规划与渲染流程。
cs-board 采用 MIT License：

- 本仓库随附的完整版权与许可文本：[`licenses/cs-board-MIT.txt`](licenses/cs-board-MIT.txt)
- <https://github.com/ChenShuo2004/cs-board>
- <https://github.com/ChenShuo2004/cs-board/blob/main/LICENSE>
- <https://github.com/ChenShuo2004/cs-board/blob/main/scripts/render_stream_whiteboard.py>

## cs-board 风格配方与纸感隐喻路由

`references/style-registry.json` 中的 `paper-metaphor-collage`、
`retro-newspaper`、`black-gold-tech` 配方，以及
`scripts/visual_director.py` 的纸感隐喻关键词到既有构图的映射，改写自
cs-board `webapp/server.py` 中的对应风格描述和 `PAPER_METAPHOR_ROUTES`。
SketchNarrator 没有复制其服务端、前端、任务队列或参考 PNG；本仓库中的十张
风格参考图均为第一方生成资产，包括 `orderly-color-doodle/reference.png`，
随本仓库按根目录 MIT License 授权。

- <https://github.com/ChenShuo2004/cs-board/blob/main/webapp/server.py>
- <https://github.com/ChenShuo2004/cs-board/blob/main/LICENSE>

## 示例素材沿革

`renderer/examples/` 中下列文件曾使用 cs-board 历史示例。2026-09-08，两张图片
已使用 SketchNarrator 自有 `orderly-color-doodle` 风格参考重新生成，未以旧图作为
生成参考；标注也按新图重新编写。当前文件不再是上游原图或原样标注：

- `scene-01-monkey-mountain.png`
- `scene-01-monkey-mountain-banana.png`
- `scene-01-monkey-mountain-banana.annotation.json`

当前示例作为第一方生成图片及配套标注，随仓库按根目录 MIT License 提供。
上游代码的版权与许可文本仍保留在 [`licenses/cs-board-MIT.txt`](licenses/cs-board-MIT.txt)。
沿革说明见 [`renderer/UPSTREAM_README.md`](renderer/UPSTREAM_README.md)。

## Classic Light CC0 音效

`assets/sfx/classic-light/` 中的 WAV 文件由下列 CC0 公开素材转为
48kHz PCM16，并在运行时以较低增益混入旁白。Creative Commons Zero 允许复制、
修改、分发和商业使用；署名不是强制要求，本仓库仍保留作者与来源记录：

- `writing-pencil.wav`、`eraser-rub.wav`：AntumDeluge，Pencil Sounds，CC0 1.0
  <https://opengameart.org/content/pencil-sounds>
- `transition-whoosh.wav`：Fupi，Erase / Escape，CC0 1.0
  <https://opengameart.org/content/erase-escape>
- `emphasis-pop.wav`：EZduzziteh，Pop sounds，CC0 1.0
  <https://opengameart.org/content/pop-sounds-0>
- `conclusion-chime.wav`：PWL，Bell dings/chimes，CC0 1.0
  <https://opengameart.org/content/bell-dingschimes>
- CC0 1.0 法律文本：<https://creativecommons.org/publicdomain/zero/1.0/legalcode>
