from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .aggregator import analyze_logs
from .code_linker import link_issues_to_code
from .config import AnalysisConfig, LLMConfig
from .llm import (
    LLMError,
    OpenAICompatibleLLMClient,
    build_stage_one_messages,
    build_stage_two_issue_messages,
)
from .models import CodeReference, FixSuggestion, StageTwoSummary
from .reporter import render_stage_one_markdown, render_stage_two_markdown, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="两阶段 Java 日志分析工具。")
    parser.add_argument("log_files", nargs="+", help="输入的日志文件路径，可传多个。")
    parser.add_argument("--output-dir", default="out", help="分析结果输出目录。")
    parser.add_argument("--source-root", help="第二阶段源码分析使用的本地 Java 项目根目录。")
    parser.add_argument("--disable-llm", action="store_true", help="跳过所有 LLM 调用，仅输出规则分析结果。")
    parser.add_argument("--llm-base-url", default="http://10.130.61.232:8002")
    parser.add_argument("--llm-model", default="InstructModelQwen3")
    parser.add_argument("--slow-threshold-ms", type=int, default=1000, help="慢请求阈值，单位毫秒。")
    parser.add_argument("--max-code-matches", type=int, default=30, help="第二阶段最多保留的代码命中数量。")
    return parser


def _run_stage_one_llm(client: OpenAICompatibleLLMClient, summary_dict: dict) -> tuple[dict | None, str | None]:
    payload = {
        "overview": summary_dict["overview"],
        "top_endpoints": summary_dict["endpoints"][:10],
        "issues": summary_dict["issues"][:5],
        "trace_samples": summary_dict["trace_samples"][:3],
    }
    try:
        result = client.create_chat_completion(build_stage_one_messages(payload), max_completion_tokens=800)
        return result.parsed_json or {"raw_text": result.raw_text}, None
    except LLMError as exc:
        return None, str(exc)


def _run_stage_one_llm_with_retry(
    client: OpenAICompatibleLLMClient, summary_dict: dict
) -> tuple[dict | None, str | None]:
    last_error = None
    for attempt in range(1, client.config.retry_count + 1):
        llm_summary, llm_error = _run_stage_one_llm(client, summary_dict)
        if llm_summary is not None:
            return llm_summary, None
        last_error = llm_error
        if attempt < client.config.retry_count:
            time.sleep(client.config.retry_backoff_seconds * attempt)
    return None, last_error


def _select_related_matches(issue, code_matches: list[CodeReference]) -> list[CodeReference]:
    related: list[CodeReference] = []
    seen: set[tuple[str, int]] = set()
    class_short_names = {clazz.split(".")[-1] for clazz in issue.related_classes}
    keyword_tokens = set(issue.related_keywords)
    if issue.category == "数据库":
        keyword_tokens.update({"druid", "validation", "keepAlive", "testWhileIdle", "DynamicDataSourceFactory"})
    if issue.category == "应用":
        keyword_tokens.update({"async method", "addDistance"})

    for match in code_matches:
        reason = match.reason.lower()
        snippet = match.snippet.lower()
        class_name = (match.class_name or "").lower()
        method_name = (match.method_name or "").lower()
        matched = False

        if any(endpoint.lower() in reason or endpoint.lower() in snippet for endpoint in issue.related_endpoints):
            matched = True
        elif any(short.lower() == class_name or short.lower() in reason for short in class_short_names):
            matched = True
        elif any(token.lower() in reason or token.lower() in snippet or token.lower() == method_name for token in keyword_tokens):
            matched = True

        if matched:
            key = (match.file_path, match.line_number)
            if key not in seen:
                seen.add(key)
                related.append(match)
        if len(related) >= 5:
            break
    return related


def _normalize_llm_suggestions(parsed: object) -> list[dict]:
    if isinstance(parsed, dict) and isinstance(parsed.get("suggestions"), list):
        return [item for item in parsed["suggestions"] if isinstance(item, dict)]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict) and {"title", "fix_direction", "llm_suggestion"} <= set(parsed.keys()):
        return [parsed]
    return []


def _ensure_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _convert_issue_llm_result(
    issue, item: dict, issue_matches: list[CodeReference], raw_text: str
) -> FixSuggestion:
    return FixSuggestion(
        title=str(item.get("title") or issue.title),
        confidence=str(item.get("confidence") or "中"),
        observed_facts=_ensure_list(item.get("observed_facts"))[:6] or [issue.description],
        linked_code=issue_matches,
        fix_direction=str(item.get("fix_direction") or ""),
        llm_suggestion=str(item.get("llm_suggestion") or raw_text[:500]),
        side_effects=_ensure_list(item.get("side_effects"))[:6],
        evidence=issue.evidence[:4],
    )


