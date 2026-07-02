import unittest

from log_analysis.json_view import render_json_document
from log_analysis.markdown_view import render_markdown_document


class MarkdownViewTests(unittest.TestCase):
    def test_render_markdown_document_keeps_utf8_html_and_basic_blocks(self) -> None:
        html_text = render_markdown_document(
            "测试报告.md",
            "# 标题\n\n- 第一项\n- 第二项\n\n```json\n{\"名称\":\"中文\"}\n```",
        )
        self.assertIn('<meta charset="utf-8">', html_text)
        self.assertIn('<h1 id="标题">标题</h1>', html_text)
        self.assertIn("<li>第一项</li>", html_text)
        self.assertIn("中文", html_text)
        self.assertIn("文档目录", html_text)
        self.assertIn("返回报告首页", html_text)
        self.assertIn("code-label", html_text)

    def test_render_json_document_builds_card_view(self) -> None:
        html_text = render_json_document(
            "测试结果.json",
            {"标题": "中文", "列表": [1, {"状态": "正常"}]},
        )
        self.assertIn('<meta charset="utf-8">', html_text)
        self.assertIn("返回报告首页", html_text)
        self.assertIn("badge", html_text)
        self.assertIn("标题", html_text)
        self.assertIn("状态", html_text)


if __name__ == "__main__":
    unittest.main()
