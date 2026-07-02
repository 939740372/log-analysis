from __future__ import annotations

import html
import re
from dataclasses import dataclass


@dataclass
class HeadingItem:
    level: int
    text: str
    anchor: str


def render_markdown_document(title: str, markdown_text: str, home_href: str = "/") -> str:
    body, headings = _render_markdown(markdown_text)
    escaped_title = html.escape(title)
    toc = _render_toc(headings)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escaped_title}</title>
<style>
:root {{
  color-scheme: light;
  --bg:#f5f1e8;
  --panel:#fffdf8;
  --ink:#1f1a14;
  --muted:#6e6256;
  --line:#ddd3c5;
  --accent:#8c3d14;
  --accent-soft:#f6e8d8;
  --code:#fffaf1;
  --shadow:0 12px 30px rgba(47,35,20,0.06);
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
  margin:0;
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  background:linear-gradient(180deg,#f7efe1 0%,#f1f4f6 100%);
  color:var(--ink);
}}
.layout {{
  max-width:1400px;
  margin:0 auto;
  padding:24px 20px 40px;
  display:grid;
  grid-template-columns:280px minmax(0, 1fr);
  gap:20px;
}}
.sidebar {{
  position:sticky;
  top:20px;
  align-self:start;
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:18px;
  padding:18px;
  box-shadow:var(--shadow);
}}
.content {{
  min-width:0;
}}
.toolbar {{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:18px;
  padding:14px 18px;
  box-shadow:var(--shadow);
  margin-bottom:18px;
}}
.doc {{
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:18px;
  padding:28px;
  box-shadow:var(--shadow);
}}
.back-link {{
  display:inline-flex;
  align-items:center;
  gap:8px;
  padding:10px 14px;
  border-radius:999px;
  text-decoration:none;
  color:var(--accent);
  background:var(--accent-soft);
  font-weight:600;
}}
.muted {{ color:var(--muted); }}
.toc-title {{ margin:0 0 12px; font-size:15px; }}
.toc-list {{ list-style:none; padding:0; margin:0; display:flex; flex-direction:column; gap:8px; }}
.toc-list a {{ color:var(--ink); text-decoration:none; display:block; line-height:1.5; }}
.toc-level-2 {{ padding-left:12px; }}
.toc-level-3, .toc-level-4, .toc-level-5, .toc-level-6 {{ padding-left:24px; }}
h1,h2,h3,h4,h5,h6 {{ margin:26px 0 12px; scroll-margin-top:90px; }}
h1:first-child {{ margin-top:0; }}
p, li {{ line-height:1.85; }}
code {{
  font-family: "SFMono-Regular", Consolas, monospace;
  background:#fff6ea;
  padding:2px 6px;
  border-radius:6px;
}}
.code-shell {{ position:relative; margin:14px 0; }}
.code-shell pre {{
  margin:0;
  padding:16px;
  border-radius:14px;
  background:var(--code);
  overflow:auto;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size:13px;
  line-height:1.65;
  border:1px solid #eadfce;
}}
.code-label {{
  position:absolute;
  top:10px;
  right:12px;
  font-size:12px;
  color:var(--muted);
  background:#fff3df;
  border:1px solid #eadfce;
  padding:3px 8px;
  border-radius:999px;
}}
.tok-key {{ color:#9c3f14; }}
.tok-string {{ color:#116149; }}
.tok-number {{ color:#3d5c96; }}
.tok-bool {{ color:#7b2cbf; font-weight:600; }}
.tok-null {{ color:#9b2226; font-weight:600; }}
ul, ol {{ padding-left:24px; }}
a {{ color:var(--accent); }}
blockquote {{
  margin:16px 0;
  padding:10px 16px;
  border-left:4px solid var(--line);
  color:var(--muted);
  background:#fffaf1;
  border-radius:0 10px 10px 0;
}}
hr {{ border:none; border-top:1px solid var(--line); margin:24px 0; }}
@media (max-width: 980px) {{
  .layout {{ grid-template-columns:1fr; }}
  .sidebar {{ position:static; order:2; }}
}}
</style>
</head>
<body>
<main class="layout">
  <aside class="sidebar">
    <h2 class="toc-title">文档目录</h2>
    {toc}
  </aside>
  <section class="content">
    <div class="toolbar">
      <a class="back-link" href="{html.escape(home_href, quote=True)}">返回报告首页</a>
      <div class="muted">{escaped_title}</div>
    </div>
    <article class="doc">
      {body}
    </article>
  </section>
</main>
</body>
</html>"""


def _render_markdown(text: str) -> tuple[str, list[HeadingItem]]:
    lines = text.splitlines()
    parts: list[str] = []
    headings: list[HeadingItem] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    in_code = False
    code_lines: list[str] = []
    code_language = ""
    heading_counts: dict[str, int] = {}

    def flush_paragraph() -> None:
        if paragraph:
            content = _render_inline(" ".join(item.strip() for item in paragraph))
            parts.append(f"<p>{content}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            items = "".join(f"<li>{_render_inline(item)}</li>" for item in list_items)
            parts.append(f"<ul>{items}</ul>")
            list_items.clear()

    def flush_code() -> None:
        nonlocal code_language
        if code_lines:
            content = _highlight_code("\n".join(code_lines), code_language)
            label = html.escape(code_language) if code_language else "text"
            parts.append(
                "<div class='code-shell'>"
                f"<div class='code-label'>{label}</div>"
                f"<pre><code>{content}</code></pre>"
                "</div>"
            )
            code_lines.clear()
            code_language = ""

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
                code_language = stripped[3:].strip().lower()
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            level = len(heading_match.group(1))
            raw_heading = heading_match.group(2).strip()
            anchor = _slugify_anchor(raw_heading, heading_counts)
            headings.append(HeadingItem(level=level, text=raw_heading, anchor=anchor))
            content = _render_inline(raw_heading)
            parts.append(f"<h{level} id=\"{anchor}\">{content}</h{level}>")
            continue

        if re.match(r"^[-*]\s+", stripped):
            flush_paragraph()
            list_items.append(re.sub(r"^[-*]\s+", "", stripped, count=1))
            continue

        if stripped == "---":
            flush_paragraph()
            flush_list()
            parts.append("<hr>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            parts.append(f"<blockquote>{_render_inline(stripped.lstrip('> ').strip())}</blockquote>")
            continue

        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    if in_code:
        flush_code()
    return "\n".join(parts), headings


def _render_toc(headings: list[HeadingItem]) -> str:
    if not headings:
        return "<p class='muted'>当前文档没有可导航标题。</p>"
    items = []
    for item in headings:
        level = max(1, min(item.level, 6))
        items.append(
            f"<li class='toc-level-{level}'><a href='#{html.escape(item.anchor, quote=True)}'>{html.escape(item.text)}</a></li>"
        )
    return f"<ul class='toc-list'>{''.join(items)}</ul>"


def _slugify_anchor(text: str, heading_counts: dict[str, int]) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text.strip().lower()).strip("-") or "section"
    count = heading_counts.get(base, 0)
    heading_counts[base] = count + 1
    return base if count == 0 else f"{base}-{count + 1}"


def _highlight_code(text: str, language: str) -> str:
    escaped = html.escape(text)
    if language == "json":
        escaped = re.sub(r'(&quot;[^&]+&quot;)(\s*:)', r'<span class="tok-key">\1</span>\2', escaped)
        escaped = re.sub(r':\s*(&quot;.*?&quot;)', r': <span class="tok-string">\1</span>', escaped)
        escaped = re.sub(r'(?<![\w>])(-?\d+(?:\.\d+)?)', r'<span class="tok-number">\1</span>', escaped)
        escaped = re.sub(r'\b(true|false)\b', r'<span class="tok-bool">\1</span>', escaped)
        escaped = re.sub(r'\bnull\b', r'<span class="tok-null">null</span>', escaped)
    elif language in {"bash", "sh", "shell", "zsh", "powershell"}:
        escaped = re.sub(r"(^|\n)([$>#].*)", lambda m: f"{m.group(1)}<span class=\"tok-key\">{m.group(2)}</span>", escaped)
    return escaped


def _render_inline(text: str) -> str:
    placeholders: list[str] = []

    def store(value: str) -> str:
        placeholders.append(value)
        return f"@@PLACEHOLDER_{len(placeholders) - 1}@@"

    tokenized = re.sub(r"`([^`]+)`", lambda m: store(f"<code>{html.escape(m.group(1))}</code>"), text)
    tokenized = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: store(f"<a href=\"{html.escape(m.group(2), quote=True)}\">{html.escape(m.group(1))}</a>"),
        tokenized,
    )
    escaped = html.escape(tokenized)
    for index, value in enumerate(placeholders):
        escaped = escaped.replace(f"@@PLACEHOLDER_{index}@@", value)
    return escaped
