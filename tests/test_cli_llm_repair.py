import unittest

from log_analysis.cli import _repair_stage_two_issue_result
from log_analysis.models import EvidenceItem, Issue


class _FakeResult:
    def __init__(self, parsed_json, raw_text: str) -> None:
        self.parsed_json = parsed_json
        self.raw_text = raw_text


class _FakeClient:
    def __init__(self, parsed_json) -> None:
        self._parsed_json = parsed_json

    def create_chat_completion(self, messages, max_completion_tokens=None):  # noqa: ANN001
        return _FakeResult(self._parsed_json, '{"title":"修复后的建议"}')


class CliRepairTests(unittest.TestCase):
    def test_repair_stage_two_issue_result_returns_fix_suggestion(self) -> None:
        issue = Issue(
            title="测试问题",
            category="应用",
            severity="高",
            description="测试描述",
            evidence=[
                EvidenceItem(
                    label="测试问题",
                    source="/tmp/test.log",
                    line_number=1,
                    excerpt="error line",
                )
            ],
        )
        client = _FakeClient(
            {
                "title": "修复后的建议",
                "confidence": "高",
                "observed_facts": ["看到异常日志"],
                "fix_direction": "补充异常处理",
                "llm_suggestion": "在关键方法增加 try-catch 并补充上下文日志",
                "side_effects": ["可能掩盖原始异常，需要保留完整日志"],
            }
        )
        suggestion = _repair_stage_two_issue_result(client, issue, [], "bad output")
        self.assertIsNotNone(suggestion)
        assert suggestion is not None
        self.assertEqual(suggestion.title, "修复后的建议")
        self.assertEqual(suggestion.fix_direction, "补充异常处理")


if __name__ == "__main__":
    unittest.main()
