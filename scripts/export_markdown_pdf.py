from __future__ import annotations

import argparse
from datetime import date
from html import escape
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import markdown


def resolve_browser_paths() -> list[Path]:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]
    browsers = [path for path in candidates if path.exists()]
    if browsers:
        return browsers
    raise FileNotFoundError("No Edge/Chrome executable found in standard install paths.")


def _strip_local_link_targets(body: str) -> str:
    pattern = re.compile(
        r'<a href="([^"]+)"([^>]*)>(.*?)</a>',
        flags=re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        href, attributes, label = match.groups()
        if href.startswith(("https://", "http://", "mailto:")):
            return f'<a href="{href}"{attributes}>{label}</a>'
        return f'<span class="local-link">{label}</span>'

    return pattern.sub(replace, body)


def render_html(
    md_text: str,
    title: str,
    *,
    annotated: bool = False,
    source_label: str = "",
) -> str:
    body = markdown.markdown(md_text, extensions=["extra", "sane_lists", "nl2br"])
    body = _strip_local_link_targets(body)
    body_class = "annotated" if annotated else "plain"
    banner = ""
    if annotated:
        banner = (
            '<section class="annotation-banner">'
            '<div class="eyebrow">重点标注版 · ANNOTATED EDITION</div>'
            f"<div class=\"document-title\">{escape(title)}</div>"
            '<div class="annotation-note">'
            "蓝色章节条用于定位结构；黄色提示框表示结论、边界或风险；"
            "表格中的加粗项是当前决策重点。"
            "</div>"
            f'<div class="source-label">源文档：{escape(source_label)}'
            f" · 状态日期：{date.today().isoformat()}</div>"
            "</section>"
        )
    css = """
    <style>
    :root{--ink:#172033;--muted:#64748b;--blue:#155eef;--blue-soft:#edf4ff;
      --yellow:#fff5cc;--yellow-line:#e9b949;--line:#d8e0eb;--panel:#f7f9fc}
    @page{size:A4;margin:14mm 13mm 16mm}
    *{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
    html{background:#fff}
    body{font-family:"Microsoft YaHei","Noto Sans CJK SC","Segoe UI",Arial,sans-serif;
      line-height:1.62;color:var(--ink);font-size:10.4pt;margin:0}
    h1,h2,h3,h4{line-height:1.3;page-break-after:avoid;break-after:avoid}
    h1{font-size:23pt;margin:0 0 18px;color:#102a56}
    h2{font-size:16pt;margin:28px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}
    h3{font-size:12.5pt;margin:20px 0 8px;color:#24456e}
    h4{font-size:11pt;margin:16px 0 7px}
    p{margin:7px 0}
    ul,ol{padding-left:1.45em;margin:7px 0}
    li{margin:3px 0}
    strong{color:#102a56}
    code{font-family:"Cascadia Mono",Consolas,monospace;background:#eef2f7;
      padding:1px 4px;border-radius:4px;font-size:.9em;overflow-wrap:anywhere}
    pre{background:#101828;color:#edf4ff;padding:11px 13px;border-radius:8px;
      overflow-wrap:anywhere;white-space:pre-wrap;page-break-inside:avoid}
    pre code{background:transparent;color:inherit;padding:0}
    table{border-collapse:collapse;width:100%;margin:11px 0 16px;font-size:9.2pt}
    thead{display:table-header-group}
    tr{page-break-inside:avoid}
    th,td{border:1px solid var(--line);padding:6px 7px;vertical-align:top}
    th{background:#eaf1fb;color:#17345e;text-align:left}
    tbody tr:nth-child(even){background:#fafbfd}
    blockquote{margin:12px 0;padding:10px 13px;border-left:5px solid var(--yellow-line);
      background:var(--yellow);border-radius:3px;page-break-inside:avoid}
    blockquote p{margin:2px 0}
    hr{border:none;border-top:1px solid var(--line);margin:22px 0}
    a{color:#155eef;text-decoration:none}
    .local-link{color:#234b7c;border-bottom:1px dotted #8aa6c8}
    .annotation-banner{margin:0 0 22px;padding:17px 19px;border:1px solid #b9cdf4;
      border-left:7px solid var(--blue);border-radius:8px;background:var(--blue-soft);
      page-break-inside:avoid}
    .eyebrow{font-size:8.5pt;letter-spacing:.08em;font-weight:700;color:var(--blue)}
    .document-title{font-size:20pt;font-weight:750;line-height:1.25;margin:6px 0;color:#102a56}
    .annotation-note{font-size:9.5pt;color:#334e75}
    .source-label{font-size:8.2pt;color:var(--muted);margin-top:8px}
    .annotated h2{border-left:6px solid var(--blue);border-bottom:none;
      padding:8px 11px;background:linear-gradient(90deg,#edf4ff 0,#f8fbff 72%,#fff 100%);
      color:#123a72;border-radius:3px}
    .annotated h3{border-left:3px solid #8eb1ef;padding-left:8px}
    .annotated table strong{background:#fff0a6;padding:0 2px;border-radius:2px}
    @media screen{body{max-width:980px;margin:28px auto;padding:0 24px}}
    @media print{body{max-width:none}.annotation-banner{margin-top:0}}
    </style>
    """
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='color-scheme' content='light'>"
        f"<title>{escape(title)}</title>{css}</head>"
        f"<body class='{body_class}'>{banner}{body}</body></html>"
    )


