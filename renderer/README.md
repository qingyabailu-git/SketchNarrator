# 内置白板渲染器

这个目录是 SketchNarrator 的内部运行组件，负责分区遮罩、连续笔迹、分层绘制、统一上色、通用手部、板擦转场和安全后动画。用户应从仓库根目录的 `SKILL.md` 启动完整工作流，不需要单独调用或安装这里的代码。

原项目说明保存在 `UPSTREAM_README.md`，MIT 许可和第三方运行时说明分别保存在 `LICENSE` 与 `THIRD_PARTY_NOTICES.md`。

公开仓库不包含任何 Presenter/IP 资产。用户可把自有资产包放在 `assets/presenter.json`，或用 `SKETCHNARRATOR_PRESENTER_MANIFEST` 指向其 manifest。默认使用通用 `small-hand`。
