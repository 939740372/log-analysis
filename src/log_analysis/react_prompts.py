from __future__ import annotations

import json

from .code_display import group_code_reference_dicts
from .react_state import AgentRunState


PLANNER_SYSTEM_PROMPT = (
    "你是一个受控的 Java 日志排障 ReAct Agent。"
    "你必须在给定工具中选择下一步动作，不能编造工具结果。"
    "你的输出必须是 JSON。"
    "如果证据仍不足，输出 thought 和 action。"
    "如果证据已经足够，输出 thought 和 final。"
    "action 必须包含 tool 和 arguments。"
    "final 必须包含 title、summary、observed_facts、linked_code、fix_direction、llm_suggestion、risks。"
    "除非新的 line_number、文件或窗口范围不同，否则不要重复调用同一个 open_code_context。"
    "优先使用更便宜、更高信息密度的工具：先 search_logs / search_code_symbols / get_config_matches，再 open_code_context。"
    "当已经具备文件路径或符号线索时，先搜索再打开，不要盲目连续打开多个代码片段。"
    "如果已经有明确的 observed facts、linked code 和可执行修复方向，请尽快输出 final，不要为了凑步数继续检索。"
)


def build_planner_messages(state: AgentRunState, tool_specs: list[dict]) -> list[dict]:
    recent_steps = [step.to_dict() for step in state.steps[-3:]]
    previous_open_code = []
    for action in state.previous_actions("open_code_context")[-5:]:
        previous_open_code.append(action.arguments)
    payload = {
        "goal": state.goal,
        "stage": state.stage,
        "issue": state.issue.to_dict(),
        "tool_specs": tool_specs,
        "recent_steps": recent_steps,
        "planner_hints": {
            "previous_open_code_context_calls": previous_open_code,
            "preferred_order": [
                "search_logs",
                "search_code_symbols",
                "get_config_matches",
                "open_code_context",
            ],
        },
        "max_steps": state.max_steps,
        "current_step_index": len(state.steps) + 1,
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def build_final_markdown(run_state: AgentRunState) -> str:
    lines: list[str] = []
    lines.append(f"# ReAct 调查结果：{run_state.issue.title}")
    lines.append("")
    lines.append(f"- 运行 ID：`{run_state.run_id}`")
    lines.append(f"- 阶段：`{run_state.stage}`")
    lines.append(f"- 步数：`{len(run_state.steps)}` / `{run_state.max_steps}`")
    lines.append("")
    if run_state.final_answer:
        final = run_state.final_answer
        lines.append("## 最终结论")
        lines.append("")
        lines.append(f"- 标题：{final.get('title', run_state.issue.title)}")
        lines.append(f"- 摘要：{final.get('summary', '')}")
        lines.append("- Observed：")
        for item in final.get("observed_facts", []):
            lines.append(f"  - {item}")
        lines.append("- Linked：")
        for group in group_code_reference_dicts(final.get("linked_code", [])):
            classes = ", ".join(group["class_names"]) if group["class_names"] else "-"
            methods = ", ".join(group["method_names"]) if group["method_names"] else "-"
            reasons = "；".join(group["reasons"][:4])
            lines.append(f"  - 文件：{group['file_path']} 类={classes} 方法={methods} 原因={reasons}")
            for block in group["blocks"]:
                if block["start_line"] == block["end_line"]:
                    lines.append(f"  - 片段行 {block['start_line']}")
                else:
                    lines.append(f"  - 片段行 {block['start_line']}-{block['end_line']}")
                if block["context_lines"]:
                    for context in block["context_lines"]:
                        marker = "*" if context["line_number"] in block["hit_line_numbers"] else "-"
                        lines.append(f"  - {marker} {context['line_number']}: {context['content']}")
                else:
                    for line in block["lines"]:
                        lines.append(f"  - 行 {line['line_number']}: {line['snippet']}")
        lines.append(f"- Inferred 修复方向：{final.get('fix_direction', '')}")
        lines.append(f"- Inferred LLM 建议：{final.get('llm_suggestion', '')}")
        lines.append("- 风险：")
        for item in final.get("risks", []):
            lines.append(f"  - {item}")
        lines.append("")
    lines.append("## 执行轨迹")
    lines.append("")
    for step in run_state.steps:
        lines.append(f"### Step {step.index}")
        lines.append("")
        lines.append(f"- Thought：{step.thought}")
        if step.action:
            lines.append(f"- Action：`{step.action.tool}` {json.dumps(step.action.arguments, ensure_ascii=False)}")
        if step.observation:
            lines.append(f"- Observation：{step.observation.summary}")
        if step.final:
            lines.append("- Final：已输出最终答案")
        lines.append("")
    return "\n".join(lines)


def build_multi_issue_markdown(run_states: list[AgentRunState]) -> str:
    lines: list[str] = []
    lines.append("# ReAct 多问题汇总")
    lines.append("")
    lines.append(f"- 子 Agent 数量：`{len(run_states)}`")
    lines.append("")
    for state in run_states:
        lines.append(f"## {state.issue.title}")
        lines.append("")
        lines.append(f"- 运行 ID：`{state.run_id}`")
        lines.append(f"- 完成状态：`{'已完成' if state.completed else '未完成'}`")
        lines.append(f"- 状态：`{state.status}`")
        lines.append(f"- 实际步数：`{len(state.steps)}`")
        if state.last_tool:
            lines.append(f"- 最后工具：`{state.last_tool}`")
        if state.failure_reason:
            lines.append(f"- 失败原因：{state.failure_reason}")
        if state.final_answer:
            lines.append(f"- 最终标题：{state.final_answer.get('title', state.issue.title)}")
            lines.append(f"- 摘要：{state.final_answer.get('summary', '')}")
        lines.append("")
    return "\n".join(lines)
