import unittest

from log_analysis.parser import parse_log_line


class ParserTests(unittest.TestCase):
    def test_parse_request_line_extracts_uri_trace_and_span(self) -> None:
        line = (
            '2026-07-01 18:09:32.752 [http-nio-9504-exec-49] INFO  '
            'com.umsin.base.config.filter.ReqRespLoggerFilter [TID: N/A] [20260701180932752149] - '
            'Incoming request:{"logHeader":{"requestMethod":"POST","requestUri":"/api/email/getSendErrorEmailCount",'
            '"spanId":"yq-20260701180932752811","traceId":"20260701180932752149"}}'
        )
        event = parse_log_line(line, "sample.log", 1)
        self.assertEqual(event.level, "INFO")
        self.assertEqual(event.request_uri, "/api/email/getSendErrorEmailCount")
        self.assertEqual(event.trace_id, "20260701180932752149")
        self.assertEqual(event.span_id, "yq-20260701180932752811")
        self.assertEqual(event.direction, "incoming_request")

    def test_parse_response_line_extracts_cost(self) -> None:
        line = (
            '2026-07-01 18:09:32.776 [http-nio-9504-exec-49] INFO  '
            'com.umsin.base.config.filter.ReqRespLoggerFilter [TID: N/A] [20260701180932752149] - '
            'Incoming response:{"cost":24,"httpStatus":0,"logHeader":{"requestMethod":"POST","requestUri":"/api/email/getSendErrorEmailCount"}}'
        )
        event = parse_log_line(line, "sample.log", 2)
        self.assertEqual(event.cost_ms, 24)
        self.assertEqual(event.request_uri, "/api/email/getSendErrorEmailCount")
        self.assertEqual(event.direction, "incoming_response")

    def test_parse_warn_line_extracts_exception_keyword(self) -> None:
        line = (
            "2026-07-01 18:15:00.039 [xxl-job, JobThread-10-1782900900032] WARN  "
            "com.alibaba.druid.pool.DruidAbstractDataSource [TID: N/A] [] - "
            "discard long time none received connection."
        )
        event = parse_log_line(line, "sample.log", 3)
        self.assertIn("discard long time none received connection", " ".join(event.exception_keywords).lower())

    def test_parse_time_only_error_log_uses_date_from_filename(self) -> None:
        line = (
            "06:30:00.176 [RuoyiScheduler_Worker-12] ERROR c.r.c.u.PushUtils - "
            "[pushPunchAndroid,223] - ErrCode: MissingTargetValue"
        )
        event = parse_log_line(line, "sys-error.2026-07-01.log", 4)
        self.assertEqual(event.level, "ERROR")
        self.assertEqual(event.thread, "RuoyiScheduler_Worker-12")
        self.assertEqual(event.logger, "c.r.c.u.PushUtils")
        self.assertIsNotNone(event.timestamp)
        self.assertEqual(event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"), "2026-07-01 06:30:00.176000")
        self.assertIn("MissingTargetValue", event.message)

    def test_parse_wrapper_line_extracts_outer_timestamp_and_level(self) -> None:
        line = "INFO   | jvm 1    | 2026/06/27 10:45:32 | Caused by: java.net.SocketTimeoutException: Read timed out"
        event = parse_log_line(line, "wrapper.log.1", 5)
        self.assertEqual(event.level, "INFO")
        self.assertEqual(event.thread, "jvm 1")
        self.assertEqual(event.logger, "wrapper")
        self.assertIsNotNone(event.timestamp)
        self.assertEqual(event.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "2026-06-27 10:45:32")
        self.assertIn("timed out", " ".join(event.exception_keywords).lower())

    def test_parse_wrapper_line_prefers_embedded_time_only_log(self) -> None:
        line = (
            "INFO   | jvm 1    | 2026/06/27 10:13:24 | "
            "at org.foo.Bar(Baz.java:1)10:13:23.895 [http-nio-9001-exec-32] WARN  "
            "o.s.w.s.m.m.a.ExceptionHandlerExceptionResolver - [doResolveHandlerMethodException,434] - "
            "Failure in @ExceptionHandler com.umsin.pms.exception.GlobalExceptionHandler#exceptionHandler(HttpServletRequest, Exception)"
        )
        event = parse_log_line(line, "wrapper.log.1", 6)
        self.assertEqual(event.level, "WARN")
        self.assertEqual(event.thread, "http-nio-9001-exec-32")
        self.assertEqual(event.logger, "o.s.w.s.m.m.a.ExceptionHandlerExceptionResolver")
        self.assertIsNotNone(event.timestamp)
        self.assertEqual(event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"), "2026-06-27 10:13:23.895000")
        self.assertIn("GlobalExceptionHandler", event.message)


if __name__ == "__main__":
    unittest.main()
