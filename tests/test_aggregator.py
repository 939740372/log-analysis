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

    def test_analyze_logs_detects_time_only_error_format(self) -> None:
        tmp_dir = Path("test-output-time-only")
        tmp_dir.mkdir(exist_ok=True)
        log_file = tmp_dir / "sys-error.2026-07-01.log"
        log_file.write_text(
            "\n".join(
                [
                    "06:30:00.176 [RuoyiScheduler_Worker-12] ERROR c.r.c.u.PushUtils - [pushPunchAndroid,223] - ErrCode: MissingTargetValue",
                    "06:30:00.201 [RuoyiScheduler_Worker-12] ERROR c.r.c.u.PushUtils - [pushPunchIOS,148] - ErrMsg: TargetValue is mandatory for this action.",
                ]
            ),
            encoding="utf-8",
        )
        try:
            summary = analyze_logs([log_file], AnalysisConfig(output_dir=tmp_dir))
            titles = {issue.title for issue in summary.issues}
            self.assertIn("PushUtils.pushPunchAndroid 调用异常 与 PushUtils.pushPunchIOS 调用异常", titles)
            self.assertEqual(summary.overview["level_distribution"].get("ERROR"), 2)
        finally:
            if log_file.exists():
                log_file.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()

    def test_analyze_logs_builds_specific_generic_error_title(self) -> None:
        tmp_dir = Path("test-output-error-specific")
        tmp_dir.mkdir(exist_ok=True)
        log_file = tmp_dir / "sys-error.2026-07-01.log"
        log_file.write_text(
            "\n".join(
                [
                    "00:00:02.001 [RuoyiScheduler_Worker-16] ERROR c.a.d.f.s.StatFilter - [mergeSql,149] - merge sql error, dbType mysql, druid-1.1.14, sql : INSERT INTO st_print_daily_recommend",
                    "00:00:02.017 [RuoyiScheduler_Worker-16] ERROR c.r.q.u.AbstractQuartzJob - [execute,49] - 任务执行异常  - ：",
                    "00:13:11.522 [MqttCallback-5] ERROR o.s.a.i.SimpleAsyncUncaughtExceptionHandler - [handleUncaughtException,39] - Unexpected exception occurred invoking async method: public void com.ruoyi.system.service.impl.MqttCallbackServiceImpl.pushSong(java.lang.String,java.lang.String)",
                    "00:17:59.799 [MqttCallback-9] ERROR o.s.a.i.SimpleAsyncUncaughtExceptionHandler - [handleUncaughtException,39] - Unexpected exception occurred invoking async method: public void com.ruoyi.system.service.impl.MqttCallbackServiceImpl.pushSong(java.lang.String,java.lang.String)",
                ]
            ),
            encoding="utf-8",
        )
        try:
            summary = analyze_logs([log_file], AnalysisConfig(output_dir=tmp_dir))
            titles = {issue.title for issue in summary.issues}
            self.assertIn("MqttCallbackServiceImpl.pushSong 异步异常 与 Druid SQL 合并异常", titles)
        finally:
            if log_file.exists():
                log_file.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()

    def test_analyze_logs_supports_wrapper_format(self) -> None:
        tmp_dir = Path("test-output-wrapper")
        tmp_dir.mkdir(exist_ok=True)
        log_file = tmp_dir / "wrapper.log.1"
        log_file.write_text(
            "\n".join(
                [
                    "INFO   | jvm 1    | 2026/06/27 10:13:24 | at org.foo.Bar(Baz.java:1)10:13:23.895 [http-nio-9001-exec-32] WARN  o.s.w.s.m.m.a.ExceptionHandlerExceptionResolver - [doResolveHandlerMethodException,434] - Failure in @ExceptionHandler com.umsin.pms.exception.GlobalExceptionHandler#exceptionHandler(HttpServletRequest, Exception)",
                    "INFO   | jvm 1    | 2026/06/27 10:13:24 | 10:13:24.214 [http-nio-9001-exec-43] ERROR c.u.p.e.GlobalExceptionHandler - [exceptionHandler,77] - unknown error",
                    "INFO   | jvm 1    | 2026/06/27 10:45:32 | Caused by: java.net.SocketTimeoutException: Read timed out",
                ]
            ),
            encoding="utf-8",
        )
        try:
            summary = analyze_logs([log_file], AnalysisConfig(output_dir=tmp_dir))
            self.assertEqual(summary.overview["level_distribution"].get("WARN"), 1)
            self.assertEqual(summary.overview["level_distribution"].get("ERROR"), 1)
            self.assertEqual(summary.overview["level_distribution"].get("INFO"), 1)
            self.assertEqual(summary.overview["time_range"]["start"], "2026-06-27 10:13:23.895000")
            self.assertEqual(summary.overview["time_range"]["end"], "2026-06-27 10:45:32")
            titles = {issue.title for issue in summary.issues}
            self.assertIn("Feign/HTTP 超时风险", titles)
            self.assertIn("GlobalExceptionHandler.exceptionHandler 调用异常", titles)
        finally:
            if log_file.exists():
                log_file.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
