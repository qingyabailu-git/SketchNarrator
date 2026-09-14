# 免费配音选择

这里把“离线本地”“免密钥在线服务”和“用户自备账户的正式 API”分开。联网服务的可用性、价格和条款会变化，本 Skill 不据此承诺免费额度。

| 工具 | 在本 Skill 中的定位 | 中文与时间轴 | 代价与限制 |
|---|---|---|---|
| [rany2/edge-tts](https://github.com/rany2/edge-tts) | 默认采用 | 普通话声音较多；可直接取得 `WordBoundary`，一次生成音频、`words.json` 和 SRT | 联网、免 API Key；它调用 Edge 在线语音服务，不承诺永久可用；代码 LGPL-3.0 |
| [Microsoft Azure Speech](https://learn.microsoft.com/azure/ai-services/speech-service/text-to-speech) | 用户明确选择的在线备用 | SDK 直接返回单词时间边界，适合字幕和画面同步；支持 SSML 语速、音量和音高 | 需要用户自己的 Azure 账户、Key 和 Region；不自动调用 |
| [ElevenLabs](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps) | 用户明确选择的在线备用 | 服务返回字符级时间，可映射到展示原稿 | 需要用户自己的 API Key 和 Voice ID；不自动调用 |
| [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) | 用户要求时的离线备选 | 有中文模型；先生成 WAV，再用 `align-voice` 对齐 | 模型不随 Skill 分发，用户需确认模型卡许可证 |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) + [MeloTTS](https://github.com/myshell-ai/MeloTTS) | 用户要求时的离线备选 | 中文和中英混读；生成 WAV 后再用 `align-voice` 对齐 | 模型不随 Skill 分发，需提供完整模型目录并实际试听 |
| [hexgrad/Kokoro-82M](https://github.com/hexgrad/kokoro) | 当前不采用中文 | 模型轻且支持普通话入口 | 既有本机样片出现普通话生硬、难懂，不能因模型轻或榜单表现就替代真机试听 |
| [debpalash/VoiceStudio](https://github.com/debpalash/VoiceStudio) | 用户已有本地服务时可选 | 通过 OpenAI 兼容语音接口生成 WAV，再用 `align-voice` 对齐 | Skill 不安装 VoiceStudio 或模型；只连接用户明确提供的本地地址 |
| [QwenAudio/CosyVoice](https://github.com/QwenAudio/CosyVoice) | 保留为高质量/声音克隆升级候选 | 中文自然度和声音控制更强；需要单独对齐或读取服务返回 | Apache-2.0 代码；模型大、部署重，只连接用户已有本地服务，不打包权重 |
| Windows SAPI | 只作系统应急 | 完全离线；中文声音取决于 Windows 已安装语言包 | 无额外安装，但常偏机械，不用于默认成片 |

## 选择顺序

1. 默认只生成 Edge-TTS，女声为 `zh-CN-XiaoxiaoNeural`；它的 `WordBoundary` 直接作为时间轴。
2. 口播稿与风格第一次确认通过后才生成配音；用户没有指定时只生成 Edge。
3. 用户明确选择 Piper、Sherpa 或 VoiceStudio 时才生成，并强制运行公开 `align-voice`；模型和外部程序不随 Skill 分发。
4. Azure 与 ElevenLabs 只有用户明确选择并已有配置时启用；Skill 不替用户注册、付费或保存密钥。
5. 第二次确认展示实际选中的试听文件、提供商、时间来源和分镜视觉计划。

## 离线准备与模型缓存

Piper、Sherpa 的本地合成与 Whisper 对齐是两个步骤；VoiceStudio 也需要在返回音频后对齐。公开 `setup --provider` 会准备对齐器依赖，但不会预下载 Whisper 权重。

`align-voice` 默认使用 `--model small`，也可明确指定其他模型名或已有的本地模型目录。它直接加载指定模型；与素材提取的 `--model auto` 不同，不会自动选用缓存中的另一种模型。缺少指定模型时可能联网下载，先向用户说明再准备。

Whisper 使用 Hugging Face 缓存，通常位于 `~/.cache/huggingface/hub`，可以通过 `HF_HOME` 或 `HF_HUB_CACHE` 指定缓存位置；设置了 `XDG_CACHE_HOME` 时默认根目录随之变化。完全离线运行前，由 Codex 在获得授权后准备 TTS 模型、对齐模型与依赖；离线阶段使用已准备的模型路径，并在启动命令前设置 `HF_HUB_OFFLINE=1` 禁止 Hub 网络请求。未准备齐全时停止并报告缺项，不擅自切换云配音。模型与声音的独立许可仍需遵守。变量含义见 [Hugging Face 官方说明](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables)。

## 时间轴规则

- Edge-TTS 使用它返回的 `WordBoundary`，不再额外跑语音识别。
- Azure Speech 使用 SDK 返回的 `WordBoundary`，不再额外跑语音识别。
- ElevenLabs 使用服务返回的字符时间，再映射到正确书面原稿。
- Sherpa、Piper、VoiceStudio、CosyVoice 或 Windows SAPI 没有可靠逐词时间时，统一通过 `align-voice` 交给 `faster-whisper` 做脚本对齐。
- 第二次确认必须包含实际选中的试听；自动识别匹配度只用于排除错读，最终选择仍以普通话、断句、停顿和语速的实际听感为准。

## 字幕语义规则

- `words.json` 只提供真实声音时间，不直接决定字幕边界。字幕边界先取锁定口播稿中的句号、问号、叹号、分号、冒号和逗号，再把这些短句映射回逐词时间。

Edge 默认使用 `+12%` 语速。任何语速调整后都重新采用本次 WordBoundary 和实际音频时长，不缩放旧时间轴。

V3 使用相邻场景之间“上一幕最后词结束→下一幕首词开始”的真实停顿安排板擦转场。擦除与净板的活动窗口默认控制在 500ms 内；停顿更长时先保持上一幕完整画面，把擦除放到下一句前的最后约半秒。可用停顿少于约 300ms 时在脚本/配音阶段明确报错，不在渲染阶段静默拉长画面、吞掉下一句或制造长时间净板。
- 一条字幕默认不超过约 18 个可读字符、3.6 秒；优先在原稿标点、明显停顿或转折词处拆分，完整短分句优先单独显示。
- 最终每条字幕只显示一行并去掉标点，禁止在“的、地、得、和、与、或、把、被、给、在”等虚词后断开；尽量避开画面主体。
- `stage-script-voice` 硬性检查字幕与原稿全文一致、单行、无标点、时间不重叠且不超出音频。主谓或虚词硬切、过长等问题输出人工编辑警告，不再根据标点保留率误判。

## 发音覆盖

- 正确书面语始终保留在 `script/narration.md`；同音送读替换只进入 `audio/tts-script.txt`。
- 发音覆盖必须写入 `audio/pronunciation-overrides.json`，记录 `written`、`spoken`、`reason` 和用户确认。未经用户确认不得自动替换。
- Whisper 回听可用于发现漏字、错字或疑似读音，但它的转写不是声学发音证明；有覆盖时仍需实际试听目标片段。
