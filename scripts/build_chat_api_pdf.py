from __future__ import annotations

import html
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs" / "chat_api"
README_PATH = DOCS_DIR / "README.md"
HTTP_PATH = DOCS_DIR / "testing.http"
HTML_PATH = DOCS_DIR / "chat_api_reference.html"
PDF_PATH = DOCS_DIR / "chat_api_reference.pdf"
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
]


@dataclass
class Heading:
    level: int
    text: str
    anchor: str


def slugify(text: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(text or "").strip().lower())
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base or "section"


def replace_placeholders(text: str, replacements: List[str]) -> str:
    for idx, value in enumerate(replacements):
        text = text.replace(f"@@PH{idx}@@", value)
    return text


def render_inlines(text: str) -> str:
    raw = str(text or "")
    replacements: List[str] = []

    def stash(value: str) -> str:
        replacements.append(value)
        return f"@@PH{len(replacements) - 1}@@"

    raw = re.sub(
        r"`([^`]+)`",
        lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"),
        raw,
    )
    raw = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: stash(
            f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'
        ),
        raw,
    )
    raw = re.sub(
        r"\*\*([^*]+)\*\*",
        lambda m: stash(f"<strong>{html.escape(m.group(1))}</strong>"),
        raw,
    )
    escaped = html.escape(raw)
    return replace_placeholders(escaped, replacements)


def is_table_separator(line: str) -> bool:
    text = str(line or "").strip()
    if not text.startswith("|"):
        return False
    return bool(re.fullmatch(r"\|\s*[:\-| ]+\|?", text))


def parse_table_row(line: str) -> List[str]:
    row = str(line or "").strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") else row
    return [cell.strip() for cell in row.split("|")]


