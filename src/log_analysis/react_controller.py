from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .llm import LLMError, OpenAICompatibleLLMClient
from .react_prompts import build_final_markdown, build_multi_issue_markdown, build_planner_messages
from .react_state import AgentAction, AgentIssue, AgentObservation, AgentRunState, AgentStep
from .react_tools import ReactToolRegistry


class ReActAgentController:
    def __init__(
        self,
        client: OpenAICompatibleLLMClient,
        tool_registry: ReactToolRegistry,
        max_steps: int = 6,
    ):
        self.client = client
        self.tool_registry = tool_registry
        self.max_steps = max_steps

    def run_issue(
        self,
        issue_title: str,
        issue_priority: str,
        issue_description: str,
        observed_facts: list[str],
        evidence: list[dict],
        linked_code: list[dict],
        log_files: list[str],
        source_root: str | None,
    ) -> AgentRunState:
        issue = AgentIssue(
            issue_id=str(uuid.uuid4()),
            title=issue_title,
            priority=issue_priority,
            description=issue_description,
            observed_facts=observed_facts,
            evidence=evidence,
            linked_code=linked_code,
        )
        state = AgentRunState(
            run_id=str(uuid.uuid4()),
            stage="react_issue_investigation",
            goal=f"调查问题：{issue_title}",
            issue=issue,
            log_files=log_files,
            source_root=source_root,
            max_steps=self.max_steps,
        )
        self._current_state_actions = {}

        for step_index in range(1, self.max_steps + 1):
            heuristic_final = self._maybe_finalize_early(state)
            if heuristic_final is not None:
                step = AgentStep(index=step_index, thought="现有证据已足够形成阶段性结论，提前结束调查。", final=heuristic_final)
                state.add_step(step)
                state.completed = True
                if state.status == "running":
                    state.status = "completed"
                state.final_answer = heuristic_final
                self._current_state_actions = {}
                return state

            plan = self._plan_next_step(state)
            thought = str(plan.get("thought") or "继续收集证据")
            final = plan.get("final")
            if isinstance(final, dict):
                step = AgentStep(index=step_index, thought=thought, final=final)
                state.add_step(step)
                state.completed = True
                state.status = "completed"
                state.failure_reason = None
                state.final_answer = final
                self._current_state_actions = {}
                return state

            action_data = plan.get("action") or {}
            action = AgentAction(
                tool=str(action_data.get("tool") or ""),
                arguments=action_data.get("arguments") or {},
            )
            observation = self._execute_action(action)
            self._current_state_actions.setdefault(action.tool, []).append(action)
            state.add_step(AgentStep(index=step_index, thought=thought, action=action, observation=observation))

        state.completed = False
        state.status = "max_steps_reached"
        state.failure_reason = "达到最大步数仍未收敛"
        state.final_answer = {
            "title": state.issue.title,
            "summary": "达到最大步数，调查尚未完成。",
            "observed_facts": state.issue.observed_facts,
            "linked_code": state.issue.linked_code,
            "fix_direction": "需要继续追加调查步骤。",
            "llm_suggestion": "当前运行达到最大步数，请扩大 max_steps 或补充更多工具。",
            "risks": ["证据不足，结论不完整。"],
        }
        self._current_state_actions = {}
        return state

    def _plan_next_step(self, state: AgentRunState) -> dict:
        messages = build_planner_messages(state, self.tool_registry.specs())
        last_error = "Planner did not return a valid JSON object"
        for attempt in range(1, self.client.config.retry_count + 1):
            try:
                result = self.client.create_chat_completion(messages, max_completion_tokens=900)
                if isinstance(result.parsed_json, dict):
                    return result.parsed_json
            except LLMError as exc:
                last_error = str(exc)
            if attempt < self.client.config.retry_count:
                time.sleep(self.client.config.retry_backoff_seconds * attempt)
        raise LLMError(last_error)

    def _execute_action(self, action: AgentAction) -> AgentObservation:
        constraint_error = self._validate_action(action)
        if constraint_error is not None:
            return AgentObservation(
                tool=action.tool,
                success=False,
                content={"error": constraint_error},
                summary=constraint_error,
            )
        try:
            content = self.tool_registry.run(action.tool, action.arguments)
            summary = _summarize_observation(action.tool, content)
            return AgentObservation(tool=action.tool, success=True, content=content, summary=summary)
        except Exception as exc:  # noqa: BLE001
            return AgentObservation(
                tool=action.tool,
                success=False,
                content={"error": str(exc)},
                summary=f"工具执行失败：{exc}",
            )

    def _validate_action(self, action: AgentAction) -> str | None:
        if action.tool != "open_code_context":
            return None
        file_path = action.arguments.get("file_path")
        line_number = action.arguments.get("line_number")
        before = action.arguments.get("before", 10)
        after = action.arguments.get("after", 20)
        current = (file_path, line_number, before, after)
        for previous in self.tool_registry_state_actions("open_code_context"):
            previous_key = (
                previous.arguments.get("file_path"),
                previous.arguments.get("line_number"),
                previous.arguments.get("before", 10),
                previous.arguments.get("after", 20),
            )
            if previous_key == current:
                return "拒绝重复调用相同的 open_code_context，请先改用 search_logs、search_code_symbols 或更换代码位置。"
        return None

    def _maybe_finalize_early(self, state: AgentRunState) -> dict | None:
        if not state.steps:
            return None
        successful_observations = [step.observation for step in state.steps if step.observation and step.observation.success]
        if len(successful_observations) < 2:
            return None

        opened_code = [
            step for step in state.steps
            if step.action
            and step.action.tool == "open_code_context"
            and step.observation
            and step.observation.success
            and self._observation_has_useful_content(step.observation.content)
        ]
        useful_tools = {
            step.action.tool
            for step in state.steps
            if step.action and step.observation and step.observation.success and self._observation_has_useful_content(step.observation.content)
        }
        has_log_hits = any(
            step.action
            and step.action.tool == "search_logs"
            and step.observation
            and step.observation.success
            and self._observation_has_useful_content(step.observation.content)
            for step in state.steps
        )
        has_code_hits = any(
            step.action
            and step.action.tool in {"search_code_symbols", "get_config_matches"}
            and step.observation
            and step.observation.success
            and self._observation_has_useful_content(step.observation.content)
            for step in state.steps
        )

        if state.issue.linked_code and opened_code and len(useful_tools) >= 2:
            return {
                "title": state.issue.title,
                "summary": "已拿到关键日志事实与对应代码上下文，足以形成阶段性结论。",
                "observed_facts": state.issue.observed_facts[:6],
                "linked_code": state.issue.linked_code[:5],
                "fix_direction": "基于已定位的日志事实和代码上下文，建议优先围绕命中方法与关联配置做针对性排查和修复。",
                "llm_suggestion": "当前证据已足够进入工程修复阶段；如需更深调查，可进一步补充具体异常堆栈、变量值或数据库/下游接口返回。",
                "risks": ["当前结论是阶段性结论，若缺少完整异常堆栈，根因仍可能存在分支偏差。"],
            }

        if has_log_hits and has_code_hits and len(useful_tools) >= 2:
            return {
                "title": state.issue.title,
                "summary": "日志检索和源码检索都已命中关键线索，当前证据已足够形成阶段性方向。",
                "observed_facts": state.issue.observed_facts[:6],
                "linked_code": state.issue.linked_code[:5],
                "fix_direction": "建议围绕当前已命中的日志关键词、相关类和配置项继续做定向修复，不必继续扩大检索范围。",
                "llm_suggestion": "当前子问题已经从“现象定位”收敛到“候选修复点定位”，可以进入修复建议输出阶段。",
                "risks": ["若源码命中较分散，仍需工程师二次确认最终落点。"],
            }

        repeated_failures = [step for step in state.steps[-2:] if step.observation and not step.observation.success]
        if len(repeated_failures) == 2:
            state.completed = True
            state.status = "stopped_no_progress"
            state.failure_reason = "最近两步都没有拿到有效新证据"
            return {
                "title": state.issue.title,
                "summary": "最近两步工具调用都未产生有效新证据，提前结束当前调查。",
                "observed_facts": state.issue.observed_facts[:6],
                "linked_code": state.issue.linked_code[:5],
                "fix_direction": "建议切换调查策略，优先补充更明确的检索关键词、完整异常栈或更多源码线索。",
                "llm_suggestion": "当前调查路径收益较低，应调整问题切入点后重新发起调查。",
                "risks": ["由于最近步骤没有新证据，本次结论完整性有限。"],
            }
        return None

    def tool_registry_state_actions(self, tool_name: str) -> list[AgentAction]:
        # Controller 内部在 run_issue 过程中通过闭包绑定 state.steps，不单独持有状态。
        return getattr(self, "_current_state_actions", {}).get(tool_name, [])

    def _observation_has_useful_content(self, content: dict | list | str) -> bool:
        if isinstance(content, dict):
            matches = content.get("matches")
            if isinstance(matches, list) and matches:
                return True
            context = content.get("context")
            if isinstance(context, list) and context:
                return True
            files = content.get("files")
            if isinstance(files, list) and files:
                return True
            head = content.get("head")
            tail = content.get("tail")
            if isinstance(head, list) and head:
                return True
            if isinstance(tail, list) and tail:
                return True
            return False
        if isinstance(content, list):
            return bool(content)
        return bool(str(content).strip())


