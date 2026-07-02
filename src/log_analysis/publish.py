from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .dingtalk import DingTalkConfig, send_markdown_message


def build_report_url(report_index_path: Path, output_root: Path, base_url: str | None) -> str | None:
    if not base_url:
        return None
    try:
        relative = report_index_path.resolve().relative_to(output_root.resolve())
    except ValueError:
        return None
    base = base_url.rstrip("/")
    suffix = "/".join(relative.parts)
    return f"{base}/{suffix}"


def render_classic_report_site(output_dir: Path) -> Path:
    summary = _read_json_if_exists(output_dir / "summary.json")
    fix_suggestions = _read_json_if_exists(output_dir / "fix_suggestions.json")
    site_dir = output_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    title = "两阶段日志分析报告"
    html_text = _build_classic_html(title, summary, fix_suggestions, output_dir)
    index_path = site_dir / "index.html"
    index_path.write_text(html_text, encoding="utf-8")
    return index_path


def render_react_report_site(output_dir: Path) -> Path:
    stage1_summary = _read_json_if_exists(output_dir / "stage1" / "summary.json")
    react_summary = _read_json_if_exists(output_dir / "react" / "react_summary.json")
    site_dir = output_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    title = "ReAct 日志调查报告"
    html_text = _build_react_html(title, stage1_summary, react_summary, output_dir)
    index_path = site_dir / "index.html"
    index_path.write_text(html_text, encoding="utf-8")
    return index_path


