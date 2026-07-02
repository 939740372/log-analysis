from __future__ import annotations

import argparse
import re
from pathlib import Path

from .code_linker import select_related_code_match_dicts
from .config import AnalysisConfig, LLMConfig
from .dingtalk import DingTalkConfig
from .llm import OpenAICompatibleLLMClient
from .output_paths import ensure_output_root, resolve_output_dir
from .publish import (
    build_report_url,
    refresh_output_index,
    render_react_report_site,
    send_dingtalk_report_notification,
)
from .react_controller import ReActAgentController, run_issues_concurrently, write_multi_issue_summary, write_react_run
from .react_tools import ReactToolRegistry, build_issue_bundle
from .reporter import render_stage_one_markdown, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ReAct Agent 实验入口。")
    parser.add_argument("log_files", nargs="+", help="输入日志文件路径。")
    parser.add_argument("--source-root", help="源码目录。")
    parser.add_argument("--output-dir", default="react-analysis", help="统一输出目录。相对路径会自动放到 output/ 下。")
    parser.add_argument("--issue-index", type=int, help="只运行单个问题调查，从 0 开始。默认会并发运行所有高优先级问题。")
    parser.add_argument("--max-steps", type=int, default=6, help="单个 ReAct 调查的最大步数。")
    parser.add_argument("--parallelism", type=int, help="并发运行的子 Agent 数量。默认使用配置值。")
    parser.add_argument("--retry-count", type=int, help="单次 LLM 请求失败后的最大重试次数。默认使用配置值。")
    parser.add_argument("--retry-backoff-seconds", type=float, help="LLM 请求重试退避秒数。默认使用配置值。")
    parser.add_argument("--llm-base-url", default="http://10.130.61.232:8002")
    parser.add_argument("--llm-model", default="InstructModelQwen3")
    parser.add_argument("--report-base-url", help="报告站点的外部访问基地址，例如 https://example.com/reports")
    parser.add_argument("--dingtalk-webhook", help="钉钉机器人 webhook。配置后会推送摘要通知。")
    parser.add_argument("--dingtalk-secret", help="钉钉机器人加签 secret。")
    return parser


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text).strip("-")
    return cleaned[:40] or "issue"


def _notification_title(log_files: list[Path]) -> str:
    if not log_files:
        return "ReAct 日志调查完成"
    if len(log_files) == 1:
        return f"{log_files[0].stem}日志调查完成"
    return f"{log_files[0].stem}等{len(log_files)}个日志调查完成"


def _build_issue_payload(issue, code_matches: list[dict]) -> dict:
    related_code = select_related_code_match_dicts(issue, code_matches, limit=5)
    return {
        "title": issue.title,
        "priority": issue.severity,
        "description": issue.description,
        "observed_facts": [issue.description] + [item.excerpt[:180] for item in issue.evidence[:3]],
        "evidence": [item.to_dict() for item in issue.evidence],
        "linked_code": related_code,
    }


def main() -> None:
    args = build_parser().parse_args()
    default_llm_config = LLMConfig()
    log_files = [Path(item).resolve() for item in args.log_files]
    source_root = Path(args.source_root).resolve() if args.source_root else None
    output_root = ensure_output_root()
    output_dir = resolve_output_dir(args.output_dir)
    stage1_dir = output_dir / "stage1"
    react_dir = output_dir / "react"
    issues_dir = react_dir / "issues"
    stage1_dir.mkdir(parents=True, exist_ok=True)
    issues_dir.mkdir(parents=True, exist_ok=True)

    analysis_config = AnalysisConfig(output_dir=stage1_dir)
    summary_dict, issues, code_matches = build_issue_bundle(log_files, source_root, analysis_config)
    write_json(stage1_dir / "summary.json", summary_dict)
    from .models import StageOneSummary  # local import to avoid widening existing module dependencies

    stage1_summary = StageOneSummary(
        generated_at=summary_dict["generated_at"],
        files=summary_dict["files"],
        overview=summary_dict["overview"],
        endpoints=summary_dict["endpoints"],
        issues=issues,
        trace_samples=summary_dict["trace_samples"],
        llm_summary=summary_dict.get("llm_summary"),
        llm_error=summary_dict.get("llm_error"),
    )
    (stage1_dir / "summary.md").write_text(render_stage_one_markdown(stage1_summary), encoding="utf-8")
    if not issues:
        raise SystemExit("未识别到可调查的问题。")
    llm_config = LLMConfig(
        base_url=args.llm_base_url,
        model=args.llm_model,
        retry_count=args.retry_count if args.retry_count is not None else default_llm_config.retry_count,
        retry_backoff_seconds=(
            args.retry_backoff_seconds
            if args.retry_backoff_seconds is not None
            else default_llm_config.retry_backoff_seconds
        ),
        stage_two_parallelism=(
            args.parallelism if args.parallelism is not None else default_llm_config.stage_two_parallelism
        ),
    )

    def controller_factory() -> ReActAgentController:
        llm_client = OpenAICompatibleLLMClient(llm_config)
        tool_registry = ReactToolRegistry(log_files, source_root, analysis_config)
        return ReActAgentController(llm_client, tool_registry, max_steps=args.max_steps)

    selected_issues = issues
    if args.issue_index is not None:
        if args.issue_index < 0 or args.issue_index >= len(issues):
            raise SystemExit(f"issue-index 越界，可选范围：0 到 {len(issues) - 1}")
        selected_issues = [issues[args.issue_index]]

    issue_payloads = [_build_issue_payload(issue, code_matches) for issue in selected_issues]
    run_states = run_issues_concurrently(
        controller_factory=controller_factory,
        issues=issue_payloads,
        log_files=[str(path) for path in log_files],
        source_root=str(source_root) if source_root else None,
        max_workers=min(max(1, llm_config.stage_two_parallelism), len(issue_payloads)),
    )

    for index, run_state in enumerate(run_states, start=1):
        issue_output_dir = issues_dir / f"{index:02d}-{_slugify(run_state.issue.title)}"
        write_react_run(issue_output_dir, run_state)

    write_multi_issue_summary(react_dir, run_states)
    _finalize_publication(
        output_root=output_root,
        output_dir=output_dir,
        args=args,
        log_files=log_files,
        run_states=run_states,
    )


def _finalize_publication(*, output_root: Path, output_dir: Path, args, log_files: list[Path], run_states: list) -> None:
    site_index = render_react_report_site(output_dir)
    root_index = refresh_output_index(output_root)
    report_url = build_report_url(site_index, output_root, args.report_base_url)
    payload: dict[str, object] = {
        "site_index": str(site_index),
        "output_root_index": str(root_index),
        "report_url": report_url,
        "dingtalk": None,
    }
    if args.dingtalk_webhook:
        completed = sum(1 for state in run_states if state.completed)
        try:
            response = send_dingtalk_report_notification(
                DingTalkConfig(webhook=args.dingtalk_webhook, secret=args.dingtalk_secret),
                title=_notification_title(log_files),
                lines=[
                    f"输出目录：{output_dir}",
                    f"子 Agent 数量：{len(run_states)}",
                    f"已完成数量：{completed}",
                    f"未完成数量：{len(run_states) - completed}",
                ],
                report_url=report_url,
            )
            payload["dingtalk"] = {"success": True, "response": response}
        except Exception as exc:  # noqa: BLE001
            payload["dingtalk"] = {"success": False, "error": str(exc)}
    write_json(output_dir / "publish.json", payload)


if __name__ == "__main__":
    main()
