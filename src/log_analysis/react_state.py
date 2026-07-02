from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class AgentAction:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentObservation:
    tool: str
    success: bool
    content: dict[str, Any] | list[Any] | str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentStep:
    index: int
    thought: str
    action: AgentAction | None = None
    observation: AgentObservation | None = None
    final: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "thought": self.thought,
            "action": self.action.to_dict() if self.action else None,
            "observation": self.observation.to_dict() if self.observation else None,
            "final": self.final,
        }


@dataclass
class AgentIssue:
    issue_id: str
    title: str
    priority: str
    description: str
    observed_facts: list[str]
    evidence: list[dict[str, Any]]
    linked_code: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        from .code_display import group_code_reference_dicts

        data = asdict(self)
        data["linked_code_groups"] = group_code_reference_dicts(self.linked_code)
        return data


@dataclass
class AgentRunState:
    run_id: str
    stage: str
    goal: str
    issue: AgentIssue
    log_files: list[str]
    source_root: str | None
    max_steps: int
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    steps: list[AgentStep] = field(default_factory=list)
    completed: bool = False
    status: str = "running"
    failure_reason: str | None = None
    final_answer: dict[str, Any] | None = None

    def add_step(self, step: AgentStep) -> None:
        self.steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "goal": self.goal,
            "issue": self.issue.to_dict(),
            "log_files": self.log_files,
            "source_root": self.source_root,
            "max_steps": self.max_steps,
            "created_at": self.created_at,
            "steps": [step.to_dict() for step in self.steps],
            "completed": self.completed,
            "status": self.status,
            "last_tool": self.last_tool,
            "failure_reason": self.failure_reason,
            "final_answer": self.final_answer,
        }

    def previous_actions(self, tool_name: str) -> list[AgentAction]:
        return [step.action for step in self.steps if step.action and step.action.tool == tool_name]

    @property
    def last_tool(self) -> str | None:
        for step in reversed(self.steps):
            if step.action:
                return step.action.tool
        return None
