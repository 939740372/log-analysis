from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .config import AnalysisConfig
from .models import EvidenceItem, Issue, LogEvent, StageOneSummary
from .parser import iter_log_events


@dataclass
class EndpointStats:
    count: int = 0
    total_cost: int = 0
    slow_count: int = 0
    max_cost: int = 0
    error_count: int = 0
    warn_count: int = 0
    downstream_targets: Counter = field(default_factory=Counter)

    def record(self, event: LogEvent, slow_threshold_ms: int) -> None:
        self.count += 1
        if event.cost_ms is not None:
            self.total_cost += event.cost_ms
            self.max_cost = max(self.max_cost, event.cost_ms)
            if event.cost_ms >= slow_threshold_ms:
                self.slow_count += 1
        if event.level == "ERROR":
            self.error_count += 1
        if event.level == "WARN":
            self.warn_count += 1
        if event.downstream_uri:
            self.downstream_targets[event.downstream_uri] += 1

    def to_dict(self, endpoint: str) -> dict:
        average_cost = self.total_cost / self.count if self.count else 0
        return {
            "endpoint": endpoint,
            "count": self.count,
            "average_cost_ms": round(average_cost, 2),
            "max_cost_ms": self.max_cost,
            "slow_count": self.slow_count,
            "error_count": self.error_count,
            "warn_count": self.warn_count,
            "downstream_targets": self.downstream_targets.most_common(5),
        }


def _redact(text: str) -> str:
    import re

    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.\w+\b", "<redacted-email>", text)
    text = re.sub(r"\b1[3-9]\d{9}\b", "<redacted-phone>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<redacted-ip>", text)
    text = re.sub(r'"(?:s|t|token|Authorization)"\s*:\s*"[^"]+"', '"token":"<redacted-token>"', text, flags=re.IGNORECASE)
    return text


def _build_issue(
    title: str,
    category: str,
    severity: str,
    description: str,
    events: list[LogEvent],
    max_issue_evidence: int,
) -> Issue:
    evidence = [
        EvidenceItem(
            label=title,
            source=event.source_file,
            line_number=event.line_number,
            excerpt=event.raw_line[:600],
        )
        for event in events[:max_issue_evidence]
    ]
    endpoints = sorted({event.request_uri for event in events if event.request_uri})
    classes = sorted({event.logger for event in events if event.logger})
    keywords = sorted({keyword for event in events for keyword in event.exception_keywords})
    trace_ids = sorted({event.trace_id for event in events if event.trace_id})
    return Issue(
        title=title,
        category=category,
        severity=severity,
        description=description,
        evidence=evidence,
        related_endpoints=endpoints,
        related_classes=classes,
        related_keywords=keywords,
        trace_ids=trace_ids[:10],
    )


