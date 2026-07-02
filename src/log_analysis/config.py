from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = "http://10.130.61.232:8002"
    chat_path: str = "/v1/chat/completions"
    model: str = "InstructModelQwen3"
    temperature: float = 0.1
    timeout_seconds: int = 60
    max_completion_tokens: int = 1200
    retry_count: int = 3
    retry_backoff_seconds: float = 1.0
    stage_two_parallelism: int = 4

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.chat_path}"


@dataclass(frozen=True)
class AnalysisConfig:
    output_dir: Path
    slow_request_threshold_ms: int = 1000
    top_n_endpoints: int = 20
    trace_sample_limit: int = 12
    per_trace_event_limit: int = 8
    max_issue_evidence: int = 5
    max_llm_samples: int = 12
    max_code_matches: int = 30
    redact_sensitive: bool = True
    adaptive_sample_lines: int = 80
    adaptive_validation_improvement: int = 3
