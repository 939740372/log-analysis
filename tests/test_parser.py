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


if __name__ == "__main__":
    unittest.main()