def _summarize_observation(tool: str, content: dict | list | str) -> str:
    if isinstance(content, dict):
        if "matches" in content and isinstance(content["matches"], list):
            return f"{tool} 返回 {len(content['matches'])} 条匹配"
        if "files" in content and isinstance(content["files"], list):
            return f"{tool} 返回 {len(content['files'])} 个文件"
        if "context" in content and isinstance(content["context"], list):
            return f"{tool} 返回 {len(content['context'])} 行代码上下文"
    if isinstance(content, list):
        return f"{tool} 返回 {len(content)} 条记录"
    return f"{tool} 已执行"


def write_react_run(output_dir: Path, run_state: AgentRunState) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "react_run.json").write_text(json.dumps(run_state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "react_run.md").write_text(build_final_markdown(run_state), encoding="utf-8")


def write_multi_issue_summary(output_dir: Path, run_states: list[AgentRunState]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "runs": [state.to_dict() for state in run_states],
        "summary": [
            {
                "run_id": state.run_id,
                "issue_title": state.issue.title,
                "status": state.status,
                "completed": state.completed,
                "last_tool": state.last_tool,
                "failure_reason": state.failure_reason,
                "steps": len(state.steps),
            }
            for state in run_states
        ],
    }
    (output_dir / "react_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "react_summary.md").write_text(build_multi_issue_markdown(run_states), encoding="utf-8")


def run_issues_concurrently(
    controller_factory,
    issues: list[dict],
    log_files: list[str],
    source_root: str | None,
    max_workers: int,
) -> list[AgentRunState]:
    results: dict[int, AgentRunState] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _run_one_issue_with_factory,
                controller_factory,
                issue,
                log_files,
                source_root,
            ): index
            for index, issue in enumerate(issues)
        }
        for future in as_completed(future_map):
            index = future_map[future]
            results[index] = future.result()
    return [results[index] for index in sorted(results)]


def _run_one_issue_with_factory(controller_factory, issue: dict, log_files: list[str], source_root: str | None) -> AgentRunState:
    controller = controller_factory()
    return controller.run_issue(
        issue_title=issue["title"],
        issue_priority=issue["priority"],
        issue_description=issue["description"],
        observed_facts=issue["observed_facts"],
        evidence=issue["evidence"],
        linked_code=issue["linked_code"],
        log_files=log_files,
        source_root=source_root,
    )