def _run_single_stage_two_issue_llm(
    client: OpenAICompatibleLLMClient,
    issue,
    issue_matches: list[CodeReference],
) -> tuple[FixSuggestion | None, str | None]:
    payload = {
        "issue": issue.to_dict(),
        "code_matches": [match.to_dict() for match in issue_matches[:8]],
    }
    last_error = None
    for attempt in range(1, client.config.retry_count + 1):
        try:
            result = client.create_chat_completion(build_stage_two_issue_messages(payload), max_completion_tokens=900)
            parsed = result.parsed_json
            if isinstance(parsed, dict) and {"title", "fix_direction", "llm_suggestion"} <= set(parsed.keys()):
                return _convert_issue_llm_result(issue, parsed, issue_matches, result.raw_text), None
            suggestion_items = _normalize_llm_suggestions(parsed)
            if suggestion_items:
                return _convert_issue_llm_result(issue, suggestion_items[0], issue_matches, result.raw_text), None
            last_error = "LLM 返回结果中没有合法的建议对象"
        except LLMError as exc:
            last_error = str(exc)
        if attempt < client.config.retry_count:
            time.sleep(client.config.retry_backoff_seconds * attempt)
    return None, last_error


def _run_stage_two_llm(
    client: OpenAICompatibleLLMClient,
    stage_one_summary,
    code_matches: list[CodeReference],
) -> tuple[list[FixSuggestion], str | None]:
    issues = stage_one_summary.issues[:5]
    if not issues:
        return [], None

    suggestions_by_index: dict[int, FixSuggestion] = {}
    errors: list[str] = []
    max_workers = max(1, min(client.config.stage_two_parallelism, len(issues)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _run_single_stage_two_issue_llm,
                client,
                issue,
                _select_related_matches(issue, code_matches),
            ): index
            for index, issue in enumerate(issues)
        }
        for future in as_completed(future_map):
            index = future_map[future]
            issue = issues[index]
            try:
                suggestion, error = future.result()
            except Exception as exc:  # noqa: BLE001
                suggestion, error = None, str(exc)
            if suggestion is not None:
                suggestions_by_index[index] = suggestion
            else:
                errors.append(f"{issue.title}: {error or 'unknown error'}")

    ordered = [suggestions_by_index[index] for index in sorted(suggestions_by_index)]
    if errors and ordered:
        return ordered, "；".join(errors)
    if errors:
        return [], "；".join(errors)
    return ordered, None


def main() -> None:
    args = build_parser().parse_args()
    log_files = [Path(item).resolve() for item in args.log_files]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    analysis_config = AnalysisConfig(
        output_dir=output_dir,
        slow_request_threshold_ms=args.slow_threshold_ms,
        max_code_matches=args.max_code_matches,
    )
    llm_config = LLMConfig(base_url=args.llm_base_url, model=args.llm_model)

    stage_one_summary = analyze_logs(log_files, analysis_config)

    client = None if args.disable_llm else OpenAICompatibleLLMClient(llm_config)
    if client is not None:
        llm_summary, llm_error = _run_stage_one_llm_with_retry(client, stage_one_summary.to_dict())
        stage_one_summary.llm_summary = llm_summary
        stage_one_summary.llm_error = llm_error

    write_json(output_dir / "summary.json", stage_one_summary.to_dict())
    (output_dir / "summary.md").write_text(render_stage_one_markdown(stage_one_summary), encoding="utf-8")

    if not args.source_root:
        return

    source_root = Path(args.source_root).resolve()
    code_matches = link_issues_to_code(source_root, stage_one_summary.issues, analysis_config)
    if client is not None:
        suggestions, llm_error = _run_stage_two_llm(client, stage_one_summary, code_matches)
    else:
        suggestions, llm_error = [], "LLM 已禁用"

    stage_two_summary = StageTwoSummary(
        generated_at=stage_one_summary.generated_at,
        source_root=str(source_root),
        code_matches=code_matches,
        suggestions=suggestions,
        llm_error=llm_error,
    )
    write_json(output_dir / "fix_suggestions.json", stage_two_summary.to_dict())
    (output_dir / "fix_suggestions.md").write_text(render_stage_two_markdown(stage_two_summary), encoding="utf-8")


if __name__ == "__main__":
    main()
