# GitHub 复用决策

检索日期：2026-08-24。候选代码仅在许可证允许且确实减少维护成本时采用；研究仓库不自动进入成品目录。

| 流程 | 候选项目 | 决策 | 原因 |
|---|---|---|---|
| Codex Skill 编排 | [gnipbao/story-to-handdrawn-video](https://github.com/gnipbao/story-to-handdrawn-video) | 借鉴 | MIT；自然语言驱动、Codex 生图清单、风格指纹和参考图锁定值得复用，但它输出静音 3:4 Remotion 画面，不直接采用渲染器。研究快照 `fbab5b27f4f0db61739d86f78000a39eeaa692d3`。 |
| 语义导演契约 | [s1dashu/director](https://github.com/s1dashu/director) | 借鉴，不复制实现 | 借鉴“先声明镜头目的、状态和动作，再选择效果”的导演层；落地为 visual beat 的语义目的、开始/结束状态、静止锚点与稳定降级。 |
| 简笔动画导演 | [kaomei/stickman-video-director](https://github.com/kaomei/stickman-video-director) | 借鉴，不接入生成链 | 借鉴动作服务于叙事、固定主体与限制同时运动数量；不引入其独立视频生成路径。 |
| 手绘提示词结构 | [kaomei/hand-drawn-video-prompts](https://github.com/kaomei/hand-drawn-video-prompts) | 借鉴语义字段 | 借鉴把构图、主体状态、动作和结尾状态拆开描述；不把提示词里的想象线条直接转换为渲染路径。 |
| 古诗绢本视频 | [Mr-funny/hbg-classical-poem-silk-video](https://github.com/Mr-funny/hbg-classical-poem-silk-video) | 借鉴后动画分层 | 借鉴在原始画面完成后叠加轻量气氛和提示层；第一轮仅实现真实前景遮罩内的安全后动画，不复制特定画风。 |
| 白板与动态信息图参考 | [ChenShuo2004/cs-board](https://github.com/ChenShuo2004/cs-board) | 借鉴风格与契约 | MIT；当前仓库有标准白板和真实短语时间驱动的动态信息图两条路径，并提供 12 个提示词风格、人物参考、单图重生和断点续做。V2 借其风格注册表、构图多样性和短语时间契约；暂不引入 OpenLux、IndexTTS、数据库或 Web 工作台。标准白板的旧式平均分时不能替代本 Skill 的真实音频时间。检索日期 `2026-08-24`。 |
| 分区笔迹渲染 | [geeklee/srt-whiteboard-animation](https://github.com/geeklee/srt-whiteboard-animation) | 内置改造 | MIT；上游算法已连同许可证和说明收进 `renderer/`，在单一 Skill 内维护，不再要求用户另外安装底层 Skill。研究快照 `696a7243c0e6ffb6827676e539c2ca5ebae2bf6b`。 |
| Remotion 笔迹组件 | [komplamoose/remotion-handdraw](https://github.com/komplamoose/remotion-handdraw) | V1 不采用 | 组件本身 MIT，自动分区和按骨架长度分时很好；但增加 Node、Chromium 和 Remotion 许可边界，而且一次只处理一张图。保留为未来 Remotion 渲染器候选。研究快照 `0e6b004639580244be7ebebddd4f576275e20394`。 |
| 逐词时间 | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 采用可选依赖 | MIT；支持 CPU int8、Windows 和 `word_timestamps=True`，用于不返回时间戳的 TTS 服务。 |
| 更精确强制对齐 | [m-bain/whisperX](https://github.com/m-bain/whisperX) | 暂不采用 | BSD-2-Clause；更精确但依赖更重、模型更多，单人 TTS 样片没有必要。 |
| 免费联网配音 | [rany2/edge-tts](https://github.com/rany2/edge-tts) | 已实现默认适配器 | LGPL-3.0；免 API Key，普通话声音较多，并能直接返回 `WordBoundary`。依赖 Edge 在线服务，不承诺永久稳定，故保留离线备用。固定测试版本 `7.2.8`。 |
| 正式联网配音 | [Microsoft Azure Speech SDK](https://pypi.org/project/azure-cognitiveservices-speech/) | 已实现可选适配器 | SDK 1.51.1；直接返回逐词时间并支持 SSML，适合视频同步；F0 有免费额度但需要账户。密钥只从 `AZURE_SPEECH_KEY` 和 `AZURE_SPEECH_REGION` 读取，不写入项目文件。SDK 使用微软许可。 |
| 轻量离线中文 | [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) + [myshell-ai/MeloTTS](https://github.com/myshell-ai/MeloTTS) | 已实现备用适配器 | 固定 sherpa-onnx 1.13.5，复用官方 `vits-melo-tts-zh_en` ONNX 模型；本机 4 线程生成 12.101 秒音频用时 5.592 秒，RTF 0.462。无逐词时间，继续走统一对齐器。 |
| 免费离线配音 | [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) | 已实现默认离线对照 | 官方包 `piper-tts[zh]==1.7.0`，GPL-3.0；本地运行且有 `zh_CN` 模型。默认使用 `zh_CN-chaowen-medium`，模型卡数据集为 CC0；模型不随 Skill 分发，音频继续用 `faster-whisper` 对齐。 |
| 本地多引擎配音 | [debpalash/VoiceStudio](https://github.com/debpalash/VoiceStudio) | 撤回本机采用 | OmniVoice 真机测试中 9 个汉字超过 300 秒并返回 HTTP 503，约占 4.1 GB 内存；本机程序、约 19.8 GB 模型与缓存已卸载。保留旧适配器只为兼容用户以后明确提供的远程服务，不进入工作流说明。 |
| Kokoro 中文 | [hexgrad/kokoro](https://github.com/hexgrad/kokoro) | 不采用 | 模型轻、Apache-2.0 且提供普通话入口，但既有本机中文样片不自然且难懂；不把英文表现或参数规模当作中文质量证据。 |
| 云端配音 | [elevenlabs/elevenlabs-python](https://github.com/elevenlabs/elevenlabs-python) | 已实现付费升级适配器 | SDK 为 MIT，`convert_with_timestamps` 同时返回音频和逐字符时间，可跳过本地识别；不再作为第一阶段的前置条件。 |
| OpenAI 配音 | [openai/openai-python](https://github.com/openai/openai-python) | 备选适配候选 | SDK 为 Apache-2.0；音频生成后仍用 `faster-whisper` 获得真实逐词时间。ChatGPT 订阅不能代替 API Key。 |
| 本地中文克隆 | [QwenAudio/CosyVoice](https://github.com/QwenAudio/CosyVoice) | 可选 HTTP 服务 | Apache-2.0 代码；部署和模型体积较大，不打包进 Skill。用户已有服务时通过适配器调用。 |
| IndexTTS | [index-tts/index-tts](https://github.com/index-tts/index-tts) | 不打包 | 代码与模型存在额外模型使用条款；只允许连接用户自己部署的服务，不复制模型或权重。 |
| 字幕文件 | [tkarabela/pysubs2](https://github.com/tkarabela/pysubs2) | 暂不引入 | 当前只需生成简单 SRT，标准库实现更小；出现复杂 ASS 样式编辑后再采用。 |
| 音视频合成 | [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) | 使用系统程序 | 直接调用用户系统中的 FFmpeg/ffprobe，不复制或分发二进制，避免构建许可证差异。 |
| 参考视频拆解 | [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) | 内置改造分析核心 | 复用并改写 `VideoAnalyzer` 的“下载视频→镜头/节奏/运动→关键帧→分析简报”结构，落地为 SketchNarrator 的 `analyze_reference_video.py`；保留现有字幕提取链，使用已有 OpenCV、Pillow、FFmpeg 与 yt-dlp，不引入其多代理、多管线、成本估算或项目管理层。网络视频限制最高 720p，完整分析必须由用户显式选择。研究快照 `cd9f3c1f03368be87b140af494914b8ee4e3c7a4`。 |

## 决策规则

- 先调用稳定 CLI 或 SDK，再考虑复制源码。
- 引入仓库前固定版本并保留许可证与来源；仅借鉴思路时不复制实现。
- 不为“看起来完整”而引入 Web 前端、数据库、任务队列或 GPU 模型。
- 新依赖必须先在 Windows 上跑一个真实输入，再进入默认链路。
