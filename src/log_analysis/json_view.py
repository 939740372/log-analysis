from __future__ import annotations

import html
from typing import Any


def render_json_document(title: str, payload: Any, home_href: str = "/") -> str:
    body = _render_value(payload)
    escaped_title = html.escape(title)
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
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0;
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  background:linear-gradient(180deg,#f7efe1 0%,#f1f4f6 100%);
  color:var(--ink);
}}
.page {{ max-width:1200px; margin:0 auto; padding:24px 20px 40px; }}
.toolbar, .card, .node {{ background:var(--panel); border:1px solid var(--line); border-radius:18px; box-shadow:0 12px 30px rgba(47,35,20,0.06); }}
.toolbar {{ display:flex; justify-content:space-between; align-items:center; gap:12px; padding:14px 18px; margin-bottom:18px; }}
.card {{ padding:24px; }}
.node {{ padding:14px 16px; margin:12px 0; }}
.row {{ display:grid; grid-template-columns:220px minmax(0,1fr); gap:12px; padding:8px 0; border-bottom:1px dashed #e7dece; }}
.row:last-child {{ border-bottom:none; }}
.key {{ color:var(--accent); font-weight:700; word-break:break-word; }}
.value {{ white-space:pre-wrap; word-break:break-word; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:999px; background:var(--accent-soft); color:var(--accent); font-size:12px; margin-bottom:8px; }}
.back-link {{ display:inline-flex; align-items:center; gap:8px; padding:10px 14px; border-radius:999px; text-decoration:none; color:var(--accent); background:var(--accent-soft); font-weight:600; }}
.muted {{ color:var(--muted); }}
@media (max-width: 900px) {{
  .row {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>
<main class="page">
  <div class="toolbar">
    <a class="back-link" href="{html.escape(home_href, quote=True)}">返回报告首页</a>
    <div class="muted">{escaped_title}</div>
  </div>
  <section class="card">
    {body}
  </section>
</main>
</body>
</html>"""


def _render_value(value: Any, label: str | None = None) -> str:
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            rows.append(
                "<div class='row'>"
                f"<div class='key'>{html.escape(str(key))}</div>"
                f"<div class='value'>{_render_value(item)}</div>"
                "</div>"
            )
        inner = "".join(rows) or "<div class='muted'>空对象</div>"
        badge = "<div class='badge'>object</div>" if label is None else f"<div class='badge'>{html.escape(label)} · object</div>"
        return f"<div class='node'>{badge}{inner}</div>"
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value):
            rows.append(
                "<div class='row'>"
                f"<div class='key'>[{index}]</div>"
                f"<div class='value'>{_render_value(item)}</div>"
                "</div>"
            )
        inner = "".join(rows) or "<div class='muted'>空数组</div>"
        badge = "<div class='badge'>array</div>" if label is None else f"<div class='badge'>{html.escape(label)} · array</div>"
        return f"<div class='node'>{badge}{inner}</div>"
    return html.escape(str(value))
