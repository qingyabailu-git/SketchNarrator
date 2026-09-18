# Changelog

## 0.1.4 - 2026-09-18

- Fix compact ink stroke erosion during Zhang-Suen skeletonization: recover isolated compact ink components (such as eye pupils, dots, and small facial features) via `_recover_compact_ink_strokes` and promote core short strokes to `short_identity` in semantic stroke planning.
- Add `ink_color_mode` (default `"source"`, fallback `"monochrome"`): sample original artwork RGB colors for line drawing in light whiteboard mode, faithfully preserving golden sparks, red warnings, green neural paths, and blue accents instead of forcing monochrome thresholding.
- Integrate `ink_color_mode` across `stream_render.py`, `render_stream_whiteboard.py`, and `workflow.py` render pipelines with deterministic cache invalidation.

## 0.1.3 - 2026-09-17

- Add an explicit opening-anchor beat to every visual shot. The first visible element now starts about 100ms after the scene begins instead of waiting for a later semantic trigger word.
- Recommend opening strokes within 200ms and reject plans or annotations whose first visible element starts more than 500ms after the scene begins.
- Mirror the opening-anchor check in board QA, including a non-blocking advisory between 200ms and 500ms and a hard error beyond 500ms.
- Document the opening-anchor contract across the skill entrypoint, visual-production guide, final QA guide, and project file contract.

## 0.1.2 - 2026-09-17

- Add adaptive duration pacing solver (`scripts/pacing.py` and `workflow.py pace-annotations`): dynamically stretch element drawing durations across voiceover intervals (default 72% drawing + 28% hold) to eliminate rushed 1.2s bursts and long dead pauses.
- Add idle pacing advisory warning to `board_qa.py` when element drawing duration is disproportionately short compared to available voiceover window.
- Promote optional per-scene title cards into the storyboard, Gate 2 confirmation, visual plan, render cache, panel preview, and final scene renderer. External private settings may require a card for every scene without embedding private values in the public package.

## 0.1.1 - 2026-09-15

- Separate isolated scene diagnostics from whole-timeline coverage validation so multi-scene Gate 2 compilation preserves global word anchors without falsely requiring every scene to cover the complete narration.

## 0.1.0 - 2026-09-14

### 2026-09-13

- Compile safe storyboard draft fields before staging and return all independent schema and semantic timing problems together.
- Treat draw-order-only workbench changes as a third-gate edit while semantic object or narration-binding changes return to the second gate.
- Expose a machine-readable next command for each project stage and bind board approval to the displayed visual-risk snapshot.
- Report likely object cuts by object and region side while keeping all visual findings advisory after explicit render approval.
- Resolve SFX and external media configuration before scene rendering; `--no-sfx` now bypasses machine-local SFX settings.
- Persist each successful scene render immediately so later merge, caption, or audio failures do not discard reusable work.
- Analyze full-reference videos in one sequential decode pass with bounded motion samples instead of thousands of random seeks.
- Separate board registration from project planning and report scene-specific ownership diagnostics.
- Preserve editable selections, compile deterministic masks, and honor user-confirmed visual quality without repeated pixel vetoes.
- Stop generating connector lines and local contour/wash/flash effects; retain legacy effect readers.
- Protect hand-release frames and calculate recognition ratios against the original drawing budget.
- Probe optional bookends with PyAV when FFprobe is absent; keep probing bounded to metadata.
- Trim every optional bookend segment to one video-led timeline and cap the final audio and video to the same target duration.
- Remove product-specific Presenter aliases from the public package; custom identities now enter only through the generic external Presenter protocol.
- Add a deterministic manifest-based release packager and a public, identity-neutral local-settings example.
- Synchronize affected contract tests with the confirmed save, approval, timing, and compatibility behavior.

- Made scene elements adaptive (1–6), initially used connector semantics and nearest-foreground ownership; these historical policies are superseded by the 2026-09-13 confirmed-mask workflow.
- Added the `orderly-color-doodle` style: human felt-tip contours, direct opaque flat fills, orderly reading paths, controlled blue/green/coral/yellow accents, and a first-party 16:9 reference image.
- Added an explicit transcript-only versus full-reference-video intake choice, plus an OpenMontage-inspired local analyzer for scene cuts, pacing, motion, keyframes, and contact sheets.
- Added a real three-confirmation state machine for script/style, voice/storyboard, and boards/render order.
- Added explicit optional provider adapters for Piper, Azure Speech, ElevenLabs, Sherpa-ONNX, and VoiceStudio while keeping Edge as the default.
- Added a public alignment command for providers without reliable native word timing.
- Hardened first-run environment verification, optional dependency consent, release packaging, and the localhost composition panel.
- Unified cross-platform CJK font discovery and FFmpeg/FFprobe runtime discovery.
- Added public repository documentation, release-manifest checks, and a multi-platform CI matrix.
