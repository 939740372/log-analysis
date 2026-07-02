import unittest
from pathlib import Path

from log_analysis.aggregator import analyze_logs
from log_analysis.config import AnalysisConfig


class AggregatorTests(unittest.TestCase):
    def test_analyze_logs_builds_issue_summary(self) -> None:
        tmp_dir = Path("test-output")
        tmp_dir.mkdir(exist_ok=True)
        log_file = tmp_dir / "app.log"
        log_file.write_text(
            "\n".join(
                [
                    '2026-07-01 18:09:32.776 [exec] INFO  c.Logger [TID: N/A] [trace-1] - Incoming response:{"cost":1200,"logHeader":{"requestUri":"/api/a"}}',
                    "2026-07-01 18:15:00.039 [exec] WARN  com.alibaba.druid.pool.DruidAbstractDataSource [TID: N/A] [] - discard long time none received connection.",
                    "2026-07-01 18:25:01.073 [exec] ERROR c.u.c.s.feign.fallback.AdminSystemServiceFallBack [TID: N/A] [] - Fallback Reason: Read timed out executing POST http://esg-system/in/email/sendQueued",
                ]
            ),
            encoding="utf-8",
        )
        try:
            summary = analyze_logs([log_file], AnalysisConfig(output_dir=tmp_dir))
            titles = {issue.title for issue in summary.issues}
            self.assertIn("Feign fallback 被触发", titles)
            self.assertIn("数据库空闲连接被丢弃", titles)
            self.assertEqual(summary.endpoints[0]["endpoint"], "/api/a")
        finally:
            if log_file.exists():
                log_file.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