def validate_pdf(
    pdf_path: Path,
    *,
    required_text: list[str],
    forbidden_text: list[str],
) -> None:
    import fitz

    with fitz.open(pdf_path) as document:
        if document.page_count < 1:
            raise RuntimeError("generated PDF has no pages")
        text = "\n".join(page.get_text() for page in document)
    if len(text.strip()) < 100:
        raise RuntimeError("generated PDF contains too little extractable text")
    missing = [item for item in required_text if item not in text]
    forbidden = [item for item in forbidden_text if item in text]
    if missing:
        raise RuntimeError(f"generated PDF misses required text: {missing}")
    if forbidden:
        raise RuntimeError(f"generated PDF contains forbidden text: {forbidden}")


def set_pdf_metadata(pdf_path: Path, *, title: str) -> None:
    """Normalize metadata after Chromium printing, which can mis-encode CJK titles."""
    import fitz

    with fitz.open(pdf_path) as document:
        metadata = dict(document.metadata or {})
        metadata["title"] = title
        document.set_metadata(metadata)
        document.saveIncr()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Markdown to PDF via Edge/Chrome headless print.")
    parser.add_argument("markdown_file", type=str, help="Path to source markdown file.")
    parser.add_argument("--pdf-out", type=str, default="", help="Optional output pdf path.")
    parser.add_argument("--html-out", type=str, default="", help="Optional output html path.")
    parser.add_argument("--title", type=str, default="", help="Document title used in PDF metadata/banner.")
    parser.add_argument("--annotated", action="store_true", help="Apply the highlighted annotated layout.")
    parser.add_argument(
        "--require-text",
        action="append",
        default=[],
        help="Text that must be extractable from the generated PDF (repeatable).",
    )
    parser.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        help="Text that must not occur in the generated PDF (repeatable).",
    )
    args = parser.parse_args()

    md_path = Path(args.markdown_file).resolve()
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown file not found: {md_path}")

    pdf_path = Path(args.pdf_out).resolve() if args.pdf_out else md_path.with_suffix(".pdf")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    text = md_path.read_text(encoding="utf-8")
    title = str(args.title or "").strip() or md_path.stem
    html = render_html(
        text,
        title,
        annotated=bool(args.annotated),
        source_label=md_path.name,
    )

    with TemporaryDirectory(prefix="m-agent-pdf-", ignore_cleanup_errors=True) as temp_dir:
        temp_root = Path(temp_dir)
        html_path = (
            Path(args.html_out).resolve()
            if args.html_out
            else temp_root / "document.html"
        )
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")
        failures: list[str] = []
        for index, browser in enumerate(resolve_browser_paths()):
            if pdf_path.exists():
                pdf_path.unlink()
            cmd = [
                str(browser),
                "--headless=new",
                "--disable-gpu",
                "--disable-extensions",
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                f"--user-data-dir={temp_root / f'browser-profile-{index}'}",
                f"--print-to-pdf={pdf_path}",
                html_path.as_uri(),
            ]
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 0:
                break
            failures.append(f"{browser.name}: exit {result.returncode}")
        else:
            raise RuntimeError("All PDF browsers failed: " + "; ".join(failures))
        if args.html_out:
            print(f"HTML: {html_path}")

    set_pdf_metadata(pdf_path, title=title)
    validate_pdf(
        pdf_path,
        required_text=list(args.require_text),
        forbidden_text=[
            "file:///",
            "F:/AI/",
            "F:\\AI\\",
            *list(args.forbid_text),
        ],
    )
    print(f"PDF: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
