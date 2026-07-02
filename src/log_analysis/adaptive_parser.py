from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .llm import (
    LLMError,
    OpenAICompatibleLLMClient,
    build_adaptive_parser_messages,
    build_adaptive_parser_repair_messages,
)
from .models import LogEvent

ALLOWED_GROUPS = {"timestamp", "level", "thread", "logger", "message"}


@dataclass(frozen=True)
class DynamicParseRule:
    format_name: str
    strategy: str
    outer_regex: str
    outer_timestamp_format: str | None
    embedded_regex: str | None = None
    embedded_timestamp_format: str | None = None
    embedded_uses_outer_date: bool = False
    logger_literal: str | None = None

    def to_dict(self) -> dict:
        return {
            "format_name": self.format_name,
            "strategy": self.strategy,
            "outer_regex": self.outer_regex,
            "outer_timestamp_format": self.outer_timestamp_format,
            "embedded_regex": self.embedded_regex,
            "embedded_timestamp_format": self.embedded_timestamp_format,
            "embedded_uses_outer_date": self.embedded_uses_outer_date,
            "logger_literal": self.logger_literal,
        }


@dataclass(frozen=True)
class AdaptiveRuleDecision:
    source_file: str
    enabled: bool
    reason: str
    sample_line_count: int
    builtin_score: int
    dynamic_score: int
    rule: DynamicParseRule | None = None
    raw_response: dict | list | str | None = None

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "enabled": self.enabled,
            "reason": self.reason,
            "sample_line_count": self.sample_line_count,
            "builtin_score": self.builtin_score,
            "dynamic_score": self.dynamic_score,
            "rule": self.rule.to_dict() if self.rule else None,
            "raw_response": self.raw_response,
        }


def sample_log_lines(log_path: Path, limit: int) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    if limit <= 0:
        return samples
    keyword_hits: list[dict[str, object]] = []
    keywords = ("error", "warn", "exception", "timed out", "caused by", "fallback")
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.rstrip("\n")
            if not text:
                continue
            record = {"line_number": line_number, "text": text[:1200]}
            if len(samples) < limit:
                samples.append(record)
            lowered = text.lower()
            if any(keyword in lowered for keyword in keywords):
                keyword_hits.append(record)
    merged: list[dict[str, object]] = []
    seen: set[int] = set()
    for item in samples + keyword_hits:
        line_number = int(item["line_number"])
        if line_number in seen:
            continue
        seen.add(line_number)
        merged.append(item)
        if len(merged) >= limit * 2:
            break
    return merged


def infer_dynamic_parse_rule(
    client: OpenAICompatibleLLMClient,
    log_path: Path,
    sample_lines: list[dict[str, object]],
) -> tuple[DynamicParseRule | None, dict | list | str | None, str | None]:
    payload = {
        "file_name": log_path.name,
        "sample_lines": sample_lines,
    }
    last_error = None
    raw_response: dict | list | str | None = None
    for attempt in range(1, client.config.retry_count + 1):
        try:
            result = client.create_chat_completion(
                build_adaptive_parser_messages(payload),
                max_completion_tokens=1000,
            )
            raw_response = result.parsed_json or result.raw_text
            rule = _normalize_dynamic_rule(result.parsed_json)
            if rule is not None:
                return rule, raw_response, None
            repaired_rule, repaired_raw = _repair_dynamic_rule(client, result.raw_text)
            if repaired_rule is not None:
                return repaired_rule, repaired_raw, None
            last_error = "LLM 返回的解析规则不合法"
        except LLMError as exc:
            last_error = str(exc)
        if attempt < client.config.retry_count:
            import time

            time.sleep(client.config.retry_backoff_seconds * attempt)
    return None, raw_response, last_error


def _repair_dynamic_rule(
    client: OpenAICompatibleLLMClient,
    raw_text: str,
) -> tuple[DynamicParseRule | None, dict | list | str | None]:
    try:
        result = client.create_chat_completion(
            build_adaptive_parser_repair_messages(raw_text),
            max_completion_tokens=700,
        )
    except LLMError:
        return None, raw_text
    repaired_raw = result.parsed_json or result.raw_text
    return _normalize_dynamic_rule(result.parsed_json), repaired_raw


def _normalize_dynamic_rule(parsed: object) -> DynamicParseRule | None:
    if not isinstance(parsed, dict):
        return None
    try:
        strategy = str(parsed["strategy"])
        outer_regex = str(parsed["outer_regex"])
        outer_timestamp_format = parsed.get("outer_timestamp_format")
        embedded_regex = parsed.get("embedded_regex")
        embedded_timestamp_format = parsed.get("embedded_timestamp_format")
        embedded_uses_outer_date = bool(parsed.get("embedded_uses_outer_date", False))
        logger_literal = parsed.get("logger_literal")
        format_name = str(parsed.get("format_name") or "adaptive")
    except (KeyError, TypeError, ValueError):
        return None
    if strategy not in {"direct", "wrapper_embedded"}:
        return None
    if not _is_safe_regex(outer_regex):
        return None
    if embedded_regex is not None and not _is_safe_regex(str(embedded_regex)):
        return None
    if not _regex_groups_allowed(outer_regex):
        return None
    if embedded_regex is not None and not _regex_groups_allowed(str(embedded_regex)):
        return None
    if outer_timestamp_format is not None:
        outer_timestamp_format = str(outer_timestamp_format)
    if embedded_regex is not None:
        embedded_regex = str(embedded_regex)
    if embedded_timestamp_format is not None:
        embedded_timestamp_format = str(embedded_timestamp_format)
    if logger_literal is not None:
        logger_literal = str(logger_literal)
    return DynamicParseRule(
        format_name=format_name,
        strategy=strategy,
        outer_regex=outer_regex,
        outer_timestamp_format=outer_timestamp_format,
        embedded_regex=embedded_regex,
        embedded_timestamp_format=embedded_timestamp_format,
        embedded_uses_outer_date=embedded_uses_outer_date,
        logger_literal=logger_literal,
    )


