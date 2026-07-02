import tempfile
import unittest
from pathlib import Path

from log_analysis.config import LLMConfig
from log_analysis.react_cli import build_parser
from log_analysis.react_controller import ReActAgentController, write_multi_issue_summary
from log_analysis.react_state import AgentAction, AgentIssue, AgentObservation, AgentRunState, AgentStep


class _FakeLLMResult:
    def __init__(self, parsed_json):
        self.parsed_json = parsed_json
        self.raw_text = ""


class _FakeClient:
    def __init__(self, responses, retry_count: int = 3, retry_backoff_seconds: float = 0.0):
        self.responses = list(responses)
        self.config = LLMConfig(retry_count=retry_count, retry_backoff_seconds=retry_backoff_seconds)

    def create_chat_completion(self, messages, max_completion_tokens=None):  # noqa: ANN001, ARG002
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return _FakeLLMResult(response)


class _FakeToolRegistry:
    def specs(self):
        return [
            {"name": "search_logs", "description": "search logs", "arguments_schema": {"query": "string"}},
            {"name": "search_code_symbols", "description": "search code", "arguments_schema": {"query": "string"}},
            {"name": "open_code_context", "description": "open code", "arguments_schema": {"file_path": "string"}},
        ]

    def run(self, tool_name, arguments):  # noqa: ANN001
        if tool_name == "search_logs":
            return {"matches": [{"file": "app.log", "line_number": 1, "excerpt": "error line"}]}
        if tool_name == "search_code_symbols":
            return {"matches": [{"file_path": "/tmp/App.java", "line_number": 8, "snippet": "class App {}"}]}
        if tool_name == "open_code_context":
            return {"context": [{"line_number": 8, "content": "public void handle() {}"}]}
        raise ValueError(tool_name)


def _build_issue() -> AgentIssue:
    return AgentIssue(
        issue_id="issue-1",
        title="测试问题",
        priority="high",
        description="测试描述",
        observed_facts=["存在错误日志", "已关联到候选代码"],
        evidence=[],
        linked_code=[{"file_path": "/tmp/App.java", "line_number": 8, "reason": "class match"}],
    )


class ReactControllerTests(unittest.TestCase):
    def test_parser_exposes_react_runtime_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "a.log",
                "--max-steps",
                "4",
                "--parallelism",
                "2",
                "--retry-count",
                "5",
            ]
        )
        self.assertEqual(args.max_steps, 4)
        self.assertEqual(args.parallelism, 2)
        self.assertEqual(args.retry_count, 5)

    def test_run_issue_finishes_early_with_useful_evidence(self) -> None:
        client = _FakeClient(
            [
                {"thought": "先查日志", "action": {"tool": "search_logs", "arguments": {"query": "error"}}},
                {"thought": "再查代码", "action": {"tool": "search_code_symbols", "arguments": {"query": "App"}}},
            ]
        )
        controller = ReActAgentController(client, _FakeToolRegistry(), max_steps=4)
        state = controller.run_issue(
            issue_title="测试问题",
            issue_priority="high",
            issue_description="测试描述",
            observed_facts=["存在错误日志", "已关联到候选代码"],
            evidence=[],
            linked_code=[{"file_path": "/tmp/App.java", "line_number": 8, "reason": "class match"}],
            log_files=["/tmp/app.log"],
            source_root="/tmp",
        )
        self.assertTrue(state.completed)
        self.assertEqual(state.status, "completed")
        self.assertLess(len(state.steps), 4)
        self.assertIsNotNone(state.final_answer)

    def test_write_multi_issue_summary_contains_status_fields(self) -> None:
        run_state = AgentRunState(
            run_id="run-1",
            stage="react_issue_investigation",
            goal="调查测试问题",
            issue=_build_issue(),
            log_files=["/tmp/app.log"],
            source_root="/tmp",
            max_steps=4,
            completed=True,
            status="stopped_no_progress",
            failure_reason="最近两步都没有拿到有效新证据",
        )
        run_state.add_step(
            AgentStep(
                index=1,
                thought="测试",
                action=AgentAction(tool="search_logs", arguments={"query": "error"}),
                observation=AgentObservation(
                    tool="search_logs",
                    success=False,
                    content={"error": "no progress"},
                    summary="no progress",
                ),
            )
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            write_multi_issue_summary(Path(tmp_dir), [run_state])
            content = (Path(tmp_dir) / "react_summary.json").read_text(encoding="utf-8")
        self.assertIn('"status": "stopped_no_progress"', content)
        self.assertIn('"last_tool": "search_logs"', content)
        self.assertIn('"failure_reason": "最近两步都没有拿到有效新证据"', content)


if __name__ == "__main__":
    unittest.main()