def analyze_logs(log_paths: list[Path], config: AnalysisConfig) -> StageOneSummary:
    level_counter: Counter[str] = Counter()
    endpoint_stats: dict[str, EndpointStats] = defaultdict(EndpointStats)
    file_stats: list[dict] = []
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    total_lines = 0
    issues_by_type: dict[str, list[LogEvent]] = defaultdict(list)
    trace_samples: dict[str, list[LogEvent]] = {}

    for log_path in log_paths:
        file_line_count = 0
        file_levels: Counter[str] = Counter()
        for event in iter_log_events(log_path):
            file_line_count += 1
            total_lines += 1
            level = event.level or "UNKNOWN"
            level_counter[level] += 1
            file_levels[level] += 1

            if event.timestamp:
                if first_timestamp is None or event.timestamp < first_timestamp:
                    first_timestamp = event.timestamp
                if last_timestamp is None or event.timestamp > last_timestamp:
                    last_timestamp = event.timestamp

            if event.request_uri:
                endpoint_stats[event.request_uri].record(event, config.slow_request_threshold_ms)

            if event.level == "WARN":
                issues_by_type["warn"].append(event)
            if event.level == "ERROR":
                issues_by_type["error"].append(event)
            if event.cost_ms is not None and event.cost_ms >= config.slow_request_threshold_ms:
                issues_by_type["slow"].append(event)
            if any("timed out" in keyword.lower() for keyword in event.exception_keywords):
                issues_by_type["timeout"].append(event)
            if any("discard long time none received connection" in keyword.lower() for keyword in event.exception_keywords):
                issues_by_type["db_idle_connection"].append(event)
            if "Fallback Reason" in event.raw_line:
                issues_by_type["feign_fallback"].append(event)

            interesting = (
                event.level in {"WARN", "ERROR"}
                or event.cost_ms is not None
                and event.cost_ms >= config.slow_request_threshold_ms
                or bool(event.exception_keywords)
            )
            if interesting and event.trace_id:
                bucket = trace_samples.setdefault(event.trace_id, [])
                if len(trace_samples) <= config.trace_sample_limit and len(bucket) < config.per_trace_event_limit:
                    bucket.append(event)

        file_stats.append(
            {
                "file": str(log_path),
                "line_count": file_line_count,
                "levels": dict(file_levels),
            }
        )

    issues: list[Issue] = []
    if issues_by_type["timeout"]:
        issues.append(
            _build_issue(
                "Feign/HTTP 超时风险",
                "超时",
                "高",
                "检测到超时相关日志，较可能影响跨服务调用或下游依赖稳定性。",
                issues_by_type["timeout"],
                config.max_issue_evidence,
            )
        )
    if issues_by_type["db_idle_connection"]:
        issues.append(
            _build_issue(
                "数据库空闲连接被丢弃",
                "数据库",
                "中",
                "Druid 丢弃了长时间空闲的 MySQL 连接，说明连接池保活或校验配置可能存在缺口。",
                issues_by_type["db_idle_connection"],
                config.max_issue_evidence,
            )
        )
    if issues_by_type["feign_fallback"]:
        issues.append(
            _build_issue(
                "Feign fallback 被触发",
                "依赖调用",
                "高",
                "下游请求触发了 fallback，说明容错逻辑可能掩盖了真实的依赖失败。",
                issues_by_type["feign_fallback"],
                config.max_issue_evidence,
            )
        )
    if issues_by_type["slow"]:
        issues.append(
            _build_issue(
                "慢请求聚集",
                "性能",
                "中",
                "存在大量请求超过慢请求阈值，需要重点检查热点路径或依赖侧耗时。",
                sorted(issues_by_type["slow"], key=lambda event: event.cost_ms or 0, reverse=True),
                config.max_issue_evidence,
            )
        )
    if issues_by_type["error"] and not any(issue.category == "dependency" for issue in issues):
        issues.append(
            _build_issue(
                "存在未处理错误日志",
                "应用",
                "高",
                "检测到 ERROR 级别日志，建议进一步排查对应代码路径。",
                issues_by_type["error"],
                config.max_issue_evidence,
            )
        )

    endpoints = [
        stats.to_dict(endpoint)
        for endpoint, stats in sorted(
            endpoint_stats.items(),
            key=lambda item: (item[1].count, item[1].max_cost),
            reverse=True,
        )[: config.top_n_endpoints]
    ]

    rendered_trace_samples = []
    for trace_id, events in list(trace_samples.items())[: config.trace_sample_limit]:
        rendered_trace_samples.append(
            {
                "trace_id": trace_id,
                "events": [event.to_dict() for event in events],
            }
        )

    overview = {
        "total_files": len(log_paths),
        "total_lines": total_lines,
        "time_range": {
            "start": first_timestamp.isoformat(sep=" ") if first_timestamp else None,
            "end": last_timestamp.isoformat(sep=" ") if last_timestamp else None,
        },
        "level_distribution": dict(level_counter),
        "issue_counts": {key: len(value) for key, value in issues_by_type.items() if value},
    }

    if config.redact_sensitive:
        for issue in issues:
            for evidence in issue.evidence:
                evidence.excerpt = _redact(evidence.excerpt)
        for trace_sample in rendered_trace_samples:
            for event in trace_sample["events"]:
                event["raw_line"] = _redact(event["raw_line"])
                event["message"] = _redact(event["message"])

    return StageOneSummary(
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        files=file_stats,
        overview=overview,
        endpoints=endpoints,
        issues=issues,
        trace_samples=rendered_trace_samples,
    )
