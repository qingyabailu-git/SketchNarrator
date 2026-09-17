# 项目文件契约 V3

## 输出目录

```text
<project>/
  project.json
  state.json
  source/extracted/<时间戳>/
    script.txt
    voiceover.srt
    voiceover.vtt
    cues.json
    words.json                  # 本地 ASR 时存在
    extraction.json
    voiceover.<ext>
    analysis/                    # reference_scope=full 时存在
      source-analysis.json
      source-analysis.md
      contact-sheet.jpg
      reference-video.<ext>      # 网络视频下载副本；本地视频不复制
      keyframes/
        ref-scene-001-*.jpg
  script/narration.md
  storyboard.json
  visual-plan.json
  visual-plan.md
  animation-plan.json
  audio/narration.<ext>
  audio/tts-script.txt
  audio/pronunciation-overrides.json
  audio/words.json
  audio/captions.srt
  audio/sfx-plan.json
  audio/sfx-track.wav
  boards/scene-01.png
  annotations/scene-01.annotation.json
  previews/scene-01-preview.png
  previews/board-qa.json
  renders/scene-01.mp4
  renders/scene-cache.json
  renders/silent.mp4
  deliverables/final.mp4
  deliverables/validation.json
  deliverables/qa-report.json
  deliverables/qa-frames/contact-sheet.jpg
  deliverables/archive/
```

`project.json` 保存用户意图、风格、配音来源、渲染配置、可选文字卡片配置和场景索引；`state.json` 保存三次用户确认、当前产物哈希和内部 QA。任何文件都不保存 API 密钥。

`state.json.boards` 是整板图与 annotation 的持久登记，不是一次确认的临时缓存。改稿、换声音、修改文字卡片或重建逐词时间时，撤销受影响的批准、清空对应 `render_cache` 并把现有记录写成 `stale: true` 与 `stale_reason`，但保留图片路径、标注路径和文件哈希，供工作台继续显示和用户复核。用户重新登记该幕或再次确认全部板图后移除 stale 字段。工作台启动时可以从项目内约定路径恢复丢失的登记，但恢复项必须保持待复核状态；不得读取项目目录外的文件。

`state.json.approvals.boards` 绑定动画计划哈希、输入指纹和第三次确认时的像素风险快照。风险快照记录 `visual_warnings_are_advisory: true`、当前归属报告哈希及已展示的对象级风险；它证明执行的是用户看到的版本，不把视觉提醒升级成批准后的否决门禁。图片、标注、掩码、顺序、文字卡片或渲染参数变化时，原批准照常失效。

`state.json.source_extract` 登记当前一次参考素材提取的来源类型、去掉查询参数后的显示地址、用户明确选择的 `reference_scope`、提取来源、条目数、清单路径和文件哈希。`reference_scope` 只允许 `transcript` 或 `full`；命令行入口必须显式提供，不能把完整视频分析作为隐式默认。它不属于正式 `artifacts`，不会自动成为锁定口播，也不会使已经确认的项目阶段倒退。再次提取写入新的时间戳目录并更新这一指针，旧提取包保留。

`reference_scope: transcript` 只保存字幕、文案和音轨，不下载整条参考视频。`reference_scope: full` 还必须保存 `source_extract.analysis`，其中 `files` 对 `source-analysis.json`、`source-analysis.md` 和 `contact-sheet.jpg` 做项目相对路径与 SHA256 登记，`keyframes` 逐项保存场景 ID、相对路径与 SHA256，并记录镜头数、每分钟切换数和网络输入是否下载了完整视频。任一分析文件或关键帧缺失/变化时，`stage-script` 必须拒绝登记口播稿。

`source-analysis.json` 是确定性参考分析清单，不是已经批准的分镜：

```json
{
  "version": 1,
  "analysis_mode": "full-reference-video",
  "source": {
    "kind": "url",
    "display": "https://example.com/video/123",
    "analysis_video_path": "<项目内下载文件>",
    "duration_ms": 60000,
    "width": 1280,
    "height": 720,
    "fps": 30
  },
  "structure": {
    "scene_detection_method": "opencv-sampled-frame-difference",
    "pacing": {
      "scene_count": 12,
      "average_scene_ms": 5000,
      "shortest_scene_ms": 1800,
      "longest_scene_ms": 9200,
      "cuts_per_minute": 11
    },
    "scenes": [{
      "scene_id": "ref-scene-001",
      "start_ms": 0,
      "end_ms": 4200,
      "motion_type": "subtle-motion",
      "keyframe_path": "keyframes/ref-scene-001-0000002100ms.jpg",
      "visual_description": ""
    }]
  },
  "visual_review": {
    "status": "awaiting-codex-visual-review",
    "opening_hook": "",
    "visual_style": "",
    "camera_and_transition_patterns": [],
    "keep": [],
    "change": [],
    "sketch_narrator_adaptation": []
  }
}
```

