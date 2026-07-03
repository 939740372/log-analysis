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
TIE_ENABLE_CANDIDATES = {"wrapper_embedded_spring", "nacos_dubbo_bootstrap"}
WHITELIST_GAPS = {
    "wrapper_embedded_spring": 0,
    "nacos_dubbo_bootstrap": 0,
    "time_only_method": 25,
}
DATE_FROM_FILENAME_PATTERN = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class DynamicParseRule:
    format_name: str
    strategy: str
    outer_regex: str
    outer_timestamp_format: str | None
    use_source_file_date: bool = False
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
            "use_source_file_date": self.use_source_file_date,
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


RULE_CANDIDATES: dict[str, DynamicParseRule] = {
    "spring_tid_trace": DynamicParseRule(
        format_name="Spring TID Trace",
        strategy="direct",
        outer_regex=(
            r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) "
            r"\[(?P<thread>[^\]]*)\] "
            r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
            r"(?P<logger>[^\[]+?) "
            r"\[TID: [^\]]*\] "
            r"\[[^\]\s]{6,}\] - "
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="yyyy-MM-dd HH:mm:ss.SSS",
    ),
    "spring_json_plain": DynamicParseRule(
        format_name="Spring JSON Plain",
        strategy="direct",
        outer_regex=(
            r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) "
            r"\[(?P<thread>[^\]]*)\] "
            r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
            r"(?P<logger>\S+)\s+-\s+"
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="yyyy-MM-dd HH:mm:ss.SSS",
    ),
    "spring_boot_pid_thread": DynamicParseRule(
        format_name="Spring Boot PID Thread",
        strategy="direct",
        outer_regex=(
            r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+"
            r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+\d+\s+---\s+\[(?P<thread>[^\]]+)\]\s+"
            r"(?P<logger>\S+)\s+:\s+"
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="yyyy-MM-dd HH:mm:ss.SSS",
    ),
    "time_only_method": DynamicParseRule(
        format_name="Time Only Method Log",
        strategy="direct",
        outer_regex=(
            r"^(?P<timestamp>\d{2}:\d{2}:\d{2}\.\d{3}) "
            r"\[(?P<thread>[^\]]*)\] "
            r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
            r"(?P<logger>\S+)\s+-\s+"
            r"\[[^\]]+\]\s+-\s+"
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="HH:mm:ss.SSS",
        use_source_file_date=True,
    ),
    "nacos_dubbo_bootstrap": DynamicParseRule(
        format_name="Nacos Dubbo Bootstrap",
        strategy="direct",
        outer_regex=(
            r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+"
            r"\[(?P<thread>[^\]]+)\]\s+"
            r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
            r"(?P<logger>\S+)\s+"
            r"\[TID:\s*[^\]]+\]\s+"
            r"\[[^\]]*\]\s+-\s+"
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="yyyy-MM-dd HH:mm:ss.SSS",
    ),
    "wrapper_embedded_spring": DynamicParseRule(
        format_name="JavaWrapper Spring Embedded",
        strategy="wrapper_embedded",
        outer_regex=(
            r"^(?P<level>\w+)\s*\|\s*(?P<thread>[^|]+)\s*\|\s*"
            r"(?P<timestamp>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s*\|\s*"
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="yyyy/MM/dd HH:mm:ss",
        embedded_regex=(
            r"(?P<timestamp>\d{2}:\d{2}:\d{2}\.\d{3}) "
            r"\[(?P<thread>[^\]]*)\] "
            r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
            r"(?P<logger>\S+)\s+-\s+"
            r"\[[^\]]+\]\s+-\s+"
            r"(?P<message>.*)$"
        ),
        embedded_timestamp_format="HH:mm:ss.SSS",
        embedded_uses_outer_date=True,
        logger_literal="wrapper",
    ),
    "tanuki_wrapper_plain": DynamicParseRule(
        format_name="Tanuki Wrapper Plain",
        strategy="direct",
        outer_regex=(
            r"^(?P<level>\w+)\s*\|\s*(?P<thread>[^|]+)\s*\|\s*"
            r"(?P<timestamp>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s*\|\s*"
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="yyyy/MM/dd HH:mm:ss",
        logger_literal="wrapper",
    ),
    "tomcat_catalina_juli": DynamicParseRule(
        format_name="Tomcat Catalina JULI",
        strategy="direct",
        outer_regex=(
            r"^(?P<timestamp>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2}\.\d{3})\s+"
            r"(?P<level>INFO|WARN|ERROR|DEBUG|TRACE)\s+"
            r"\[(?P<thread>[^\]]+)\]\s+"
            r"(?P<logger>\S+)\s+"
            r"(?P<message>.*)$"
        ),
        outer_timestamp_format="dd-MMM-yyyy HH:mm:ss.SSS",
    ),
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
    ranked_candidates = rank_candidate_rules(sample_lines, str(log_path))
    payload = {
        "file_name": log_path.name,
        "sample_lines": sample_lines,
        "candidate_rules": [
            {"candidate_name": name, "rule": rule.to_dict(), "sample_score": score}
            for name, rule, score in ranked_candidates
        ],
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
            fallback_rule = _fallback_rule_from_ranked_candidates(ranked_candidates)
            if fallback_rule is not None:
                return fallback_rule, raw_response, "LLM 返回的解析规则不合法，已回退到本地高置信候选规则"
            last_error = "LLM 返回的解析规则不合法"
        except LLMError as exc:
            last_error = str(exc)
        if attempt < client.config.retry_count:
            import time

            time.sleep(client.config.retry_backoff_seconds * attempt)
    return None, raw_response, last_error


def _fallback_rule_from_ranked_candidates(
    ranked_candidates: list[tuple[str, DynamicParseRule, int]],
) -> DynamicParseRule | None:
    if not ranked_candidates:
        return None
    top_name, top_rule, top_score = ranked_candidates[0]
    second_score = ranked_candidates[1][2] if len(ranked_candidates) > 1 else 0
    if top_score <= 0:
        return None
    if top_score >= second_score + 80:
        return top_rule
    if top_name == "spring_boot_pid_thread" and top_score >= second_score + 20:
        return top_rule
    return None


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
    if "candidate_name" in parsed:
        return _normalize_candidate_selection(parsed)
    try:
        strategy = str(parsed["strategy"])
        outer_regex = str(parsed["outer_regex"])
        outer_timestamp_format = parsed.get("outer_timestamp_format")
        use_source_file_date = bool(parsed.get("use_source_file_date", False))
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
        use_source_file_date=use_source_file_date,
        embedded_regex=embedded_regex,
        embedded_timestamp_format=embedded_timestamp_format,
        embedded_uses_outer_date=embedded_uses_outer_date,
        logger_literal=logger_literal,
    )


def _normalize_candidate_selection(parsed: dict) -> DynamicParseRule | None:
    candidate_name = parsed.get("candidate_name")
    if not isinstance(candidate_name, str):
        return None
    base_rule = RULE_CANDIDATES.get(candidate_name)
    if base_rule is None:
        return None
    tuned_fields = parsed.get("tuned_fields") or {}
    if tuned_fields and not isinstance(tuned_fields, dict):
        return None
    merged = base_rule.to_dict()
    if tuned_fields:
        for key in (
            "format_name",
            "strategy",
            "outer_regex",
            "outer_timestamp_format",
            "use_source_file_date",
            "embedded_regex",
            "embedded_timestamp_format",
            "embedded_uses_outer_date",
            "logger_literal",
        ):
            if key in tuned_fields:
                merged[key] = tuned_fields[key]
    return _normalize_dynamic_rule(merged)


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


def rank_candidate_rules(
    sample_lines: list[dict[str, object]],
    source_file: str,
) -> list[tuple[str, DynamicParseRule, int]]:
    ranked: list[tuple[str, DynamicParseRule, int, int, int]] = []
    for name, rule in RULE_CANDIDATES.items():
        total = 0
        embedded_hits = 0
        for item in sample_lines:
            line_number = int(item["line_number"])
            text = str(item["text"])
            event = parse_with_dynamic_rule(text, source_file, line_number, rule)
            total += _score_rule_match(text, event, rule)
            _, embedded_matched = _inspect_rule_match(text, rule)
            if embedded_matched:
                embedded_hits += 1
        direct_priority = 1 if rule.strategy == "direct" else 0
        ranked.append((name, rule, total, embedded_hits, direct_priority))
    ranked.sort(key=lambda item: (item[2], item[3], item[4]), reverse=True)
    return [(name, rule, total) for name, rule, total, _, _ in ranked[:3]]


def _score_rule_match(text: str, event: LogEvent | None, rule: DynamicParseRule) -> int:
    if event is None:
        return 0
    score = score_event(event)
    outer_message, embedded_matched = _inspect_rule_match(text, rule)
    if rule.strategy == "wrapper_embedded" and rule.embedded_regex:
        if embedded_matched:
            score += 2
    lowered = text.lower()
    if rule.format_name == "JavaWrapper Spring Embedded":
        if embedded_matched and outer_message and re.search(
            r"\d{2}:\d{2}:\d{2}\.\d{3} \[[^\]]+\] (trace|debug|info|warn|error)\s",
            outer_message,
            re.IGNORECASE,
        ):
            score += 2
    if rule.format_name == "Nacos Dubbo Bootstrap":
        if "org.apache.dubbo" in lowered or "com.alibaba.nacos" in lowered:
            score += 1
        if "[tid:" in lowered and "] [] -" in lowered:
            score += 1
    return max(score, 0)


def _inspect_rule_match(text: str, rule: DynamicParseRule) -> tuple[str | None, bool]:
    try:
        outer_match = re.search(rule.outer_regex, text)
    except re.error:
        return None, False
    if not outer_match:
        return None, False
    outer_message = str(outer_match.groupdict().get("message") or text)
    if rule.strategy != "wrapper_embedded" or not rule.embedded_regex:
        return outer_message, False
    try:
        embedded_match = re.search(rule.embedded_regex, outer_message)
    except re.error:
        return outer_message, False
    return outer_message, embedded_match is not None


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
    outer_timestamp = _parse_datetime(
        outer_groups.get("timestamp"),
        rule.outer_timestamp_format,
        source_file=source_file if rule.use_source_file_date else None,
    )
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


def _parse_datetime(
    value: object,
    fmt: str | None,
    outer_date: date | None = None,
    source_file: str | None = None,
) -> datetime | None:
    if value is None or fmt is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    fmt = _normalize_datetime_format(fmt)
    effective_date = outer_date
    if effective_date is None and source_file is not None:
        effective_date = _extract_date_from_source_file(source_file)
    if effective_date is not None and not _format_contains_explicit_date(fmt):
        try:
            return datetime.strptime(f"{effective_date.isoformat()} {text}", f"%Y-%m-%d {fmt}")
        except ValueError:
            return None
    try:
        return datetime.strptime(text, fmt)
    except ValueError:
        pass
    if effective_date is not None:
        try:
            return datetime.strptime(f"{effective_date.isoformat()} {text}", f"%Y-%m-%d {fmt}")
        except ValueError:
            return None
    return None


def _extract_date_from_source_file(source_file: str) -> date | None:
    match = DATE_FROM_FILENAME_PATTERN.search(Path(source_file).name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("date"), "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_contains_explicit_date(fmt: str) -> bool:
    return any(token in fmt for token in ("%Y", "%y", "%m", "%b", "%B", "%d", "%j"))


def _normalize_datetime_format(fmt: str) -> str:
    replacements = [
        ("yyyy", "%Y"),
        ("MMM", "%b"),
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
    candidate_name = next((name for name, candidate in RULE_CANDIDATES.items() if candidate == rule), None)
    for item in sample_lines:
        line_number = int(item["line_number"])
        text = str(item["text"])
        builtin_event = baseline_parser(text, source_file, line_number)
        dynamic_event = parse_with_dynamic_rule(text, source_file, line_number, rule)
        builtin_score += score_event(builtin_event)
        dynamic_score += _score_rule_match(text, dynamic_event, rule)
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
    if candidate_name in TIE_ENABLE_CANDIDATES and dynamic_score >= builtin_score and dynamic_score > 0:
        return AdaptiveRuleDecision(
            source_file=source_file,
            enabled=True,
            reason="dynamic_rule_tie_enabled_for_whitelist_candidate",
            sample_line_count=len(sample_lines),
            builtin_score=builtin_score,
            dynamic_score=dynamic_score,
            rule=rule,
        )
    allowed_gap = WHITELIST_GAPS.get(candidate_name)
    if allowed_gap is not None and dynamic_score > 0 and dynamic_score + allowed_gap >= builtin_score:
        return AdaptiveRuleDecision(
            source_file=source_file,
            enabled=True,
            reason="dynamic_rule_enabled_with_whitelist_gap",
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
