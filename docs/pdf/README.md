# PDF 文档

这里仅保存需要直接分发的当前 PDF。历史 PDF 已移至 [`../archive/pdf/`](../archive/pdf/)，不得再作为项目现状引用。

## 当前文档

- [M-Agent v0.2.0—v1.0.0 版本规划](M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf)

可编辑源文件为 [`../roadmap-v0.2.0-v1.0.0.zh-CN.md`](../roadmap-v0.2.0-v1.0.0.zh-CN.md)。Markdown 是权威源，PDF 是便于评审和分发的生成产物；二者内容变更时必须同步更新。

## 可复现生成

在仓库根目录执行：

```powershell
& 'F:\ProgramData\miniconda3\python.exe' -B `
  scripts/export_markdown_pdf.py `
  docs/roadmap-v0.2.0-v1.0.0.zh-CN.md `
  --annotated `
  --title "M-Agent v0.2.0—v1.0.0 版本规划" `
  --pdf-out docs/pdf/M-Agent-Roadmap-v0.2.0-v1.0.0.zh-CN.pdf `
  --require-text "Stimulus-Native Cognitive Runtime" `
  --require-text "System Memory & Temporal Runtime" `
  --require-text "Stable Stimulus-Native Cognitive Runtime" `
  --forbid-text "memory-centric"
```

导出器使用系统 Edge/Chrome 的无头打印，并检查 PDF 可打开、正文可提取、关键文字存在、过时文字不存在，以及页眉页脚没有泄露本地绝对路径。

正式分发前还应抽查首页、版本总览、系统记忆与 Strategy 表格以及末页，确认中文字体、表格分页和页面裁切正常。