镜头边界、节奏、运动类型和关键帧来自本地确定性分析。空白的 `visual_review` 与 `visual_description` 明确表示仍需 Codex 实际查看联系表和代表性关键帧；它们不得由文件存在性冒充已经完成。参考分析只为后续原创改写提供证据，原视频时间轴和镜头表不直接成为正式 `storyboard.json` 或 `visual-plan.json`。

渲染后 `project.json` 还记录 `render_fps`、`render_target_frames` 与按帧量化后的 `render_duration_ms`。`renders/scene-cache.json` 在每幕成功后立即保存缓存键、场景视频路径、SHA256、目标帧数、前导空白帧数和输入摘要；后续合成失败不删除它。`state.json.render_cache` 记录最近一次完整流水线采用的同一批缓存。缓存键覆盖整板图、标注、该幕 animation plan、已批准文字卡片、字体、风格、手部、分辨率、帧率和渲染器指纹。两处记录都不属于 `artifacts`，命中时必须重新核对键、文件和哈希。`state.json.render_metrics` 只记录本次并行数、命中数和阶段耗时，不作为成片 QA 证据。

`visual-plan.json` 与 `visual-plan.md` 是脚本/正式配音锁定后生成的独立视觉编排层。它只消费 `storyboard.json` 和真实 `audio/words.json`，记录 section、shot、beat、镜头类别、模板、构图、节奏、动画触发和素材需求；它不生成图片、不绑定尚未确定的 presenter IP。`state.json.artifacts.visual_plan` 与 `visual_plan_markdown` 保存两份文件的哈希。V1/V2 旧项目没有这两个文件时继续按旧安全默认读取；V3 项目重新登记脚本/配音时必须生成。

## project.json

V3 新项目必须保存注册风格、渲染配置和语义动画约束：

公开注册表目前提供十种风格。每项风格只保存一个 `reference_image`，并以
`visual_contract` 记录背景亮度、主色范围、线材、填色、纹理和可用构图；
项目文件只记录选中的 `style_id` 和渲染配置，不复制整份风格契约。

```json
{
  "version": 3,
  "title": "为什么黄金上涨",
  "topic": "解释黄金价格变化",
  "target_duration_sec": 60,
  "aspect_ratio": "16:9",
  "style": "暖米黄素描白板",
  "style_id": "warm-pencil",
  "renderer_profile": {
    "ink_path": "skeleton",
    "stroke_planner": "semantic-v2",
    "color_fill": "local-brush",
    "hand_mode": "small-hand",
    "draw_mode": "layered",
    "color_reserve_ratio": 0.32,
    "minimum_color_ms": 900
  },
  "sfx_profile": {
    "enabled": true,
    "preset": "classic-light",
    "mode": "deterministic"
  }
}
```

场景 `id` 同时用于板图、标注、预览和渲染文件名，只允许 1–128 位 ASCII 字母、数字、点、下划线和连字符，并且必须以字母或数字开头。任何包含斜杠、反斜杠、盘符、空白或以点开头的场景 ID 都必须在写文件前拒绝。

每个 `scenes[].elements[]` 都必须提供幕内唯一且稳定的 `id` 和可回指口播的 `trigger_text`。分镜、标注、对象掩码和动画计划共同引用这个 ID；移动、缩放、调整顺序或时间不改变 ID。旧项目只有在对象关系无歧义时才能显式迁移并撤销受影响的批准，不能在生产过程中临时猜测身份。

用户自定义风格使用 `style_id: custom`，渲染参数回退到 `warm-pencil`，同时保留用户给出的 `style` 名称。

`sfx_profile` 只控制最终合成层。新项目默认启用仓库内置的 CC0
`classic-light`；本机存在 `.local/settings.json` 时，可在不修改项目和公开默认值的情况下
使用用户有权使用的私有默认清单。旧项目缺少该字段时维持无音效。渲染时自动生成
`audio/sfx-plan.json`，其时间基准为全片绝对音频毫秒：

