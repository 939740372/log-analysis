from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class LogEvent:
    timestamp: datetime | None
    level: str | None
    thread: str | None
    logger: str | None
    trace_id: str | None
    span_id: str | None
    request_uri: str | None
    cost_ms: int | None
    message: str
    source_file: str
    line_number: int
    direction: str | None = None
    exception_keywords: list[str] = field(default_factory=list)
    downstream_uri: str | None = None
    raw_json: dict[str, Any] | None = None
    raw_line: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.timestamp is not None:
            data["timestamp"] = self.timestamp.isoformat(sep=" ")
        return data


@dataclass
class EvidenceItem:
    label: str
    source: str
    line_number: int
    excerpt: str
    kind: str = "Observed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CodeReference:
    file_path: str
    line_number: int
    snippet: str
    reason: str
    class_name: str | None = None
    method_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Issue:
    title: str
    category: str
    severity: str
    description: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    related_endpoints: list[str] = field(default_factory=list)
    related_classes: list[str] = field(default_factory=list)
    related_keywords: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "evidence": [item.to_dict() for item in self.evidence],
            "related_endpoints": self.related_endpoints,
            "related_classes": self.related_classes,
            "related_keywords": self.related_keywords,
            "trace_ids": self.trace_ids,
        }


@dataclass
class StageOneSummary:
    generated_at: str
    files: list[dict[str, Any]]
    overview: dict[str, Any]
    endpoints: list[dict[str, Any]]
    issues: list[Issue]
    trace_samples: list[dict[str, Any]]
    llm_summary: dict[str, Any] | None = None
    llm_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "files": self.files,
            "overview": self.overview,
            "endpoints": self.endpoints,
            "issues": [issue.to_dict() for issue in self.issues],
            "trace_samples": self.trace_samples,
            "llm_summary": self.llm_summary,
            "llm_error": self.llm_error,
        }


@dataclass
class FixSuggestion:
    title: str
    confidence: str
    observed_facts: list[str]
    linked_code: list[CodeReference]
    fix_direction: str
    llm_suggestion: str
    side_effects: list[str]
    evidence: list[EvidenceItem]

    def to_dict(self) -> dict[str, Any]:
        from .code_display import group_code_references

        return {
            "title": self.title,
            "confidence": self.confidence,
            "observed_facts": self.observed_facts,
            "linked_code": [item.to_dict() for item in self.linked_code],
            "linked_code_groups": group_code_references(self.linked_code),
            "fix_direction": self.fix_direction,
            "llm_suggestion": self.llm_suggestion,
            "side_effects": self.side_effects,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass
class StageTwoSummary:
    generated_at: str
    source_root: str | None
    code_matches: list[CodeReference]
    suggestions: list[FixSuggestion]
    llm_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "source_root": self.source_root,
            "code_matches": [item.to_dict() for item in self.code_matches],
            "suggestions": [item.to_dict() for item in self.suggestions],
            "llm_error": self.llm_error,
        }
