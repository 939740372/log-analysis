from __future__ import annotations

import json
from pathlib import Path

from .models import StageOneSummary, StageTwoSummary


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_stage_one_markdown(summary: StageOneSummary) -> str:
    lines: list[str] = []
    lines.append("# 第一阶段日志总结")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 生成时间：`{summary.generated_at}`")
    lines.append(f"- 日志文件数：`{summary.overview['total_files']}`")
    lines.append(f"- 总日志行数：`{summary.overview['total_lines']}`")
    lines.append(f"- 时间范围：`{summary.overview['time_range']['start']}` -> `{summary.overview['time_range']['end']}`")
    lines.append(f"- 日志级别分布：`{summary.overview['level_distribution']}`")
    lines.append("")
    lines.append("## 主要问题")
    lines.append("")
    if not summary.issues:
        lines.append("- 规则分析未发现高信号问题。")
    for issue in summary.issues:
        lines.append(f"### {issue.title}")
        lines.append("")
        lines.append(f"- 严重级别：`{issue.severity}`")
        lines.append(f"- 问题分类：`{issue.category}`")
        lines.append(f"- 问题说明：{issue.description}")
        if issue.related_endpoints:
            lines.append(f"- 相关接口：`{', '.join(issue.related_endpoints[:8])}`")
        if issue.related_classes:
            lines.append(f"- 相关类：`{', '.join(issue.related_classes[:8])}`")
        lines.append("- 证据摘录：")
        for evidence in issue.evidence:
            lines.append(f"  - `Observed` [{Path(evidence.source).name}:{evidence.line_number}] {evidence.excerpt}")
        lines.append("")

    lines.append("## 接口画像")
    lines.append("")
    for endpoint in summary.endpoints:
        lines.append(
            f"- `{endpoint['endpoint']}` 调用次数={endpoint['count']} 平均耗时={endpoint['average_cost_ms']}ms "
            f"最大耗时={endpoint['max_cost_ms']}ms 慢请求数={endpoint['slow_count']} WARN数={endpoint['warn_count']} ERROR数={endpoint['error_count']}"
        )
    lines.append("")

    if summary.trace_samples:
        lines.append("## 关键 Trace 样本")
        lines.append("")
        for trace_sample in summary.trace_samples[:5]:
            lines.append(f"### Trace `{trace_sample['trace_id']}`")
            lines.append("")
            for event in trace_sample["events"][:6]:
                lines.append(
                    f"- `Observed` {event['source_file']}:{event['line_number']} "
                    f"[{event['level']}] {event['message'][:300]}"
                )
            lines.append("")

    if summary.llm_summary:
        lines.append("## LLM 总结")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(summary.llm_summary, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    elif summary.llm_error:
        lines.append("## LLM 状态")
        lines.append("")
        lines.append(f"- LLM 不可用：`{summary.llm_error}`")
        lines.append("")

    return "\n".join(lines)


def render_stage_two_markdown(summary: StageTwoSummary) -> str:
    lines: list[str] = []
    lines.append("# 第二阶段修复建议")
    lines.append("")
    lines.append(f"- 生成时间：`{summary.generated_at}`")
    lines.append(f"- 源码目录：`{summary.source_root}`")
    lines.append(f"- 命中的代码位置数：`{len(summary.code_matches)}`")
    lines.append("")
    for suggestion in summary.suggestions:
        lines.append(f"## {suggestion.title}")
        lines.append("")
        lines.append(f"- 置信度：`{suggestion.confidence}`")
        lines.append("- 已确认事实：")
        for fact in suggestion.observed_facts:
            lines.append(f"  - `Observed` {fact}")
        lines.append("- 关联代码：")
        for match in suggestion.linked_code:
            lines.append(
                f"  - `Linked` {match.file_path}:{match.line_number} "
                f"({match.class_name or '-'}#{match.method_name or '-'}) {match.reason}"
            )
        lines.append(f"- Inferred 修复方向：{suggestion.fix_direction}")
        lines.append(f"- Inferred LLM 建议：{suggestion.llm_suggestion}")
        lines.append("- 可能副作用：")
        for effect in suggestion.side_effects:
            lines.append(f"  - `Inferred` {effect}")
        lines.append("- 证据摘录：")
        for evidence in suggestion.evidence:
            lines.append(
                f"  - `{evidence.kind}` {Path(evidence.source).name}:{evidence.line_number} {evidence.excerpt}"
            )
        lines.append("")
    if summary.llm_error:
        lines.append("## LLM 状态")
        lines.append("")
        lines.append(f"- LLM 不可用：`{summary.llm_error}`")
        lines.append("")
    return "\n".join(lines)