```json
{
  "version": 1,
  "enabled": true,
  "preset": "classic-light",
  "timebase": "absolute-audio-ms",
  "duration_ms": 60000,
  "events": [
    {
      "id": "scene-01-writing-cause",
      "effect": "writing",
      "asset": "writing-pencil.wav",
      "scene_id": "scene-01",
      "target_id": "cause",
      "start_ms": 100,
      "end_ms": 2000,
      "gain_db": -27.0,
      "loop": true,
      "fade_ms": 35
    }
  ]
}
```

`writing` 来自各 annotation 的 `reveal`；`eraser` 与 `transition` 来自
V3.3 绝对板擦时间；`emphasis` 或 `conclusion` 只来自已经进入渲染的安全后动画。

用户拥有合法使用权的本地音效可以通过 `manifest_path` 覆盖内置预设。路径可以是
项目相对路径或本机绝对路径；清单中的五类 WAV 必须位于清单目录内，并统一为
48kHz PCM16。外部素材只在本机读取，不复制到项目、Skill 或发布包：

```json
{
  "sfx_profile": {
    "enabled": true,
    "preset": "private-local",
    "mode": "deterministic",
    "manifest_path": "D:/my-private-sfx/manifest.json"
  }
}
```

若同一台机器上的全部新项目都要使用同一私有清单，可参照
[`local-settings.example.json`](local-settings.example.json) 创建不会进入 Git 和公开发布清单的
`.local/settings.json`：

```json
{
  "version": 1,
  "sfx": {
    "default_manifest": "D:/my-private-sfx/manifest.json",
    "gain_db_overrides": {
      "question": -15.0,
      "reveal": -2.0,
      "warning": -15.0
    }
  }
}
```

项目中的 `manifest_path` 优先于本机默认；`use_local_default: false` 可让单个项目明确回到
公开 `classic-light`。`gain_db_overrides` 是最终增益值而不是叠加量，允许范围为
`-60dB` 到 `+12dB`。

外部设置文件可以只配置字体、Presenter 或片头片尾；缺少 `sfx` 表示没有本机音效覆盖，继续使用项目与公开包默认值。显式 `render --no-sfx` 优先级最高，完全跳过本机音效配置和素材读取。音效清单与素材格式必须在任何场景渲染前完成预检。

`manifest_path` 属于本机配置。公开项目模板和示例不得写入开发者绝对路径，也不得
随仓库分发来源不明、禁止再分发或仅限特定平台使用的原始音效。
音效轨固定为 48kHz PCM16，不改变旁白、字幕、场景边界或最终时长。

所有预设必须包含 `writing`、`eraser`、`transition`、`emphasis`、`conclusion`
五个基础角色。私有预设还可提供 `question`、`reveal`、`warning`、`success`
四个可选语义角色；缺失时分别安全回退到 `emphasis` 或 `conclusion`，因此旧预设
与旧项目保持兼容。`question` 优先由 visual shot 的 `question` 模板触发，也兼容标注元素的
`id`、`type`、`label`、`narrativeRole`。visual shot/beat 还可显式写入
`sfx_cue: question|reveal|warning|success|conclusion`；没有显式值时，只在已经切好的 beat
结构字段中识别同类语义。规划器不扫描整篇口播标点，每幕同类语义最多自动安排一次。

## storyboard.json

```json
{
  "version": 3,
  "characters": [
    {"id": "host", "name": "讲解者", "reference": "references/host.png"}
  ],
  "scenes": [
    {
      "id": "scene-01",
      "title": "需求上升",
      "narration": "越来越多的人开始购买黄金。",
      "start_ms": 0,
      "end_ms": 6800,
      "composition": "causal-chain",
      "title_card": {
        "text": "需求为何上升",
        "position": "top-left",
        "style": "outlined-label-v1",
        "accent": "#356AE6"
      },
      "character_ids": ["host"],
      "elements": [
        {"id": "buyers", "label": "购买黄金的人群", "role": "原因", "trigger_text": "越来越多的人"},
        {"id": "purchase", "label": "独立的购买动作", "role": "变化", "trigger_text": "开始购买"},
        {"id": "gold", "label": "独立的黄金实物", "role": "对象", "trigger_text": "黄金"}
      ]
    }
  ]
}
```

