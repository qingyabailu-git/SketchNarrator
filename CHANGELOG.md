# Changelog

## Unreleased

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