def refresh_output_index(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    site_entries = sorted(
        [path for path in output_root.rglob("site/index.html") if path.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    lines = [
        "<!doctype html>",
        "<html lang='zh-CN'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>日志分析报告目录</title>",
        _base_style(),
        "</head>",
        "<body>",
        "<main class='page'>",
        "<h1>日志分析报告目录</h1>",
        f"<p class='muted'>输出根目录：{html.escape(str(output_root))}</p>",
        "<section class='card'><ul class='link-list'>",
    ]
    for entry in site_entries:
        relative = entry.relative_to(output_root)
        report_name = relative.parts[0] if relative.parts else entry.stem
        lines.append(f"<li><a href='{html.escape('/'.join(relative.parts))}'>{html.escape(report_name)}</a></li>")
    lines.extend(["</ul></section>", "</main>", "</body>", "</html>"])
    index_path = output_root / "index.html"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def send_dingtalk_report_notification(
    config: DingTalkConfig,
    *,
    title: str,
    lines: list[str],
    report_url: str | None,
) -> dict:
    text_lines = [f"### {title}", ""]
    text_lines.extend(f"- {line}" for line in lines if line)
    if report_url:
        text_lines.extend(["", f"[查看详细报告]({report_url})"])
    return send_markdown_message(config, title=title, text="\n".join(text_lines))


def _build_classic_html(title: str, summary: dict | None, fix_suggestions: dict | None, output_dir: Path) -> str:
    overview = summary.get("overview", {}) if isinstance(summary, dict) else {}
    issues = summary.get("issues", []) if isinstance(summary, dict) else []
    suggestions = fix_suggestions.get("suggestions", []) if isinstance(fix_suggestions, dict) else []
    parts = [
        "<!doctype html>",
        "<html lang='zh-CN'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{html.escape(title)}</title>",
        _base_style(),
        "</head>",
        "<body>",
        "<main class='page'>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p class='muted'>报告目录：{html.escape(str(output_dir))}</p>",
        "<section class='card'><h2>总览</h2>",
        f"<p>总文件数：{overview.get('total_files', '-')}，总行数：{overview.get('total_lines', '-')}</p>",
        f"<p>时间范围：{html.escape(str(overview.get('time_range', {}).get('start')))} - {html.escape(str(overview.get('time_range', {}).get('end')))}</p>",
        "</section>",
        "<section class='card'><h2>问题摘要</h2>",
    ]
    for issue in issues[:8]:
        parts.append("<article class='subcard'>")
        parts.append(f"<h3>{html.escape(str(issue.get('title', '未命名问题')))}</h3>")
        parts.append(f"<p>{html.escape(str(issue.get('description', '')))}</p>")
        evidence = issue.get("evidence", [])
        if evidence:
            parts.append("<ul>")
            for item in evidence[:3]:
                parts.append(
                    f"<li>{html.escape(str(Path(str(item.get('source', ''))).name))}:{item.get('line_number', '-')} {html.escape(str(item.get('excerpt', '')))}</li>"
                )
            parts.append("</ul>")
        parts.append("</article>")
    parts.append("</section>")
    if suggestions:
        parts.append("<section class='card'><h2>修复建议</h2>")
        for suggestion in suggestions[:8]:
            parts.append("<article class='subcard'>")
            parts.append(f"<h3>{html.escape(str(suggestion.get('title', '未命名建议')))}</h3>")
            parts.append(f"<p><strong>置信度：</strong>{html.escape(str(suggestion.get('confidence', '-')))}</p>")
            parts.append(f"<p>{html.escape(str(suggestion.get('fix_direction', '')))}</p>")
            groups = suggestion.get("linked_code_groups", [])
            for group in groups[:4]:
                parts.append(_render_group_html(group))
            parts.append("</article>")
        parts.append("</section>")
    parts.append(_render_artifact_links(output_dir))
    parts.extend(["</main>", "</body>", "</html>"])
    return "\n".join(parts)


def _build_react_html(title: str, stage1_summary: dict | None, react_summary: dict | None, output_dir: Path) -> str:
    overview = stage1_summary.get("overview", {}) if isinstance(stage1_summary, dict) else {}
    summary_items = react_summary.get("summary", []) if isinstance(react_summary, dict) else []
    runs = react_summary.get("runs", []) if isinstance(react_summary, dict) else []
    parts = [
        "<!doctype html>",
        "<html lang='zh-CN'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{html.escape(title)}</title>",
        _base_style(),
        "</head>",
        "<body>",
        "<main class='page'>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p class='muted'>报告目录：{html.escape(str(output_dir))}</p>",
        "<section class='card'><h2>阶段一概览</h2>",
        f"<p>总文件数：{overview.get('total_files', '-')}，总行数：{overview.get('total_lines', '-')}</p>",
        "</section>",
        "<section class='card'><h2>子 Agent 汇总</h2>",
    ]
    for item in summary_items:
        parts.append("<article class='subcard'>")
        parts.append(f"<h3>{html.escape(str(item.get('issue_title', '未命名问题')))}</h3>")
        parts.append(f"<p>状态：{html.escape(str(item.get('status', '-')))}，步数：{html.escape(str(item.get('steps', '-')))}</p>")
        failure_reason = item.get("failure_reason")
        if failure_reason:
            parts.append(f"<p>失败原因：{html.escape(str(failure_reason))}</p>")
        parts.append("</article>")
    parts.append("</section>")
    for run in runs[:10]:
        issue = run.get("issue", {})
        parts.append("<section class='card'>")
        parts.append(f"<h2>{html.escape(str(issue.get('title', '未命名问题')))}</h2>")
        parts.append(f"<p>{html.escape(str(issue.get('description', '')))}</p>")
        for group in issue.get("linked_code_groups", [])[:5]:
            parts.append(_render_group_html(group))
        parts.append("</section>")
    parts.append(_render_artifact_links(output_dir))
    parts.extend(["</main>", "</body>", "</html>"])
    return "\n".join(parts)


def _render_group_html(group: dict[str, Any]) -> str:
    lines = [
        "<div class='group'>",
        f"<h4>{html.escape(str(group.get('file_path', '')))}</h4>",
        f"<p class='muted'>原因：{html.escape('；'.join(str(item) for item in group.get('reasons', [])[:4]))}</p>",
    ]
    for block in group.get("blocks", [])[:4]:
        start = block.get("start_line", "-")
        end = block.get("end_line", "-")
        block_label = f"{start}" if start == end else f"{start}-{end}"
        lines.append(f"<div class='code-block'><div class='muted'>片段行 {html.escape(str(block_label))}</div><pre>")
        context_lines = block.get("context_lines", [])
        hit_line_numbers = set(block.get("hit_line_numbers", []))
        if context_lines:
            for item in context_lines:
                marker = "*" if item.get("line_number") in hit_line_numbers else " "
                lines.append(f"{marker} {item.get('line_number')}: {html.escape(str(item.get('content', '')))}")
        else:
            for item in block.get("lines", []):
                lines.append(f"* {item.get('line_number')}: {html.escape(str(item.get('snippet', '')))}")
        lines.append("</pre></div>")
    lines.append("</div>")
    return "\n".join(lines)


def _render_artifact_links(output_dir: Path) -> str:
    files = sorted([path for path in output_dir.rglob("*") if path.is_file() and path.name != "index.html"])
    lines = ["<section class='card'><h2>产物文件</h2><ul class='link-list'>"]
    for file_path in files[:80]:
        relative = file_path.relative_to(output_dir)
        lines.append(f"<li><a href='../{html.escape('/'.join(relative.parts))}'>{html.escape('/'.join(relative.parts))}</a></li>")
    lines.append("</ul></section>")
    return "\n".join(lines)


def _read_json_if_exists(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _base_style() -> str:
    return """
<style>
:root { color-scheme: light; --bg:#f5f1e8; --card:#fffdf8; --ink:#1f1a14; --muted:#6e6256; --line:#ddd3c5; --accent:#8c3d14; }
* { box-sizing:border-box; }
body { margin:0; font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background:linear-gradient(180deg,#f7efe1 0%,#f1f4f6 100%); color:var(--ink); }
.page { max-width:1100px; margin:0 auto; padding:32px 20px 48px; }
h1,h2,h3,h4 { margin:0 0 12px; }
.card,.subcard,.group,.code-block { background:var(--card); border:1px solid var(--line); border-radius:16px; }
.card { padding:20px; margin:16px 0; box-shadow:0 12px 30px rgba(47,35,20,0.06); }
.subcard,.group { padding:16px; margin:12px 0; }
.code-block { margin:10px 0; padding:12px; background:#fffaf1; }
.muted { color:var(--muted); }
.link-list { margin:0; padding-left:18px; }
pre { margin:8px 0 0; white-space:pre-wrap; word-break:break-word; font-family: "SFMono-Regular", Consolas, monospace; font-size:13px; line-height:1.6; }
a { color:var(--accent); }
</style>
"""
