---
name: sketch-narrator
description: 从中文主题、文案、视频链接、本地音视频或参考角色制作带自然配音、真实音频时间、分区笔迹和内部成片 QA 的完整手绘讲解视频。适用于素材转口播、黑板讲解、白板科普、故事口播和系列 IP 手绘视频；不用于纯 AI 视频模型生成或复杂 Web 工作台建设。
---

# SketchNarrator 手绘讲解视频

用户提供主题、时长、画幅、可选风格和参考图。Codex 负责风格推荐、脚本、默认 Edge 或用户明确选择的可选配音、分镜、视觉编排、整板图、语义标注、渲染和验收。用户进行三次轻确认，每次只确认即将成为下游输入的内容：

1. 最终口播稿与项目风格。确认前禁止生成配音。
2. Edge 配音、字幕、分镜摘要、每幕文字卡片、中文视觉编排表、节奏与素材摘要。确认前禁止生成整板图。
3. 整板画面与绘制顺序。确认后才允许正式渲染。

不要要求用户复制提示词、运行命令、安装依赖或管理文件。内部 QA 不增加用户确认关卡。

## 宿主与安装准备

支持 Codex、Antigravity、WorkBuddy、豆包中具备本地代理能力的使用方式，共用本 Skill 的文件和确认流程。下文的 Codex 指执行任务的宿主代理，不限定产品名称。宿主必须能读写项目、执行本地命令、生成并查看图片、访问本机工作台；仅聊天或仅上传文档的界面不能独立完成本地生产。不得因宿主名称自动认定这些能力可用。

安装时先运行 `scripts/run.cmd doctor --host <codex|antigravity|workbuddy|doubao> --project <项目目录>`（macOS/Linux 使用 `sh scripts/run.sh`）。该入口不安装依赖、不请求在线服务；输出 Python、依赖、字体、FFmpeg 和目录权限预检。宿主能力由代理根据实际工具确认，可用 `--capability` 声明，但报告中的声明不是运行验证。未探测项保持 unknown，不能以命令退出成功宣称安装完成。

先准备缺少的核心依赖，再复查；FFmpeg 缺失时，在已获安装授权的范围内执行 `setup --install-ffmpeg`，没有授权才一次性询问。该步骤检查实际编码能力，不修改系统 PATH，不新增创作确认关卡。安装准备属于使用前的独立阶段，不授权生产中修改源码或擅自升级环境。没有兼容 Python 时停止并说明环境前提，不自动安装系统 Python。

## 公共运行入口

Python 与依赖采用最低版本要求，不预设未来版本上限。新环境由 pip 按当前 Python、平台和依赖元数据解析可用稳定版本；旧 Python 可以选择仍兼容的依赖版本，不强迫所有平台使用同一最新版。现有可用环境默认复用，只有明确调用 `setup --upgrade` 才请求升级核心及所选可选依赖。安装后检查导入与依赖一致性并记录实际版本；这些检查不代替视频验收。尚未运行的新版组合标为未验证，不能承诺未来版本永远兼容。只有可复现的不兼容问题才添加针对性版本排除，并记录原因与解除条件。

不得假设 `python`、`uv` 或任何用户目录中的解释器位于 PATH，也不得把开发者机器的绝对路径写入项目。Windows 调用 `scripts\run.cmd <子命令>`，macOS/Linux 调用 `sh scripts/run.sh <子命令>`；两者会优先复用仓库内隔离环境并在首次使用时通过系统 Python 3.10+ 引导依赖，低于最低 Python 要求或损坏的旧环境不得继续复用。下文的 `python scripts/workflow.py` 表示该跨平台入口对应的工作流子命令，不是要求用户手动执行。

