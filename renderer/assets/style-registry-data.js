// Deterministically generated from references/style-registry.json
// Source SHA256: 1e567229ca6cb1716eb0770b1964ed51886a00915deef83e1ed133f95cf73ad2
(function(global) {
  'use strict';
  global.SKETCH_STYLE_REGISTRY_METADATA = {
    sourceSha256: "1e567229ca6cb1716eb0770b1964ed51886a00915deef83e1ed133f95cf73ad2",
    stylesCount: 10,
    data: {
  "version": 5,
  "visual_safe_frame": {
    "left": 0.04,
    "top": 0.04,
    "right": 0.96,
    "bottom": 0.82,
    "mode": "uniform-fit-no-crop"
  },
  "animation_policy": {
    "default_plan_version": "3.3",
    "default_effects": [
      "focus-push"
    ],
    "max_semantic_effects_per_scene": 1,
    "default_overlay_paths": false,
    "invent_geometry": false,
    "legacy_effects": [
      "gesture-arc",
      "gesture-underline",
      "gesture-bracket",
      "gesture-arrow",
      "pulse",
      "pulse/outline",
      "outline",
      "hand-drawn-circle",
      "circle",
      "focus-zoom"
    ],
    "legacy_note": "gesture_colors 只保存 focus-push 当前配色与旧计划手势的兼容色；新计划不生成额外路径、箭头、局部轮廓高亮、染色高亮或流光"
  },
  "hand_modes": {
    "small-hand": "默认按输出画面短边比例缩放，推荐 0.14",
    "presenter": "通用角色抱笔与推板擦覆盖层，读取用户自有 presenter.json",
    "no-hand": "完全不显示手部覆盖",
    "full-hand": "兼容旧大手高度策略",
    "bare-tip": "历史兼容别名，实际关闭手部覆盖"
  },
  "styles": [
    {
      "id": "warm-pencil",
      "name": "暖米黄素描白板",
      "aliases": [
        "暖米黄",
        "暖米黄色纸张白板手绘",
        "warm",
        "paper"
      ],
      "intended_use": "知识解释、故事口播、温和科普",
      "reference_image": "assets/style-references/warm-pencil/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#F5EBD7",
        "background_luma_range": [
          210,
          252
        ],
        "background_rgb_tolerance": 48,
        "palette": [
          "#F5EBD7",
          "#3E4145",
          "#C85B4A",
          "#D9A65A",
          "#4E7598"
        ],
        "line_material": "graphite-pencil",
        "line_weight": "medium-varied",
        "fill": "soft-local-color-pencil",
        "texture": "very-light-warm-paper-grain",
        "compositions": [
          "center-spoke",
          "causal-chain",
          "character-action"
        ]
      },
      "prompt": {
        "background": "统一暖米黄纸张 #F5EBD7，纹理极轻",
        "line": "深灰铅笔线，轮廓清楚，允许少量排线",
        "accents": "低饱和红、橙、蓝，仅用于关键关系",
        "forbid": "文字、数字、Logo、摄影、3D、复杂纹理和大面积渐变"
      },
      "gesture_colors": {
        "gesture-arc": "#4E7598",
        "gesture-underline": "#C77D3C",
        "gesture-bracket": "#8A6A52",
        "gesture-arrow": "#C85B4A",
        "focus-push": "#4E7598",
        "default": "#4E7598"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.32,
        "minimum_color_ms": 900
      }
    },
    {
      "id": "minimal-whiteboard",
      "name": "极简粗线简笔白板",
      "aliases": [
        "极简白板",
        "简笔白板",
        "minimal"
      ],
      "intended_use": "快节奏概念解释、工具介绍、流程说明",
      "reference_image": "assets/style-references/minimal-whiteboard/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#F6F4EF",
        "background_luma_range": [
          224,
          255
        ],
        "background_rgb_tolerance": 38,
        "palette": [
          "#F6F4EF",
          "#343A40",
          "#557A9E",
          "#E0AD63"
        ],
        "line_material": "clean-marker",
        "line_weight": "bold-uniform",
        "fill": "sparse-flat-accent",
        "texture": "none-or-nearly-none",
        "compositions": [
          "vertical-layers",
          "before-after",
          "center-spoke"
        ]
      },
      "prompt": {
        "background": "统一柔和白灰底 #F6F4EF，无边框",
        "line": "粗而稳定的深灰简笔轮廓，细节克制",
        "accents": "蓝和橙各一种低饱和强调色",
        "forbid": "文字、数字、Logo、写实阴影、3D 和密集背景"
      },
      "gesture_colors": {
        "gesture-arc": "#557A9E",
        "gesture-underline": "#D1843E",
        "gesture-bracket": "#6C7076",
        "gesture-arrow": "#C85B4A",
        "focus-push": "#557A9E",
        "default": "#557A9E"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.28,
        "minimum_color_ms": 720
      }
    },
    {
      "id": "orderly-color-doodle",
      "name": "规整彩线手绘",
      "aliases": [
        "规整手绘",
        "彩线手绘",
        "规整涂鸦",
        "orderly doodle",
        "color doodle"
      ],
      "intended_use": "开源项目、软件工具、产品机制、轻松科技科普和团队协作",
      "reference_image": "assets/style-references/orderly-color-doodle/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#F8F7F2",
        "background_luma_range": [
          226,
          255
        ],
        "background_rgb_tolerance": 34,
        "palette": [
          "#F8F7F2",
          "#262A2E",
          "#356AE6",
          "#43A85B",
          "#E6574F",
          "#F2C84B"
        ],
        "line_material": "confident-felt-tip-marker",
        "line_weight": "medium-organic-controlled-varied",
        "fill": "selective-clean-opaque-flat-color",
        "texture": "none-or-nearly-none",
        "compositions": [
          "causal-chain",
          "center-spoke",
          "before-after",
          "vertical-layers",
          "character-action"
        ]
      },
      "prompt": {
        "background": "统一柔和暖白纸底 #F8F7F2，纹理极轻，底部保留字幕安全留白",
        "line": "炭黑毡尖笔一笔成形，线宽保持同一范围但有真实压感和轻微自然抖动；人物比例、表情和动作保留画手个性，主要对象沿宽松基线和阅读路径组织",
        "accents": "钴蓝和清新绿为主，珊瑚红与暖黄只用于少量语义强调；颜色块直接、不透明、均匀平涂，不把所有对象填满",
        "forbid": "文字、字母、数字、Logo、水印、居中机器人、成套 UI 卡片、矢量化统一线宽、数学对称模板、彩铅排线、蜡笔颗粒、渐变、光影、凌乱涂鸦堆、歪斜阅读关系、拥挤装饰、摄影和 3D"
      },
      "gesture_colors": {
        "gesture-arc": "#356AE6",
        "gesture-underline": "#E6574F",
        "gesture-bracket": "#262A2E",
        "gesture-arrow": "#43A85B",
        "focus-push": "#356AE6",
        "default": "#356AE6"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.34,
        "minimum_color_ms": 860
      }
    },
    {
      "id": "business-doodle",
      "name": "极简商务涂鸦",
      "aliases": [
        "商务涂鸦",
        "business"
      ],
      "intended_use": "商业逻辑、产品机制、数据关系",
      "reference_image": "assets/style-references/business-doodle/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#F2F3EF",
        "background_luma_range": [
          218,
          255
        ],
        "background_rgb_tolerance": 42,
        "palette": [
          "#F2F3EF",
          "#354A54",
          "#426A72",
          "#D0A15B",
          "#B85A4D"
        ],
        "line_material": "navy-gray-doodle-pen",
        "line_weight": "medium-clean-geometric",
        "fill": "restrained-flat-business-color",
        "texture": "subtle-paper-only",
        "compositions": [
          "before-after",
          "causal-chain",
          "vertical-layers"
        ]
      },
      "prompt": {
        "background": "统一浅灰米白底 #F2F3EF",
        "line": "深蓝灰几何线稿，人物和图表保持简洁",
        "accents": "克制蓝绿配色，橙色只标关键变化",
        "forbid": "文字、数字、Logo、拟真材质、密集网格和复杂装饰"
      },
      "gesture_colors": {
        "gesture-arc": "#426A72",
        "gesture-underline": "#C4763B",
        "gesture-bracket": "#5F6870",
        "gesture-arrow": "#B85A4D",
        "focus-push": "#426A72",
        "default": "#426A72"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.3,
        "minimum_color_ms": 800
      }
    },
    {
      "id": "dark-chalkboard",
      "name": "深墨绿粉笔黑板",
      "aliases": [
        "黑板",
        "粉笔",
        "chalkboard"
      ],
      "intended_use": "课程讲解、公式关系、课堂感科普",
      "reference_image": "assets/style-references/dark-chalkboard/reference.png",
      "render_mode": "dark",
      "visual_contract": {
        "background_hex": "#173E38",
        "background_luma_range": [
          22,
          92
        ],
        "background_rgb_tolerance": 55,
        "palette": [
          "#173E38",
          "#F3EBD3",
          "#A8D2D1",
          "#E7C979",
          "#E28E78"
        ],
        "line_material": "dry-chalk",
        "line_weight": "medium-grainy",
        "fill": "broken-chalk-hatching",
        "texture": "clean-board-with-light-chalk-dust",
        "compositions": [
          "center-spoke",
          "causal-chain",
          "vertical-layers"
        ]
      },
      "prompt": {
        "background": "统一深墨绿色黑板 #173E38，表面干净",
        "line": "米白粉笔主线，边缘略有粉笔颗粒",
        "accents": "浅黄、浅蓝、浅红少量点缀",
        "forbid": "源图文字、数字、Logo、霓虹光、摄影和复杂黑板污迹"
      },
      "gesture_colors": {
        "gesture-arc": "#A8D2D1",
        "gesture-underline": "#E7C979",
        "gesture-bracket": "#D5D2C5",
        "gesture-arrow": "#E28E78",
        "focus-push": "#A8D2D1",
        "default": "#A8D2D1"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.34,
        "minimum_color_ms": 960
      }
    },
    {
      "id": "guofeng-flat",
      "name": "粗线扁平国风卡通",
      "aliases": [
        "国风",
        "国风卡通",
        "guofeng"
      ],
      "intended_use": "传统文化、历史故事、国风知识解释",
      "reference_image": "assets/style-references/guofeng-flat/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#F3E5C8",
        "background_luma_range": [
          202,
          250
        ],
        "background_rgb_tolerance": 52,
        "palette": [
          "#F3E5C8",
          "#4D392B",
          "#B84D3F",
          "#507A76",
          "#425C86"
        ],
        "line_material": "dark-brown-ink-brush",
        "line_weight": "bold-flat",
        "fill": "flat-muted-guofeng-color",
        "texture": "light-xuan-paper",
        "compositions": [
          "character-action",
          "vertical-layers",
          "before-after"
        ]
      },
      "prompt": {
        "background": "统一浅宣纸底 #F3E5C8，无文字纹样",
        "line": "深棕粗线，造型扁平，人物比例卡通化",
        "accents": "朱红、玉绿、靛蓝小面积平涂",
        "forbid": "文字、印章、Logo、复杂纹样、写实古画和厚重渐变"
      },
      "gesture_colors": {
        "gesture-arc": "#507A76",
        "gesture-underline": "#B85B42",
        "gesture-bracket": "#806148",
        "gesture-arrow": "#B84D3F",
        "focus-push": "#507A76",
        "default": "#507A76"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.34,
        "minimum_color_ms": 960
      }
    },
    {
      "id": "comic-ink",
      "name": "漫画墨线解释",
      "aliases": [
        "漫画",
        "墨线",
        "comic"
      ],
      "intended_use": "冲突、机制拆解、反转型短视频",
      "reference_image": "assets/style-references/comic-ink/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#EFE9DD",
        "background_luma_range": [
          205,
          250
        ],
        "background_rgb_tolerance": 46,
        "palette": [
          "#EFE9DD",
          "#202020",
          "#C54C42",
          "#E2B34F",
          "#3F6485"
        ],
        "line_material": "expressive-comic-ink",
        "line_weight": "bold-varied",
        "fill": "selective-flat-comic-color",
        "texture": "light-ink-hatching",
        "compositions": [
          "before-after",
          "character-action",
          "causal-chain"
        ]
      },
      "prompt": {
        "background": "统一暖灰纸底 #EFE9DD",
        "line": "黑色漫画墨线，动作清楚，允许少量速度线",
        "accents": "红色和黄色只强调冲突或结果",
        "forbid": "文字、拟声词、数字、Logo、密集网点、摄影和 3D"
      },
      "gesture_colors": {
        "gesture-arc": "#3F6485",
        "gesture-underline": "#D08C3A",
        "gesture-bracket": "#555555",
        "gesture-arrow": "#C54C42",
        "focus-push": "#3F6485",
        "default": "#3F6485"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.3,
        "minimum_color_ms": 800
      }
    },
    {
      "id": "paper-metaphor-collage",
      "name": "纸感隐喻拼贴",
      "aliases": [
        "纸感拼贴",
        "隐喻拼贴",
        "paper collage",
        "metaphor collage"
      ],
      "intended_use": "抽象概念、选择权衡、因果与层级关系",
      "reference_image": "assets/style-references/paper-metaphor-collage/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#EEDFC8",
        "background_luma_range": [
          190,
          245
        ],
        "background_rgb_tolerance": 58,
        "palette": [
          "#EEDFC8",
          "#2E2A27",
          "#6E6861",
          "#C9685B",
          "#B9857C",
          "#D6A43D"
        ],
        "line_material": "hand-cut-paper-edge",
        "line_weight": "thin-charcoal-outline",
        "fill": "layered-paper-cutout",
        "texture": "visible-paper-fibre-torn-edge-and-soft-layer-shadow",
        "compositions": [
          "center-spoke",
          "vertical-layers",
          "before-after",
          "causal-chain",
          "timeline"
        ]
      },
      "prompt": {
        "background": "统一暖米白手工纸 #EEDFC8，保留清晰纸纤维和轻微褶皱",
        "line": "炭黑细轮廓与手工裁切撕边，主体由 1–3 组叠层剪纸构成",
        "accents": "珊瑚红、灰粉和暖灰；金黄只用于价值、希望或关键转折",
        "forbid": "文字、数字、Logo、摄影、塑料 3D、儿童贴纸、霓虹 UI、图标堆砌和逐句图标化"
      },
      "gesture_colors": {
        "focus-push": "#6E6861",
        "default": "#6E6861"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.38,
        "minimum_color_ms": 980
      }
    },
    {
      "id": "retro-newspaper",
      "name": "复古报纸拼贴",
      "aliases": [
        "复古报纸",
        "报纸拼贴",
        "retro newspaper"
      ],
      "intended_use": "历史回顾、档案故事、媒体事件和观点梳理",
      "reference_image": "assets/style-references/retro-newspaper/reference.png",
      "render_mode": "light",
      "visual_contract": {
        "background_hex": "#DCCFB5",
        "background_luma_range": [
          170,
          230
        ],
        "background_rgb_tolerance": 62,
        "palette": [
          "#DCCFB5",
          "#201E1A",
          "#8F2F2B",
          "#B8AA8D"
        ],
        "line_material": "rough-black-newsprint-ink",
        "line_weight": "bold-editorial-cutout",
        "fill": "black-ink-red-accent-and-halftone",
        "texture": "newsprint-grain-screenprint-and-torn-paper",
        "compositions": [
          "timeline",
          "before-after",
          "vertical-layers",
          "center-spoke"
        ]
      },
      "prompt": {
        "background": "统一暖灰旧新闻纸 #DCCFB5，保留油墨颗粒和纸张纤维",
        "line": "粗粝黑色油墨轮廓，剪贴边缘清楚，允许克制半色调网点",
        "accents": "复古暗红只标关键事件，其他以黑灰和新闻纸色为主",
        "forbid": "文字、日期、数字、Logo、真实报头、来源不明照片、光滑渐变和现代扁平 UI"
      },
      "gesture_colors": {
        "focus-push": "#55504A",
        "default": "#55504A"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.3,
        "minimum_color_ms": 820
      }
    },
    {
      "id": "black-gold-tech",
      "name": "黑金科技发布会",
      "aliases": [
        "黑金科技",
        "科技发布会",
        "black gold",
        "tech keynote"
      ],
      "intended_use": "AI、芯片、未来科技、产品发布和高端机制展示",
      "reference_image": "assets/style-references/black-gold-tech/reference.png",
      "render_mode": "dark",
      "visual_contract": {
        "background_hex": "#111318",
        "background_luma_range": [
          8,
          55
        ],
        "background_rgb_tolerance": 52,
        "palette": [
          "#111318",
          "#D2A84A",
          "#F2D58A",
          "#4FD3D8",
          "#353B45"
        ],
        "line_material": "precise-metallic-gold-line",
        "line_weight": "fine-to-medium-geometric",
        "fill": "dark-layered-metallic-accent",
        "texture": "subtle-charcoal-stage-grain",
        "compositions": [
          "center-spoke",
          "causal-chain",
          "vertical-layers",
          "timeline"
        ]
      },
      "prompt": {
        "background": "统一深黑炭灰舞台 #111318，避免大面积渐变和发光雾",
        "line": "金属金主轮廓，几何结构精确但保持可绘制，少量电光青辅助",
        "accents": "金色表达核心与价值，青色只表达数据或能量流",
        "forbid": "文字、数字、Logo、摄影、复杂 UI、霓虹泛光、玻璃拟态和不可绘制的 3D 光效"
      },
      "gesture_colors": {
        "focus-push": "#F2D58A",
        "default": "#F2D58A"
      },
      "renderer": {
        "ink_path": "skeleton",
        "stroke_planner": "semantic-v2",
        "color_fill": "local-brush",
        "color_schedule": "object-progressive-v1",
        "hand_mode": "small-hand",
        "draw_mode": "layered",
        "color_reserve_ratio": 0.36,
        "minimum_color_ms": 960
      }
    }
  ]
}
  };
})(typeof window !== 'undefined' ? window : this);