def render_table(lines: List[str]) -> str:
    headers = parse_table_row(lines[0])
    body_rows = [parse_table_row(line) for line in lines[2:]]
    parts = ['<table>', '<thead><tr>']
    for cell in headers:
        parts.append(f"<th>{render_inlines(cell)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in body_rows:
        parts.append("<tr>")
        for idx, cell in enumerate(row):
            if idx >= len(headers):
                break
            parts.append(f"<td>{render_inlines(cell)}</td>")
        if len(row) < len(headers):
            for _ in range(len(headers) - len(row)):
                parts.append("<td></td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def render_markdown(markdown_text: str) -> Tuple[str, List[Heading]]:
    lines = markdown_text.splitlines()
    parts: List[str] = []
    headings: List[Heading] = []
    paragraph: List[str] = []
    list_items: List[str] = []
    list_kind: str | None = None
    in_code = False
    code_lines: List[str] = []
    code_lang = ""
    used_anchors: set[str] = set()
    idx = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(item.strip() for item in paragraph if item.strip())
            if text:
                parts.append(f"<p>{render_inlines(text)}</p>")
        paragraph = []

    def flush_list() -> None:
        nonlocal list_items, list_kind
        if list_items and list_kind:
            parts.append(f"<{list_kind}>")
            parts.extend(list_items)
            parts.append(f"</{list_kind}>")
        list_items = []
        list_kind = None

    def flush_code() -> None:
        nonlocal in_code, code_lines, code_lang
        if in_code:
            class_attr = f' class="language-{html.escape(code_lang)}"' if code_lang else ""
            parts.append(
                f"<pre><code{class_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>"
            )
        in_code = False
        code_lines = []
        code_lang = ""

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        if in_code:
            if stripped.startswith("```"):
                flush_code()
            else:
                code_lines.append(line)
            idx += 1
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            in_code = True
            code_lang = stripped[3:].strip()
            idx += 1
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            idx += 1
            continue

        if stripped.startswith("|") and idx + 1 < len(lines) and is_table_separator(lines[idx + 1]):
            flush_paragraph()
            flush_list()
            table_lines = [line]
            idx += 1
            while idx < len(lines):
                candidate = lines[idx]
                if not candidate.strip().startswith("|"):
                    break
                table_lines.append(candidate)
                idx += 1
            parts.append(render_table(table_lines))
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            anchor = slugify(text)
            dedupe = 2
            base_anchor = anchor
            while anchor in used_anchors:
                anchor = f"{base_anchor}-{dedupe}"
                dedupe += 1
            used_anchors.add(anchor)
            headings.append(Heading(level=level, text=text, anchor=anchor))
            parts.append(f'<h{level} id="{anchor}">{render_inlines(text)}</h{level}>')
            idx += 1
            continue

        quote_match = re.match(r"^>\s?(.*)$", stripped)
        if quote_match:
            flush_paragraph()
            flush_list()
            quote_lines: List[str] = []
            while idx < len(lines):
                quote_line = lines[idx].strip()
                match = re.match(r"^>\s?(.*)$", quote_line)
                if not match:
                    break
                quote_lines.append(match.group(1))
                idx += 1
            quote_text = " ".join(item.strip() for item in quote_lines if item.strip())
            parts.append(f"<blockquote><p>{render_inlines(quote_text)}</p></blockquote>")
            continue

        bullet_match = re.match(r"^[-*]\s+(.*)$", stripped)
        ordered_match = re.match(r"^\d+\.\s+(.*)$", stripped)
        if bullet_match or ordered_match:
            flush_paragraph()
            kind = "ul" if bullet_match else "ol"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            item_text = bullet_match.group(1) if bullet_match else ordered_match.group(1)
            list_items.append(f"<li>{render_inlines(item_text)}</li>")
            idx += 1
            continue

        if list_kind:
            flush_list()
        paragraph.append(line)
        idx += 1

    flush_paragraph()
    flush_list()
    flush_code()
    return "\n".join(parts), headings


def build_toc(headings: List[Heading]) -> str:
    items: List[str] = []
    for heading in headings:
        if heading.level > 3:
            continue
        cls = f"toc-level-{heading.level}"
        items.append(
            f'<li class="{cls}"><a href="#{heading.anchor}">{html.escape(heading.text)}</a></li>'
        )
    if not items:
        return ""
    return (
        '<section class="toc">'
        "<h2>Contents</h2>"
        "<ul>"
        + "".join(items)
        + "</ul>"
        "</section>"
    )


def build_html() -> str:
    readme = README_PATH.read_text(encoding="utf-8")
    request_collection = HTTP_PATH.read_text(encoding="utf-8")
    readme_html, headings = render_markdown(readme)
    toc_html = build_toc(headings)
    request_html = (
        "<section class=\"appendix\">"
        "<h1 id=\"appendix-testing-http\">Appendix: testing.http</h1>"
        "<p>This appendix includes the ready-to-edit request collection from "
        "<code>docs/chat_api/testing.http</code>.</p>"
        f"<pre><code>{html.escape(request_collection)}</code></pre>"
        "</section>"
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>M-Agent Chat API Reference</title>
  <style>
    @page {{
      size: A4;
      margin: 16mm 14mm 16mm 14mm;
    }}
    :root {{
      --fg: #1f2328;
      --muted: #57606a;
      --line: #d0d7de;
      --soft: #f6f8fa;
      --soft-2: #ffffff;
      --accent: #0b57d0;
    }}
    html {{
      font-size: 12px;
    }}
    body {{
      margin: 0 auto;
      color: var(--fg);
      font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
      line-height: 1.55;
    }}
    main {{
      max-width: 100%;
    }}
    .cover {{
      border-bottom: 2px solid var(--line);
      padding-bottom: 10mm;
      margin-bottom: 8mm;
    }}
    .cover h1 {{
      font-size: 24px;
      margin: 0 0 4mm 0;
    }}
    .cover p {{
      margin: 2mm 0;
      color: var(--muted);
    }}
    .toc {{
      margin: 0 0 8mm 0;
      padding: 5mm 6mm;
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 6px;
    }}
    .toc h2 {{
      margin-top: 0;
    }}
    .toc ul {{
      margin: 0;
      padding-left: 18px;
    }}
    .toc li {{
      margin: 1.5mm 0;
    }}
    .toc .toc-level-3 {{
      margin-left: 10px;
    }}
    h1, h2, h3, h4 {{
      page-break-after: avoid;
      margin-top: 7mm;
      margin-bottom: 3mm;
      line-height: 1.25;
    }}
    h1 {{
      font-size: 20px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 2mm;
    }}
    h2 {{
      font-size: 16px;
    }}
    h3 {{
      font-size: 13px;
    }}
    p, ul, ol, blockquote, table, pre {{
      margin-top: 0;
      margin-bottom: 4mm;
    }}
    ul, ol {{
      padding-left: 18px;
    }}
    li {{
      margin: 1mm 0;
    }}
    blockquote {{
      margin-left: 0;
      padding: 3mm 4mm;
      border-left: 4px solid #9fbef5;
      background: #f4f8ff;
    }}
    code {{
      font-family: "Cascadia Mono", "Consolas", monospace;
      background: var(--soft);
      padding: 0.2em 0.45em;
      border-radius: 4px;
      font-size: 0.95em;
    }}
    pre {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 4mm;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    pre code {{
      background: transparent;
      padding: 0;
      border-radius: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 10.5px;
      table-layout: fixed;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 2.2mm 2.5mm;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{
      background: #eef3f8;
      text-align: left;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    .appendix {{
      page-break-before: always;
    }}
  </style>
</head>
<body>
  <main>
    <section class="cover">
      <h1>M-Agent Chat API Reference</h1>
      <p>Combined from <code>docs/chat_api/README.md</code> and <code>docs/chat_api/testing.http</code>.</p>
      <p>This PDF is intended for software development, integration, and manual API testing.</p>
    </section>
    {toc_html}
    {readme_html}
    {request_html}
  </main>
</body>
</html>
"""


def resolve_browser() -> Path | None:
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def export_pdf_from_html(html_path: Path, pdf_path: Path) -> Path | None:
    browser = resolve_browser()
    if browser is None:
        return None
    html_url = html_path.resolve().as_uri()
    subprocess.run(
        [
            str(browser),
            "--headless",
            "--disable-gpu",
            "--allow-file-access-from-files",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_url,
        ],
        check=True,
    )
    return pdf_path


def main() -> None:
    HTML_PATH.write_text(build_html(), encoding="utf-8")
    print(f"HTML\t{HTML_PATH}")
    pdf_path = export_pdf_from_html(HTML_PATH, PDF_PATH)
    if pdf_path is None:
        print("PDF\tSKIPPED (no Edge/Chrome found)")
    else:
        print(f"PDF\t{pdf_path}")


if __name__ == "__main__":
    main()