def _is_safe_regex(pattern: str) -> bool:
    if len(pattern) > 1000:
        return False
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


def _regex_groups_allowed(pattern: str) -> bool:
    compiled = re.compile(pattern)
    return set(compiled.groupindex).issubset(ALLOWED_GROUPS)


def parse_with_dynamic_rule(
    raw_line: str,
    source_file: str,
    line_number: int,
    rule: DynamicParseRule,
) -> LogEvent | None:
    outer_match = re.search(rule.outer_regex, raw_line)
    if not outer_match:
        return None
    outer_groups = outer_match.groupdict()
    outer_message = str(outer_groups.get("message") or raw_line)
    outer_timestamp = _parse_datetime(outer_groups.get("timestamp"), rule.outer_timestamp_format)
    if rule.strategy == "wrapper_embedded" and rule.embedded_regex:
        embedded_match = re.search(rule.embedded_regex, outer_message)
        if embedded_match:
            embedded_groups = embedded_match.groupdict()
            embedded_timestamp = _parse_datetime(
                embedded_groups.get("timestamp"),
                rule.embedded_timestamp_format,
                outer_date=outer_timestamp.date() if outer_timestamp and rule.embedded_uses_outer_date else None,
            )
            return _build_dynamic_event(
                timestamp=embedded_timestamp or outer_timestamp,
                level=str(embedded_groups.get("level") or outer_groups.get("level") or "UNKNOWN"),
                thread=_clean(embedded_groups.get("thread")) or _clean(outer_groups.get("thread")),
                logger=_clean(embedded_groups.get("logger")) or rule.logger_literal or _clean(outer_groups.get("logger")),
                message=str(embedded_groups.get("message") or outer_message),
                source_file=source_file,
                line_number=line_number,
                raw_line=raw_line,
            )
    return _build_dynamic_event(
        timestamp=outer_timestamp,
        level=str(outer_groups.get("level") or "UNKNOWN"),
        thread=_clean(outer_groups.get("thread")),
        logger=rule.logger_literal or _clean(outer_groups.get("logger")),
        message=outer_message,
        source_file=source_file,
        line_number=line_number,
        raw_line=raw_line,
    )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: object, fmt: str | None, outer_date: date | None = None) -> datetime | None:
    if value is None or fmt is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    fmt = _normalize_datetime_format(fmt)
    try:
        return datetime.strptime(text, fmt)
    except ValueError:
        if outer_date is not None:
            try:
                return datetime.strptime(f"{outer_date.isoformat()} {text}", f"%Y-%m-%d {fmt}")
            except ValueError:
                return None
        return None


def _normalize_datetime_format(fmt: str) -> str:
    replacements = [
        ("yyyy", "%Y"),
        ("MM", "%m"),
        ("dd", "%d"),
        ("HH", "%H"),
        ("mm", "%M"),
        ("ss", "%S"),
        ("SSS", "%f"),
    ]
    normalized = fmt
    for source, target in replacements:
        normalized = normalized.replace(source, target)
    return normalized


def _build_dynamic_event(
    *,
    timestamp: datetime | None,
    level: str,
    thread: str | None,
    logger: str | None,
    message: str,
    source_file: str,
    line_number: int,
    raw_line: str,
) -> LogEvent:
    from .parser import build_structured_event

    return build_structured_event(
        timestamp=timestamp,
        level=level,
        thread=thread,
        logger=logger,
        message=message,
        source_file=source_file,
        line_number=line_number,
        raw_line=raw_line,
    )


def score_event(event: LogEvent) -> int:
    score = 0
    if event.level and event.level != "UNKNOWN":
        score += 2
    if event.timestamp is not None:
        score += 2
    if event.logger:
        score += 1
    if event.thread:
        score += 1
    if event.exception_keywords:
        score += 1
    return score


def validate_dynamic_rule(
    source_file: str,
    sample_lines: list[dict[str, object]],
    rule: DynamicParseRule,
    baseline_parser,
    min_improvement: int = 3,
) -> AdaptiveRuleDecision:
    builtin_score = 0
    dynamic_score = 0
    for item in sample_lines:
        line_number = int(item["line_number"])
        text = str(item["text"])
        builtin_event = baseline_parser(text, source_file, line_number)
        dynamic_event = parse_with_dynamic_rule(text, source_file, line_number, rule)
        builtin_score += score_event(builtin_event)
        dynamic_score += score_event(dynamic_event) if dynamic_event is not None else 0
    if dynamic_score >= builtin_score + min_improvement:
        return AdaptiveRuleDecision(
            source_file=source_file,
            enabled=True,
            reason="dynamic_rule_improved_sample_score",
            sample_line_count=len(sample_lines),
            builtin_score=builtin_score,
            dynamic_score=dynamic_score,
            rule=rule,
        )
    return AdaptiveRuleDecision(
        source_file=source_file,
        enabled=False,
        reason="dynamic_rule_not_better_than_builtin",
        sample_line_count=len(sample_lines),
        builtin_score=builtin_score,
        dynamic_score=dynamic_score,
        rule=rule,
    )