`sh scripts/run.sh` 不依赖下载包保留可执行位。首次安装应保留完整仓库，并固定安装目录名为 `sketch-narrator`；宿主能力、Python、中文字体、FFmpeg 和浏览器前提见 [安装与使用](README.md#安装与使用)。

## 工作区外部本机默认

公开 Skill 不携带私人角色、私人视频、私人音效或本机字体路径。需要把这些默认值接入本机生产时，在 Skill 目录之外提供 JSON 配置：优先使用环境变量 `SKETCHNARRATOR_LOCAL_SETTINGS` 指定文件；未指定时，工作区级 `.local/settings.json` 会被自动发现。配置由私有生产层维护，生产项目可以记录解析后的本机路径，但这些值不得回写到本 Skill、公共资产或发布清单。没有外部配置时，工作流保持公开默认行为。

外部配置可以提供 `sfx.default_manifest`、`presenter.manifest_path`、`presenter.caption_font.path` 或 `font.path`，以及 `bookends.intro`、`bookends.outro`、`bookends.fit_mode` 和 `bookends.background_color`。新项目初始化时自动接入这些默认值；正式渲染时，启用的开场和结尾固定片段会包住主片，QA 按实际前后片段时长校正正文时间窗口。公共层只实现这个通用配置协议，不包含任何具体私人值。

## 生产运行边界

本 Skill 的视频生产流程只能调用已经存在的公开命令并修改当前视频项目。生产任务、验收片和回归片不得读取实现代码以排障，不得修改、修补、同步或备份本 Skill、内部 `renderer/`、安装入口、测试、依赖或运行环境，也不得运行代码测试、结构校验和源码哈希对照。媒体结果不理想不构成开发授权。

如果现有公开命令无法完成某个生产步骤，只保留该步骤为未完成，不展开源码诊断，不在视频交付中输出源码缺陷或整改方案。只有用户另行明确要求修改或调试 Skill/renderer/工作台代码时，才进入与视频生产完全分开的开发任务。

## 边界与不变量

工作台允许任何标注编辑被保存，包括空框、越界、重叠和删除全部框；每幕独立撤销／重做。保存不运行生产质量门禁，预览和渲染才检查可执行性。统一定义见 [编辑与执行契约](references/timing-and-recovery.md#编辑生产与确认)。

分镜到标注沿用同一个对象 ID，QA 与渲染使用同一份独占前景映射；这是生产层内部契约，不是用户需要维护的字段。工作台用户可以调整区域、增删区域、绘制顺序和真实时间，但不编辑 storyboard、不改 semantic ID、不运行命令；保存时由生产层自动同步新增、删除和身份映射。第三次确认绑定计划内容与上游输入；后动画不得减少原始识别与基础色帧数。执行定义、旧项目迁移和显式重建入口统一见 [三项执行不变量](references/timing-and-recovery.md#三项执行不变量)。

需要分别绘制的元素，优先在分镜和生图阶段设计为独立、完整且互不共用前景的单元。布局是指导，不要求生图完美遵守；优先通过标注正常调整，不反复生图。像素疑点不阻止准备和渲染，用户确认当前工作台计划后按该版本执行；明显画面问题最多提醒一次，用户坚持后不得以质量门禁否决，也不能合并整幕规避。定义与例外统一见 [可独立绘制单元](references/unified-visual-language.md#可独立绘制单元)。

时间窗口、参考观看记录、语义标注草稿和失败恢复入口见 [时间与保存契约](references/timing-and-recovery.md)。使用这些公开入口处理当前项目；不得通过手改 `state.json` 哈希、拉长口播边界或合并为全画布区域消除报错。

- 脚本和分镜由 Codex 在当前任务中完成，不调用大模型 API。
- 图片优先使用 Codex/ChatGPT 内置 `imagegen`，不调用图片 API。
- 音频决定字幕、分镜、笔迹和成片时长；禁止按字数平均分时。
- 新项目默认启用内置 `classic-light` CC0 音效预设。音效由代码根据真实落笔、板擦、`visual-plan.json` 的结构化语义和安全后动画时间确定性编排，不调用大模型、剪映或外部服务；旁白始终优先，旧项目未声明 `sfx_profile` 时保持原有无音效行为。用户自备且有权使用的本地音效可通过项目 `sfx_profile.manifest_path` 或 Skill 外部的本机默认配置作为私有默认预设读取，但原文件和本机绑定不得复制进公开 Skill、项目模板或发布包。
- 字幕先按锁定原稿的完整语义短句和真实停顿切分，再映射到真实逐词时间；最终每条只有一行且正文不含标点。默认不加空格；只有原稿已有标点边界、两个语义完整短句仍被有意放在同一条字幕时，才允许用一个空格替代该标点。不得在原稿连续词语、短语、主谓结构、修饰关系或固定表达内部插空格。独立句意优先分成不同字幕，宁可切换更频繁，也不为凑长度强行合并。
- 展示文案必须保持正确书面语；为控制发音而做的同音替换只写入 TTS 专用文本。每条替换必须记录原词、送读词、原因和用户确认，Whisper 转写只能提示疑点，不能证明实际读音正确。
- 笔迹渲染只调用仓库内置的 `renderer/`。它是 SketchNarrator 的内部组件，不是第二个 Skill，也不回退到外部渲染 Skill。
- 默认使用 `layered + semantic-v2 + local-brush + object-progressive-v1`。逐对象完成结构与识别线条、动作支撑、基础色，再描绘可选纹理与润色。笔画与填色跟随真实手部，不瞬间补图；识别和基础色基线不被后动画挤占。
- 整板图登记时自动执行 `uniform-fit-no-crop`：把全部可见内容等比缩进顶部、左右和字幕安全框内，并同步变换图片、区域、保护区和对象掩码；禁止仅把大图向上平移造成头顶或边缘截断。
- 项目使用 JSON 和文件夹保存，不引入数据库、Next.js 或 FastAPI。
- 默认使用 `small-hand`：按输出画面短边比例缩放；接入用户自有角色时使用通用 `presenter` 协议，由底层独立角色层完成抱笔绘制与推板擦。`no-hand` 完全隐藏手部；`full-hand` 保留兼容模式；历史 `bare-tip` 只表示关闭覆盖。
- `presenter` 从 `renderer/assets/presenter.json` 或 `SKETCHNARRATOR_PRESENTER_MANIFEST` 读取用户有权使用的资产包。角色身份约束、禁止镜像等规则属于具体资产包，而不是公开核心的硬编码。笔尖、板擦使用真实归一化锚点；极端边缘无法完整显示时整层隐藏。固定开场与结尾模板仍由 `render_presenter_bookend.py` 生成无声片段；只有外部本机配置声明的固定视频，才会在正式渲染时按真实主片时长自动接入，且不提前改变三次确认门禁。
- 新计划取消局部轮廓描边、局部染色高亮和流光效果。逐对象绘制完成后稳定停留；只有明确聚焦意图和足够真实时间才允许一次 focus-push。必要的收笔撤手时间必须保留，可选效果不得挤占它或造成 QA 失败。
- `animation-plan.json` 的事件 `startMs/endMs` 使用幕内相对时间，板擦转场使用全片绝对逐词时间。V3.3 的同一转场同时保存在 `scenes[].transition` 与顶层 `transitions[]`，两份必须逐字段一致；控制台可以按幕内时间显示，但保存时必须转换回绝对时间并同步两份数据。
- 脚本/正式配音锁定后自动生成独立的 `visual-plan.json` 与 `visual-plan.md`。它只规划 section、shot、beat、A/B-roll、模板、构图、节奏、动画触发和素材需求，不直接生图，也不绑定尚未确定的 presenter IP。
- `visual-plan.json` 为每幕记录原生手绘表达和统一 `layout_plan`。先按 [统一视觉语言规范](references/unified-visual-language.md) 完成语义分解、时间预算和空间分配，再生成整板图；`native_regions` 与底部 `caption_region` 必须在同一个归一化坐标系内预先确定。默认 Edge、可选 Azure/ElevenLabs 和网络素材提取的数据与联网边界以 README/SECURITY 为准。
- 支持 `a_host`、`a_reaction`、`b_whiteboard`、`b_infographic`、`b_screenshot`、`b_text`；没有 presenter 素材时，前两者确定性降级为 `b_text` 或 `b_whiteboard`。截图/录屏缺失时必须列入素材需求或走明确 fallback，禁止伪造界面。
- 第一版模板包括 `question`、`focus`、`three-items`、`comparison`、`cause`、`process`、`timeline`、`summary`。所有 shot/beat 时间都回指锁定后的 `words.json` 真实边界，不能按字数估算。
- 任何上游修改都使下游确认、渲染缓存、成片和 QA 失效；旧成片先归档再替换。已经存在的整板图登记只标记为待复核，不得清空路径或删除文件；工作台必须继续显示这些画面供用户决定复用或替换。
- 口播稿一旦展示给用户就必须停止推进，等待明确确认；不得为了“先准备一下”提前调用 TTS。用户修改口播时只改稿，不生成或保留旧稿对应的正式配音、字幕与分镜。

## 0. 选择参考范围并提取素材（有链接或音视频时）

用户只给主题或现成文案时跳过本步。用户明确要求“只提取字幕/文案”时，范围已经明确为 `transcript`，无需重复追问。用户提出“参考这个视频制作一期短视频”但没有说明参考深度时，必须先单独询问并停止推进：

> 这次只参考原视频的文案口播，还是同时分析整条视频的镜头、画面、节奏和转场？完整分析会额外下载视频，并增加处理时间、网络流量、磁盘占用和 CPU 消耗。

只有用户明确选择后才初始化项目并执行提取。这个选择是素材处理范围，不占用脚本/风格、声音/视觉计划、整板图/渲染三次创作确认，也不得替代其中任何一次确认：

```powershell
python scripts/workflow.py extract-source --project <项目目录> --input <链接或本地音视频> --reference-scope transcript --source-language auto
python scripts/workflow.py extract-source --project <项目目录> --input <链接或本地视频> --reference-scope full --source-language auto
```

`transcript` 只取得完成改写所需的字幕和音轨，适合只参考知识点、结构或口播表达。`full` 在同一次字幕提取后，额外下载最高 720p 的网络参考视频（本地视频直接读取），对压缩视频做一次顺序解码，以最高 4Hz、总计不超过 4800 个运动样本确定性检测镜头边界、节奏和运动强度，再抽取最多 20 张关键帧并生成联系表；禁止为每个样本反复随机跳转视频。它只接受视频，不接受纯音频。完整模式生成的是待 Codex 实际看图的分析包，不会在脚本内调用视觉模型，也不会把原视频镜头直接复制成 SketchNarrator 分镜。

本步不调用大模型。命令会自动在本 Skill 隔离环境内准备 `yt-dlp`、`faster-whisper` 和 OpenCC，不得回退到其他应用的私有 Python。`--source-language auto` 先读取平台声明的原始语言、同语言字幕轨或 `*-orig` 标记；仍无法确定时由 ASR 判断。用户或素材已经明确是英文、中文时分别使用 `--source-language en`、`--source-language zh`，不得让英文音频按中文强制识别。字幕顺序始终是“同语言人工字幕 → 同语言自动字幕 → 同语言本地 ASR”，平台翻译字幕不能冒充源语言原稿。

ASR 模型默认使用 `--model auto`：先扫描本机 Hugging Face 缓存，有 `small` 时优先复用；没有 `small` 但已有其他受支持模型时直接复用现有模型；一个模型都没有时，首次使用才下载默认的 `small`。用户明确选择 `tiny`、`base`、`small`、`medium`、`large-v3-turbo` 或 `large-v3` 时按其选择执行，已缓存则不重复下载，未缓存则该明确选择同时授权下载对应模型。Codex 不得因为源视频是英文、自动识别失败或想提高准确率而擅自指定更大模型；语言切换只改 `--source-language`。自动准备提取依赖不等于授权安装 FFmpeg；若现有隔离环境和系统都没有可用 FFmpeg，先向用户取得明确同意，再用公开 `setup --install-ffmpeg` 能力安装。只有用户明确要求忽略原生字幕或需要逐词时间时才加 `--force-asr`。

提取命令是链接/音视频任务的阻塞步骤。启动后必须等待它明确返回 `EXTRACTION_STATUS=complete` 或失败；下载、模型加载和本地转写期间根据控制台的 `EXTRACT_STAGE` 继续等待，不得因暂时没有最终摘要而切换到浏览器截图、画面猜测或外部搜索代写。生成文件后还必须通过转写质量门禁：语言一致、内容量与时长不过度失衡、时间跨度合理、无异常大段重复，短转写不得只剩点赞订阅等推广幻觉。任一阻塞项出现时保留失败包和原因，但不得返回完成，也不得进入口播登记。

基础输出保存在项目的 `source/extracted/<时间戳>/`，包括源语言 `script.txt`、`voiceover.srt`、`voiceover.vtt`、`cues.json`、可用时的 `words.json`、音轨和 `extraction.json`。清单必须记录 `requested_language`、`transcript_language`、字幕来源、`requested_asr_model`、实际 `asr_model`、模型选择来源和 `quality`；工作流只有在 `quality.status=passed` 时才进入口播审核。`full` 还会生成 `analysis/source-analysis.json`、`analysis/source-analysis.md`、`analysis/contact-sheet.jpg` 和 `analysis/keyframes/`；网络来源另保存下载的视频副本，本地视频直接读取。每次提取使用新目录，保留旧结果。

完整模式完成后，必须实际查看 `contact-sheet.jpg`、代表性关键帧以及本地参考视频的连续片段，覆盖开场 0–3 秒、长镜头内部变化和关键转场。帧差低只表示检测信号弱，不能据此判定没有动画。补充视觉钩子、主体与构图、字幕样式、镜头/转场规律及原创改编方式后，用 `review-source --project <项目目录> --review <观看记录.json>` 登记实际结论；格式见 [时间与保存契约](references/timing-and-recovery.md)。没有完成这一步，不得声称已分析整条视频，也不得进入口播登记。只参考文案时不得下载整条参考视频或执行镜头分析。

提取稿只是参考素材，不是已经锁定的口播稿。非中文来源必须依次保留“源语言原始转写 → 校正后的源语言底稿 → 忠实中文翻译 → 中文口播改写”，不得让 ASR 直接生成中文，也不得用乱码中文反推原文。数字、专有名词和研究结论要对照开头、中段、结尾的真实音频抽样复核；只有确认后的新口播稿才进入正式配音与后续时间轴。不得因为原视频已经有时间戳，就把旧声音的时间轴套到改写后的新口播上。

只处理用户有权使用的公开或本地素材。不得绕过付费墙、DRM、登录限制或平台访问控制。YouTube、抖音等通用链接仅在 `yt-dlp` 明确返回与源语言匹配的公开字幕轨时直接读取；否则下载音轨后按已确定语言本地识别。最终必须以 `extraction.json.transcript_source`、`transcript_language` 和 `quality` 区分字幕来源、语言与可信状态，不得仅凭网页显示或文件存在宣称读取成功。

## 1. 初始化与风格

先让系统根据标题与题材推荐十种注册风格中的一种，并向用户说明推荐理由；用户可以直接接受，也可以显式改选：

```powershell
python scripts/workflow.py styles --title <标题> --topic <主题>
```

`init` 默认使用 `--style auto`，把推荐结果、理由和候选风格写入 `project.json.style_selection`。可显式选择 `warm-pencil`、`minimal-whiteboard`、`orderly-color-doodle`、`business-doodle`、`dark-chalkboard`、`guofeng-flat`、`comic-ink`、`paper-metaphor-collage`、`retro-newspaper` 或 `black-gold-tech`。其中 `orderly-color-doodle` 保留人物比例、毡尖笔压感和轻微自然抖动等真实画手痕迹，颜色使用干净、不透明的直接平涂，只在构图层稳定阅读路径、基线和留白；禁止退化成对称机器人、成套 UI 卡片、彩铅排线或渐变矢量模板：

```powershell
python scripts/workflow.py init --project <项目目录> --title <标题> --topic <主题> --duration-sec 60 --style auto
```

先只完成项目风格推荐和最终口播稿，不生成配音、字幕、分镜或整板图。向用户展示完整口播稿、预计时长、推荐风格及理由，等待第一次确认。用户修改时继续改稿；只有用户明确确认后，才进入配音与真实时间阶段。

展示前先登记本次待确认稿件和风格，并用确认材料包核对门禁：

```powershell
python scripts/workflow.py stage-script --project <项目目录> --script <口播稿> --style <style-id>
python scripts/workflow.py confirmation --project <项目目录>
```

用户明确确认后才执行：

```powershell
python scripts/workflow.py approve --project <项目目录> script-style
```

第一次确认之后再写 `storyboard.json`。每幕只解释一个语义步骤，按画面真实语义安排 1–6 个可独立绘制的元素：完整人物、道具及其不可分割的识别细节通常合为一个元素；只有能在不裁断当前对象、也不提前露出下一对象的情况下才拆分。不要为了凑数量拆图，也不要用一个大框笼统包住多个先后出现的对象。随后从以下构图中选择一种：

场景 ID 会直接成为板图、标注、预览和渲染文件名，只允许英文字母、数字、点、下划线和连字符，且必须以字母或数字开头；不得使用斜杠、反斜杠、盘符、空白、`..` 或中文标题充当 ID。

- `causal-chain`
- `before-after`
- `center-spoke`
- `timeline`
- `vertical-layers`
- `character-action`

相邻场景不得重复同一构图。重复出现的人物必须在 `characters` 中声明同一个 `id`，各幕通过 `character_ids` 引用，生图时始终传入同一角色参考。

新图不生成箭头、关系线、分隔线、装饰边框或可见网格；用对象位置、动作、大小和留白表达关系。不要因 composition 为因果链、流程或对比就补线。布局区域是不可见的规划，正常人物轮廓和必要道具线条保留。

## 2. 配音与真实时间

默认只生成 Edge 配音，不自动调用或生成 Piper、Azure、ElevenLabs、Sherpa 或 VoiceStudio。先明确告诉用户“本次使用 Edge（默认 `zh-CN-XiaoxiaoNeural`）”；只有用户明确选择其他提供商时才安装对应可选依赖并生成。公开工作流统一使用 `run.cmd`、`run.sh` 对应的工作流子命令，不提供会自行下载模型的旧版 PowerShell 旁路入口：

```bash
python scripts/workflow.py tts --project <项目目录> --provider edge --rate +12% --script <口播稿> --out-dir <项目目录>/audio/edge
```

Edge 的 `WordBoundary` 直接作为正式时间轴。Azure 使用原生词边界，ElevenLabs 使用服务返回的字符对齐。Piper、Sherpa 与 VoiceStudio 没有可靠逐词时间，生成后必须通过公开 `align-voice` 命令按展示原稿重新对齐。可选环境只在用户明确选择后准备：

```powershell
python scripts/workflow.py setup --provider <piper|azure|elevenlabs|sherpa|voicestudio>
python scripts/workflow.py align-voice --project <项目目录> --audio <配音文件> --script <口播稿> --out-dir <对齐输出目录>
```

Azure 只从 `AZURE_SPEECH_KEY` 与 `AZURE_SPEECH_REGION` 读取配置；ElevenLabs 只从 `ELEVENLABS_API_KEY` 与 Voice ID 读取配置；VoiceStudio 默认连接本机 `127.0.0.1:3900`。密钥不得写入项目、日志或仓库。任何付费服务只有用户明确选择并已有配置时才启用。

本地配音还需要对齐模型：`align-voice` 默认使用 `small`，缺少时可能联网下载，不自动采用素材提取的缓存选择策略。用户要求离线时，先按 [离线准备与模型缓存](references/free-tts.md#离线准备与模型缓存) 确认依赖及权重已准备，再运行本地合成与对齐。

自动回听匹配度只能排除错读，不能代替听感判断。第二次确认必须实际提供试听文件，并检查普通话、断句、停顿和语速。

如果书面写法与 TTS 送读写法不同，先创建 `pronunciation-overrides.json`。例如展示文案保留“那就大方地放出来”，为了让助词读轻声 `de`，用户确认后可把 TTS 文本替换为“那就大方的放出来”：

```json
{"version":1,"overrides":[{"written":"大方地","spoken":"大方的","reason":"助词地在这里读轻声 de","approved_by_user":true}]}
```

再生成实际送入 TTS 的文本：

```bash
python scripts/workflow.py prepare-voice-text --project <项目目录> --script <口播稿> --overrides <pronunciation-overrides.json> --out <tts-script.txt>
```

有发音覆盖时，用 `tts-script.txt` 合成声音，同时把正确书面稿传给字幕与逐词对齐层：

```bash
python scripts/workflow.py tts --project <项目目录> --provider edge --script <tts-script.txt> --display-script <口播稿> --out-dir <项目目录>/audio/edge
```

Edge 默认使用 `+12%` 语速；如果用户明确指定 10%–15% 内的其他速度则以用户选择为准。语速变化后必须使用本次实际音频和 WordBoundary 重新生成 `words.json`、字幕、分镜与转场，禁止缩放旧时间轴。

生成字幕时先借助带标点原稿按语义和真实停顿切分，最终写入 SRT 时去掉标点，每条只显示一行。默认每条约不超过 18 个可读字符、3.6 秒；一句或一个完整短分句优先单独显示，宁可增加字幕切换，也不把两个独立句意拼成一条。若确需同条显示两个完整短句，只能在原稿原有标点位置用一个空格替代；生成器不得凭长度或画面宽度在连续文字中发明空格，例如“私人飞机 坐的人太少”“天生 尾重头轻”均为错误。不得在“的、和、把、给、在”等虚词后硬切。语义切分问题进入警告并等待人工确认；正文不一致、多行、含标点、非法空格、时间重叠或超出音频属于阻塞错误。

登记最终选中的稿件、分镜、配音和时间：

```powershell
python scripts/workflow.py stage-script-voice --project <项目目录> --provider <提供商> --script <口播稿> --tts-script <实际送入TTS的文本> --pronunciation-overrides <发音覆盖JSON> --storyboard <分镜JSON> --audio <选中配音> --words <words.json> --captions <captions.srt>
```

`stage-script-voice` 是新分镜的统一编译入口。它只为尚未批准的草稿补齐可安全确定的场景 ID、对象 ID、字段别名、缺省构图和已经声明的卡片样式，不猜测语义触发时间，也不拿场景标题或口播首句冒充尚未设计的卡片文案；所有不能安全确定的问题一次完整列出，不允许依次报一个字段、反复登记。保存的是编译后的 `storyboard.json`，它与随后展示给用户的视觉计划使用同一对象身份和卡片计划。`status.next_command` 给出唯一优先动作，`allowed_commands` 给出当前阶段可执行动作；每次用户确认后必须先登记对应 `approve`，再按新状态执行，不能凭聊天内容跳过状态转换。

没有发音覆盖时可以省略 `--tts-script` 和 `--pronunciation-overrides`，系统会把展示文案原样登记为 TTS 文本。登记后项目同时保存 `script/narration.md`、`audio/tts-script.txt` 和 `audio/pronunciation-overrides.json`，便于复核书面表达、送读文本和用户批准记录。

生图前按[基础布局](references/unified-visual-language.md#生图前选布局)先确定独立绘制单元，再在分镜 `visual.layout` 中选择布局；把对象与区域对应关系纳入第二次确认。先调整文字空间计划，避免为选布局消耗图片额度。

`stage-script-voice` 同时生成 `visual-plan.json` 和便于确认的 `visual-plan.md`。展示已锁定口播稿、实际选中配音、提供商与时间来源、字幕、分镜摘要、视觉编排表、A/B-roll 占比、镜头总数、模板次数、最长无视觉变化时段、素材需求、A-roll 降级项目，以及每幕的文字卡片、手绘对象、顺序、真实触发词、时间、音效和失败回退，等待第二次确认。文字卡片是独立的本地渲染层，不能烘进生图板图，也不参与标注框与像素归属。类型只作为计划元数据，不进入成片画面。确认后：

必须先运行 `confirmation --project <项目目录>`，以其返回的 `files`、`scenes` 和 `visual_summary` 作为本次回复的完整交付清单。不得只说“若干白板镜头”，不得遗漏试听文件或 `visual-plan.md`，也不得在用户确认前进入整板图阶段。

```powershell
python scripts/workflow.py approve --project <项目目录> script-voice
```

## 3. 整板图与第三次确认

读取已经确认的 `visual-plan.json`。它是整板图生成的上游约束：逐幕按 `layout_plan.native_regions` 放置手绘对象，并始终避开底部 `caption_region`。现有 `b_whiteboard` 继续进入整板图、annotation 和 `animation-plan.json`；visual beat 的真实触发边界映射到动画计划。A-roll 逻辑槽位在 presenter 素材最终确定前只作为编排信息保留，不生成新的 IP 图片，不修改现有抱笔/板擦素材。

读取 [视觉制作规范](references/visual-production.md) 和 [风格注册表](references/style-registry.json)。每幕生成一张完整 16:9 整板图。生图输入必须按“本幕语义与元素 → 本幕构图与角色参考 → 风格参考图与风格契约 → 安全框及禁用项”拼接；风格注册表中的同一张 `reference_image` 同时供生图、工作台和验收使用。不要让每幕都成为相同的横向图标队列。已经批准的文字卡片由渲染器放在左上安全区，板图继续禁止生成任何文字或卡片底框。

若人物跨幕出现，先生成或选定角色参考图，之后每次生图都传入该参考。Codex 必须实际查看原图，再按口播语义和真实像素创建 `annotation.json`：

```powershell
python scripts/workflow.py add-board --project <项目目录> --scene-id scene-01 --image <图片> --annotation <标注JSON>
python scripts/workflow.py pace-annotations --project <项目目录>
python scripts/workflow.py preview --project <项目目录>
python scripts/workflow.py panel --project <项目目录>
```

`pace-annotations` 根据真实口播区间自适应拉长每笔的 `durationMs`（默认 72% 运笔 + 28% 留白），消除固定短时长造成的“手绘狂飙 1 秒、画面死静数秒”节奏失配。
`preview` 同时生成编号预览和 `previews/board-qa.json`，检查尺寸、区域、时序、底部字幕安全区、顶部/侧边截断风险、完整画面停留、背景一致性和相邻构图。随后必须由 `panel` 在 localhost 启动工作台并自动打开已加载的项目；不得让用户直接双击 `panel.html`。启动成功后读取控制台输出的真实 `PANEL_URL`，无论浏览器是否自动拉起，都必须把该地址作为可点击、可复制的 localhost 链接明确发给用户；如果自动拉起失败，直接提示用户将该地址复制到浏览器打开。禁止只给项目目录或本地 HTML 路径。

`panel` 启动前必须核对分镜场景与整板图登记。若项目目录中的 `boards/<scene-id>.*` 和对应 annotation 仍存在但登记丢失，自动恢复登记并标记为待复核；若文件确实缺失，工作台必须列出缺失场景并阻止依赖缺失素材的真实预览；已有编辑仍可保存，不得只显示标注框后宣称加载成功。fragment 令牌换成同源会话 Cookie 后从地址栏消失属于正常行为，不能据此判断图片权限失效。

发送 `PANEL_URL` 后必须立即停止操作并把工作台控制权交给用户。区域调整、播放、时间轴跳转、保存并应用、当前幕真实预览以及是否进入最终渲染都由用户亲自决定；Codex 不得代点按钮、代改参数、代保存、代生成真实预览或继续渲染。只有用户在当前任务中明确要求“替我操作”或“自动测试工作台”时，才允许使用浏览器控制执行用户指定的动作。

- 工作台编辑自由保存，生产检查不得阻止持久化；逐幕撤销和重做不恢复批准。保存、冲突副本、身份同步、确认失效和生产门禁统一见 `references/timing-and-recovery.md`，不得另设相反规则。

工作台维护稳定对象 ID：移动和排序不改变身份，新增创建 ID，删除同步移除对应分镜元素。用户无需编辑 storyboard 或身份字段。身份不明确时保留待处理编辑，不用标签、触发短句或顺序猜测；旧项目通过显式迁移处理。口播、场景边界和音频时间仍由上游确认保护。确认失效规则见 [编辑与执行契约](references/timing-and-recovery.md#编辑生产与确认)。

工作台可展示生产问题，但不得用画面、像素、时长或缺失素材问题阻止保存编辑。会话、路径、文件类型与版本冲突按 [保存与恢复](references/timing-and-recovery.md#工作台保存与恢复) 处理，按钮需明确说明实际状态。

“保存并应用”只表示编辑已经持久化，同时撤销受影响的批准并使旧计划和缓存失效；不表示动画计划已经重建或生产检查通过。冲突副本保存须明确提示未覆盖当前版本。需要真实预览或第三次确认时，显式使用“准备执行计划”或 `rebuild-plan`；准备失败不撤回保存。执行副本不得回写用户标注。具体身份、掩码与帧预算规则统一见 [三项执行不变量](references/timing-and-recovery.md#三项执行不变量)。

工作台提供两级预览：即时代理预览使用真实场景时长、逐词音频和字幕，支持播放、暂停、拖动跳转及五轨色块定位，用于快速检查区域、顺序、节奏、字幕与手部避让；它只做方向遮罩代理，不能冒充最终语义笔迹。用户保存修改后可手动生成“当前幕真实预览”，以 30fps、最长边 960px 调用正式 renderer 并缓存结果，用于检查真实笔迹、手部、上色和后动画；30fps 与正式默认帧率一致，避免低帧率预览把连续运动误判为卡顿。拖动或输入时禁止自动触发真实渲染，也不得因此合成整片。

每个 `region` 最好完整包围对应人物或道具并留出笔触余量。`board-qa.json` 除像素数量外，还会指出哪个对象的哪一侧可能被裁断；人物头顶、四肢或道具存在风险时，必须把实际预览和诊断图直接展示给用户。它是醒目提醒，不是保存、预览或用户确认后的渲染门禁。

新标注顶层写入 `drawingPlan`：默认 `mode: layered`、`strokePlanner: semantic-v2`、`colorSchedule: object-progressive-v1`、`colorReserveRatio: 0.32`、`minimumColorMs: 900`。兼容比例字段不覆盖已编译的帧预算。对象内部按结构与识别、动作支撑、基础色、可选润色推进；不要求人工逐条描路径。只有复现旧项目外观时才使用 `scene-final-v1`、`reading-bands-v1`、`contour-wipe` 或 `legacy`。

第三次确认前必须已有根据当前编辑显式准备的有效计划，并向用户直接展示该版本的整板图、实际绘制预览、风险诊断图、顺序与时间安排；只发送用户当前设备无法访问的路径不算展示。准备计划不是额外确认关卡。用户在工作台中完成检查后，等待其明确选择“无需修改”或“已经保存”，并确认是否允许最终渲染。用户看到提醒后确认渲染，即接受该版本的像素分配；视觉提示不得再次否决。只有用户明确允许渲染后：

```powershell
python scripts/workflow.py approve --project <项目目录> boards
```

## 4. 渲染与内部 QA

```powershell
python scripts/workflow.py render --project <项目目录>
python scripts/workflow.py qa --project <项目目录>
```

正式渲染会生成 `audio/sfx-plan.json` 与 `audio/sfx-track.wav`：落笔期间使用低音量书写声，板擦期间使用摩擦和轻微换幕声；明确的疑问、答案揭示、风险警告、成功解决与结论语义可各自使用短提示音。疑问优先来自 `visual-plan.json` 的 `question` 模板，也兼容 annotation 中真实的疑问元素；揭示、警示等提示来自 shot/beat 的结构化 `sfx_cue`、语义目标和触发短句，或已经存在的安全后动画，不单独扫描整篇口播标点、不另造动画。每幕同类语义最多自动安排一次，真人喊声、掌声和搞笑音效不得自动使用。音效时间全部来自 `visual-plan.json`、annotation 与 `animation-plan.json` 的真实音频锚点，不延长音频、不占用额外停顿，也不随机为每个元素添加提示。用户不需要音效时，本次渲染可加 `--no-sfx`；该参数完全跳过本机音效设置，空的外部设置文件不会覆盖公开默认音效。

正式渲染前运行板图 QA 并记录提示。像素缺口、跨区内容、独立锚点和布局偏差不阻断用户确认的计划；明显画面问题最多提醒一次，用户坚持后执行。数据无法读取或计划与输入不同是实际执行故障，须报告具体原因。不得把画面质量提示转成反复重试、重画或合并整幕。

渲染默认使用 `--jobs 2` 同时处理两幕；低内存设备或排障时使用 `--jobs 1`。渲染场景前先完成字体、外部片段、音效清单、音频、批准计划和全部输入预检，配置失败时一帧也不渲染。随后把绝对音频时间换算成精确帧计划，保留第一句前与场景间的真实空隙，所有场景帧数之和必须等于最终目标帧数。幕间擦除与净板合计默认不超过 500ms；真实停顿更长时保持上一幕完整画面，把擦除安排在下一句前的最后约半秒，净板后立即进入下一幕，禁止为了板擦人为制造长空白或延长音频。语义分层画法不追加音频之外的时间：每个对象在自己的既有时段中依次完成结构与识别、动作支撑、基础色和可选润色，最后短暂停留；写字音效覆盖该元素的真实 reveal 时段。动画时长预留只应用到由当前 annotation 与 `animation-plan.json` 确定性生成的渲染副本，不写回工作台保存的标注。每幕使用整板图、原始标注、有效派生标注、该幕动画计划、已批准文字卡片、风格、字体、手部、分辨率、帧率和渲染器指纹建立缓存；每幕完成后立即写入 `renders/scene-cache.json`，即使后续合成失败也可复用。输入不变时不重复渲染，只改一幕时只重做该幕。

渲染器自动读取真实解码音频时长。`words.json.duration_ms`、最后逐词时间、最后字幕时间和实际音频尾部必须一致；最终音画轨误差必须小于一帧，16:9 项目必须输出精确 16:9 分辨率。十种默认风格使用按短边比例缩放的 `small-hand`，避免大手遮挡和画风冲突；需要时可传 `--no-hand` 或 `--full-hand`。FFmpeg 由仓库内置 renderer 的统一检测器提供；缺失时先取得用户同意，再安装到本 Skill 的 `renderer/.venv`，不得修改系统 PATH。

`qa` 的结构、时长、逐词、字幕和渲染 profile 检查不依赖保存大量图片；视觉抽帧默认最多 48 张，并输出 `deliverables/qa-frames/contact-sheet.jpg` 与 `deliverables/qa-report.json`。普通视频只保留每幕一个代表性手绘中点与元素完成点、每次换幕的结束/衔接点、一个代表性动画的开始/中点/结束、随机 1–2 条字幕帧和末帧。只有抽样或 `hand-motion-qa-v1` 发现异常时，才围绕最多 5 个异常点临时扩大范围；不得默认抽取或逐张加载所有元素中点、所有字幕帧和全部过程帧。公开仓库的 `renderer/` 是本 Skill 的内部运行组件，不作为第二个用户入口；用户自有 Presenter 素材始终由公开包之外的配置接入。轻量抽样核对：

- 相邻构图、角色身份和画风是否连续。
- 未开始元素是否提前出现，场景切换是否长时间空白。
- 每幕代表性元素完成后的抽帧是否完整；对象级风险诊断和 renderer profile 覆盖其余元素，不得在下一元素或整幕结束时突然补出被截掉的局部。发现疑点应展示，不把它重新变成用户确认后的像素门禁。
- 笔尖是否贴近当前笔迹，是否遮挡主体。
- 默认使用 `layered + semantic-v2 + local-brush + object-progressive-v1`。逐对象完成结构与识别线条、动作支撑、基础色，再描绘可选纹理与润色。笔画与填色跟随真实手部，不瞬间补图；识别和基础色基线不被后动画挤占。
- 字幕是否清楚，末帧是否完整干净。

项目级自动 QA 和轻量抽样均通过后，记录内部结论：

```powershell
python scripts/workflow.py accept-qa --project <项目目录> --summary <实际看图结论>
python scripts/workflow.py validate --project <项目目录>
```

只有状态为 `complete` 才可交付 `final.mp4`。如果 QA 不通过，只能在当前视频项目内重做失败的稿件、配音、图片、标注、计划、渲染或合成层；不得检查或修改 Skill 源码，不得运行代码测试，也不得把视频任务扩张为工具开发。

## 修改与恢复

`workflow.py status` 显示当前阶段和下一动作。

- 第一次确认前改稿：只修改口播稿和风格，不生成配音。
- 第一次确认后改稿：撤销配音、字幕、分镜和画面确认，从第一次确认重新开始。
- 第二次确认后换声音：重新生成逐词时间、字幕、分镜与视觉计划，并重新走第二、第三次确认。
- 改稿、正式配音或 `words.json`：重新生成并确认 `visual-plan.json/.md`，同时撤销整板图确认并使动画计划、渲染和 QA 失效；保留已有整板图登记并标记为待复核，让工作台仍能加载画面。
- 换某幕图：只重新登记该幕，重新确认画面并重渲染。
- 只改字幕样式：保留场景视频，重新合成和 QA。
- 已有 `final.mp4`：渲染前自动复制到 `deliverables/archive/`。

写项目 JSON 时读取 [项目文件契约](references/project-contract.md)。比较声音时读取 [免费配音选择](references/free-tts.md)。评估外部仓库和许可证时读取 [GitHub 复用决策](references/reuse-decisions.md)。成片验收细节见 [成片 QA](references/final-qa.md)。

`focusTarget` 仅为明确聚焦提供真实前景边界。新计划不选择沿轮廓提示、局部染色高亮或流光；正常逐对象描线和基础色照常保留。只有明确聚焦且真实时间充足时才慢推 400–650ms、停留至少 400ms、幅度 4%–6%。旧效果代码仅用于读取历史文件，不作为新生产建议。

## 生产失败的恢复路径

`add-board` 只登记当前幕并输出 `previews/ownership-<scene-id>.json`；保存成功不代表生产通过，也不自动准备全片计划。归属待处理时仍可启动工作台编辑保存。需要定位时运行 `check-boards --project <项目目录>`，一次查看各幕错误及红色问题像素图。全部资料齐备后显式 `rebuild-plan`，展示并确认该执行版本。工作台返回 `semantic_sync_pending` 表示等待准备同步，不是要求用户再次保存；不要改用迁移、重建项目或复制全部产物来修这个状态。

同一错误第一次出现先查看明确的场景、对象和诊断图，只修对应标注。一次有证据的修正后仍出现相同错误且没有新证据，停止该修复循环并报告公开入口阻塞；不反复重画、调阈值、换登记顺序或创建副本。不用 PowerShell GetPixel 逐像素扫图，不用临时模拟 ffprobe 等程序冒充正式依赖。

批准的独立对象、触发词与顺序是视频内容。不得为通过像素门禁将多对象整幕合并，不得把语义降级描述为“不改画面”。只改失败层，复用已完成素材。代表幕真实预览须核对对象按触发顺序分别出现、逐对象完成基础色、正常撤手；静态终帧完整不能证明动画正确。沿用已有三次确认，不额外加关卡。

开始生产前及启用功能变化后运行 doctor；它同时读取项目或外部默认配置，检查外部片段可用的 FFprobe 或 PyAV 探测后端。需要准备时先解决环境，再生图或渲染；不等到最终合成才补依赖，不制作临时替身。

上游代理转交参考视频任务时，保留用户原话和已明确的参考范围；不能自行把“参考视频”扩写成完整镜头分析。用户已明确要求镜头、画面和节奏时可使用 full，不增加重复授权。
