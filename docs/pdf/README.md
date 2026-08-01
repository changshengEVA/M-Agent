# PDF 文档

这里仅保存需要直接分发的 PDF；可编辑源文件位于 [`../architecture/`](../architecture/README.md)。

- [总体设计架构（重点标注版）](overall-design-architecture.annotated.zh-CN.pdf)
- [当前项目进度与设计计划（重点标注版）](current-project-progress-and-design-plan.annotated.zh-CN.pdf)
- [生产 Runtime 层实施计划（重点标注版）](production-runtime-layer-plan.annotated.zh-CN.pdf)

PDF 内容更新后，应与对应 Markdown 源文件一起重新生成，避免分发过期版本。

## 可复现生成

在仓库根目录使用项目 Python：

```powershell
python scripts/export_markdown_pdf.py `
  docs/architecture/overall-design-architecture.md `
  --annotated `
  --title "M-Agent 总体设计架构" `
  --pdf-out docs/pdf/overall-design-architecture.annotated.zh-CN.pdf `
  --require-text "Overall Design Architecture"

python scripts/export_markdown_pdf.py `
  docs/architecture/current-project-progress-and-design-plan.md `
  --annotated `
  --title "M-Agent 当前项目进度与设计计划" `
  --pdf-out docs/pdf/current-project-progress-and-design-plan.annotated.zh-CN.pdf `
  --require-text "P8 灰度与 LangGraph 完整矩阵已完成" `
  --require-text "38/38" `
  --require-text "旧 Supporting 目录的 Known Gap（剩余 2 个）" `
  --forbid-text "下一步进入 P8 灰度与 LangGraph 完整矩阵" `
  --forbid-text "LangGraph 完整矩阵仍待 P8" `
  --forbid-text "旧 Supporting 目录的 6 个 Known Gap"

python scripts/export_markdown_pdf.py `
  docs/architecture/production-runtime-layer-plan.zh-CN.md `
  --annotated `
  --title "M-Agent 生产 Runtime 层实施计划" `
  --pdf-out docs/pdf/production-runtime-layer-plan.annotated.zh-CN.pdf `
  --require-text "P8 后 · 生产 Runtime 接线期" `
  --require-text "RuntimeHost" `
  --require-text "R2 graph 内完整循环" `
  --require-text "smoke_langgraph_turn_loop" `
  --forbid-text "LangGraph 完整矩阵仍待 P8" `
  --forbid-text "MVP 仅 record_progress 占位"
```

导出器使用系统 Edge/Chrome 的无头打印，默认不保留中间 HTML，并会检查：

- PDF 可打开且含可提取正文；
- 重点状态文字存在；
- 页眉页脚没有 `file:///` 或仓库绝对路径；
- 调用方指定的过时文字不再出现。

人工交付前还应抽查首页、重点结论页、阶段表和末页，确认中文字体、表格分页及色彩正常。