- `id` 唯一，使用 `scene-01` 格式。
- `start_ms`、`end_ms` 来自真实配音，递增且不重叠。
- 每幕 1–6 个按语义自适应的可见元素，不写抽象口号。完整对象及其识别细节通常不拆；只有能独立隐藏和出现、不会裁断相邻对象时才拆。
- 分镜元素是后续独立绘制的单元。需要先后出现的内容在生图前分别分配完整空间，轮廓、动作范围和道具归属唯一；按 [可独立绘制单元](unified-visual-language.md#可独立绘制单元) 检查，不以重叠标注或共享像素分配替代构图设计。
- 新分镜不使用箭头、虚线轨迹、关系线或分隔线；关系由对象位置、动作、大小和留白表达。旧项目的 relation 字段只保留读取兼容。
- `composition` 必须来自视觉制作规范；相邻场景不得相同。
- `character_ids` 只能引用顶层 `characters` 中已声明的 id。
- `title_card` 是可选的场景级计划。启用项目级 `title_card_profile.required_per_scene` 后，每幕必须在 Gate 2 前明确填写 `text`；编译器只补位置、样式和强调色，不从标题或口播猜文字。卡片独立于板图、标注和像素归属，并在该幕全部帧中持续显示。

`stage-script-voice` 在保存前编译尚未批准的分镜草稿：补齐可确定的安全字段，并一次列出全部缺失的触发依据、非法构图、身份、卡片和时间问题。编译不得按元素数量平均分配触发词；正式保存的 `storyboard.json` 与第二次确认看到的视觉计划共享同一身份、时间依据和卡片计划。

## visual-plan.json

```json
{
  "version": 1,
  "timebase": "absolute-word-ms",
  "sections": [{"section_id": "scene-01", "shot_ids": ["scene-01-shot-01"]}],
  "shots": [{
    "shot_id": "scene-01-shot-01",
    "section_id": "scene-01",
    "start_phrase_id": "w-0001",
    "end_phrase_id": "w-0004",
    "start_ms": 120,
    "end_ms": 4200,
    "mode": "B-roll",
    "shot_type": "b_whiteboard",
    "purpose": "解释原因",
    "template": "cause",
    "composition": "causal-chain",
    "layout_plan": {
      "planned_before_board": true,
      "coordinate_space": "normalized-0-1",
      "caption_region": [0.05, 0.86, 0.90, 0.12],
      "template": "side-by-side",
      "native_regions": [{
        "region_id": "native-01",
        "element_ids": ["cause"],
        "purpose": "完整的原因对象",
        "box": [0.05, 0.07, 0.43, 0.72]
      }, {
        "region_id": "native-02",
        "element_ids": ["result"],
        "purpose": "完整的结果对象",
        "box": [0.52, 0.07, 0.43, 0.72]
      }],
      "semantic_ownership": {"native": [
        {"semantic_id": "cause", "spoken_span": "原因", "role": "entity-action", "primary_owner": "native"},
        {"semantic_id": "result", "spoken_span": "结果", "role": "entity-action", "primary_owner": "native"}
      ]},
      "board_generation_contract": "draw-inside-native-regions"
    },
    "beats": [{
      "beat_id": "scene-01-shot-01-beat-01",
      "trigger_phrase_id": "w-0001",
      "start_ms": 120,
      "end_ms": 288,
      "action": "reveal",
      "target": "cause",
      "sfx_cue": "reveal"
    }],
    "expression_mode": "native",
    "required_assets": [],
    "fallback": null
  }]
}
```

镜头的 `start_ms/end_ms`、`start_phrase_id/end_phrase_id` 和 beat 时间必须逐项回指 `words.json` 的真实词边界。普通镜头短于约 2 秒时必须带明确的短内容例外；超过约 12–15 秒必须有内部 beat；连续三个镜头不得完全复用模板与构图；同时运动主体最多两个。`a_host/a_reaction` 只保存 `actor_slot`、`emotion`、`action` 等逻辑槽位。缺 presenter 素材时确定性降级到 `b_text` 或 `b_whiteboard`；截图/录屏缺失时必须记录 `required_assets` 或 fallback，不能伪造界面。

`expression_mode` 固定为 `native`。每个 shot 都必须先生成 `layout_plan`，让 `native_regions` 和 `caption_region` 在整板图之前使用同一归一化坐标系完成空间分工。基础布局及自定义区域规则见 [空间规划](unified-visual-language.md#生图前选布局)。每个区域对应一个完整绘制单元，规划不能代替生图后的真实标注。视觉 beat 必须逐项绑定当前 shot 和真实 `words.json` 词边界。

V1/V2 旧项目可以继续读取；新项目按 V3 校验。

V1/V2 旧项目不要求 `animation-plan.json`，缺失时采用无语义动画、无板擦转场的安全默认；新 V3 项目由工作流根据真实 `words.json` 自动生成，不要求用户手写。

## words.json

```json
{
  "version": 1,
  "source": "edge",
  "language": "zh",
  "duration_ms": 6800,
  "words": [
    {"text": "越来越多", "start_ms": 120, "end_ms": 720}
  ]
}
```

每个词必须有非负 `start_ms` 和更大的 `end_ms`。Piper、sherpa 或其他不返回可靠逐词时间的 TTS 必须经过统一对齐器。

`duration_ms` 必须存在且不早于最后一个词的 `end_ms`。登记脚本和声音时，`duration_ms`、最后逐词时间、最后字幕时间与实际解码音频尾部的误差不得超过工作流容差；最终 QA 还会以成片帧率重新检查，禁止把文字、字幕或场景静默延伸到音频之外。

## 展示文案与 TTS 文本

`script/narration.md` 保存正确书面语，是字幕正文和用户确认的权威文本。`audio/tts-script.txt` 保存实际送入配音引擎的文本。没有发音覆盖时两者相同；有覆盖时只能修改送读文本，不能把同音替换写回展示文案。

`audio/pronunciation-overrides.json` 即使没有覆盖也必须存在：

```json
{
  "version": 1,
  "overrides": [
    {
      "written": "大方地",
      "spoken": "大方的",
      "reason": "助词地在这里读轻声 de",
      "approved_by_user": true,
      "replacement_count": 1
    }
  ]
}
```

每条覆盖必须命中展示文案、原词与送读词不同、原因非空并且 `approved_by_user` 为 `true`。最终 QA 核对 TTS 文本是否可由展示文案和覆盖记录确定性推导；实际发音仍须人工试听，Whisper 识别结果不能作为发音准确的证明。

## captions.srt

字幕内容必须与 `script/narration.md` 归一化后完全一致。带标点原稿用于判断语义与停顿边界，但最终每条字幕只允许一行且正文不含标点。默认不加空格；只有同条字幕保留两个完整短句时，才允许在原稿已有标点的位置用一个空格替代标点。任何位于原稿连续文字内部的中文空格都是非法硬拆。独立句意优先拆成不同字幕；主谓、动宾、虚词边界和过长字幕产生人工编辑警告；正文缺失或增加、多行、含标点、非法空格、时间倒序/重叠以及尾部超出音频属于阻塞错误。

## annotation.json

沿用 `srt-whiteboard-animation` 的像素级配置：

```json
{
  "sceneId": "scene-01",
  "canvas": {"width": 1672, "height": 941},
  "sceneDurationMs": 6800,
  "drawingPlan": {
    "version": 2,
    "mode": "layered",
    "strokePlanner": "semantic-v2",
    "colorSchedule": "object-progressive-v1",
    "pixelOwnership": "semantic-masks-v1",
    "colorReserveRatio": 0.32,
    "minimumColorMs": 900
  },
  "elements": [
    {
      "id": "crowd",
      "label": "购买黄金的人群",
      "sequence": 1,
      "narrativeRole": "原因",
      "subtitle": "越来越多的人开始购买黄金",
      "region": {"x": 40, "y": 80, "width": 430, "height": 720},
      "reveal": {
        "direction": "left_to_right",
        "startMs": 100,
        "durationMs": 1900,
        "protectedRegions": []
      },
      "handPath": {"start": [60, 400], "end": [450, 400], "easing": "easeInOut"}
    }
  ]
}
```

对象掩码记录实际绘制内容。布局和像素疑点用于提示，不是用户确认后的否决条件；确定性选区分配、确认与执行规则见 [执行契约](timing-and-recovery.md#三项执行不变量)。

对象身份由分镜 ID 和明确掩码决定。默认 `layered + semantic-v2 + object-progressive-v1` 先描结构与识别、动作支撑，再以真实手部横向往返上基础色，最后润色。兼容比例字段不覆盖计划的整数帧预算。

新渲染 profile 的每个对象记录 `stage_order`、`total_strokes`、`traced_hand_strokes` 与 `deferred_strokes`。`traced_hand_strokes` 必须等于 `total_strokes`，`deferred_strokes` 必须为 0；不再存在代表性笔画删减、扫描揭图或因果前沿。`color_metrics.object_records` 逐对象记录 `elementId`、`colorPixels`、`sweeps` 和 `baseColorFrames`；有待填色像素的对象至少三次描绘且基础色至少 8 帧，无待填色像素的对象记录零次。总次数仅用于统计，不能替代逐对象检查；`motion_plans` 中的填色策略为 `serpentine-flat-fill-v1`；最终填色后整板像素仍必须与原图一致。

### 手部与板擦

`hand_mode` 取值为 `small-hand`、`presenter`、`no-hand` 或 `full-hand`。默认 `small-hand` 按最终输出画面短边的 12%–16% 计算高度；`presenter` 读取用户自有 `renderer/assets/presenter.json` 或 `SKETCHNARRATOR_PRESENTER_MANIFEST`，使用角色抱笔与推板擦透明层；`no-hand` 完全不显示覆盖物；`full-hand` 保留旧的大手高度策略。

正式手部素材及归一化锚点见底层 Skill 的 `assets/hand-assets.json`：笔尖锚点用于落墨对齐，板擦接触面中心用于擦除掩码与板擦手同步。

使用 Presenter 资产包的项目可增加以下配置。V1 只启用绘制/板擦覆盖层和独立开场、结尾片段生成；在固定开场/结尾口播、声音及真实时长没有锁定前，`intro`、`outro` 不自动拼接到 `final.mp4`。

```json
{
  "renderer_profile": {"hand_mode": "presenter"},
  "presenter_profile": {
    "id": "my-presenter-v1",
    "manifest": "private-assets/presenter.json",
    "intro": {"enabled": true, "title_source": "project.topic"},
    "outro": {"enabled": true, "message": "讲完啦，下次见！"},
    "auto_merge": false
  }
}
```

`presenter_profile.manifest` 可以是项目内相对路径或本机绝对路径；公开项目不得提交该私有资产。命令行独立使用 renderer 时，也可通过 `SKETCHNARRATOR_PRESENTER_MANIFEST` 指定同一文件。manifest 至少提供 `assets.drawing` 与 `assets.eraser` 的 `runtime_file`、归一化 `anchor` 和 `height_policy`；开场结尾启用时再提供 `assets.intro/outro`。

### animation-plan.json

```json
{
  "version": 3,
  "planVersion": "3.3",
  "timebase": "absolute-word-ms",
  "scenes": [
    {
      "sceneId": "scene-01",
      "sceneStartMs": 0,
      "sceneEndMs": 6800,
      "composition": "causal-chain",
      "events": [
        {
          "targetElementId": "cause",
          "effect": "focus-push",
          "semanticIntent": "focus",
          "semanticGoal": "提示已经完整绘制的核心结论",
          "startState": "cause 已完整绘制",
          "action": "向完整对象缓慢推近 5%，保持稳定画面",
          "endState": "强调结束后恢复稳定画面",
          "staticAnchors": [],
          "focusTarget": {
            "x": 420, "y": 120, "width": 380, "height": 300,
            "center": [610, 270], "confidence": 0.91,
            "source": "foreground-pixels", "subtitleSafeTopPx": 790
          },
          "triggerText": "所以价格上涨",
          "wordStartMs": 120,
          "wordEndMs": 1820,
          "startMs": 2200,
          "endMs": 3600,
          "persistUntilMs": 3900,
          "timeBudgetMs": 1400,
          "phase": {
            "prepare": {"startMs": 2200, "endMs": 2300},
            "camera": {"startMs": 2300, "endMs": 2800},
            "hold": {"startMs": 2800, "endMs": 3520},
            "leave": {"startMs": 3520, "endMs": 3600}
          },
          "fallbackPolicy": "stable-hold",
          "targetMaskSource": "foreground-pixels-within-focusTarget",
          "effectOptions": {"preserveOriginalPixels": true, "inventGeometry": false},
          "intensity": 0.05,
          "handBehavior": {"hideDuringCamera": true, "showHandForEffect": false},
          "auxiliary": false
        }
      ],
      "transition": null
    }
  ],
  "transitions": []
}
```

`triggerText` 优先匹配实际词语区间，事件的 `startMs`/`endMs` 是相对于本幕的渲染时间。V3.3 每幕最多一个安全语义后动画或空事件；没有可靠语义、真实前景目标或足够时间时通过 `skipped` 记录原因并保持稳定画面。所有事件都不得带自动 `gesturePath`/`path`，不得凭空生成几何图形；板擦开始前停止其他动画。

后动画需要释放口播内窗口时，`timingReservations[]` 保存原始与有效时长及
`phaseBudgetPolicy: protected-phases-v3`；只为最终选中的效果保留预算。
原始 annotation 不变，执行副本通过共享编译器生成 `reveal.frameBudget`，其中包含
`fps/startFrame/totalFrames/originalFrames/baseline/phases`。识别和基础色与原始
基线逐帧相等，只有纹理和超过必要退场帧数的收尾可以缩短；renderer profile 保存 `actualPhases` 与
`actualFrames`，QA 逐对象对照。计划生成或编辑后必须重新确认第三道关卡。
不能把登记成功、总帧数相等或保护字段存在当作已经遵守阶段预算。

非最后一幕的 `transition` 使用全片绝对逐词时间，记录前一幕最后词结束、下一幕首词开始、完整画面稳定、擦除和干净画布区间。相同转场会同时出现在该幕的 `scenes[].transition` 和顶层 `transitions[]`，两份必须逐字段一致。擦除建议 240–420ms、净板至少 60ms，两者合计不超过 500ms；真实停顿更长时延长完整画面保持，把擦除贴近下一句开始。可用停顿不足约 300ms 时在脚本/正式配音阶段明确报错，不得静默延长成片。

### V3.1 legacy 手势事件（仅旧项目兼容或显式 opt-in）

新事件由工作流从整板图的真实前景像素自动生成，用户不手写坐标：

```json
{
  "targetElementId": "near-room",
  "focusTarget": {
    "x": 1140, "y": 120, "width": 420, "height": 500,
    "center": [1350, 370], "confidence": 0.91,
    "source": "foreground-pixels", "safeMarginPx": 20,
    "subtitleSafeTopPx": 790
  },
  "gesturePath": [[1118, 130], [1118, 620], [1140, 620]],
  "gesturePathSpace": "annotation-pixels",
  "effect": "gesture-bracket",
  "timeBudgetMs": 1680,
  "phase": {
    "prepare": {"startMs": 10100, "endMs": 10200},
    "draw": {"startMs": 10200, "endMs": 10470},
    "hold": {"startMs": 10470, "endMs": 10590},
    "leave": {"startMs": 10590, "endMs": 10670}
  },
  "fallback": null,
  "style": {
    "colorSource": "style-registry:warm-pencil.gesture_colors",
    "colorHex": "#8A6A52",
    "lineWidthShortEdgeRatio": 0.0036
  },
  "handBehavior": {
    "mode": "small-hand",
    "prepare": "lift-pen-move-to-start",
    "draw": "drop-pen-follow-path",
    "hold": "keep-ink-and-tip-at-end",
    "leave": "lift-pen-move-away",
    "hideDuringCamera": true
  },
  "upstreamVisualBeatId": "scene-02-shot-01-beat-03",
  "triggerPhraseId": "w-0012"
}
```

新计划仅在明确聚焦且预算充足时生成无路径 `focus-push`。V3.1 legacy 的 `gesture-*` 和机械效果仅保留历史读取兼容，不由新工作流生成。legacy 手绘事件仍遵守真实时间、路径边界和字幕安全区。

## 状态与 QA

渲染完成但尚未实际看图时，状态为 `await-final-qa`，不能交付；自动 QA 失败时进入 `fix-required`，修复失败层后允许增量重渲染。`qa` 保存自动报告和当前成片哈希；`accept-qa` 记录实际看图结论。只有以下条件同时满足才进入 `complete`：

- `final_current` 为真且 `final.mp4` 存在。
- 自动 QA 为通过。
- 实际看图已接受。
- QA 报告和 `state.json` 指向同一个最终文件哈希。
- QA 报告版本、报告文件哈希和当前验收契约仍然有效；旧规则生成的历史报告不能继续维持 `complete`。

改变脚本、配音、整板图或标注时清空 QA 状态。重新渲染前把旧成片复制到 `deliverables/archive/`。

新计划禁用局部轮廓、染色高亮与流光；历史结构仅供兼容读取，不是新生产的默认方案。新图不生成箭头和分隔线，采用不可见布局与独立对象。阶段预算按 timing-and-recovery.md 的当前版本执行。
