import unittest

from log_analysis.adaptive_parser import (
    RULE_CANDIDATES,
    _fallback_rule_from_ranked_candidates,
    _normalize_dynamic_rule,
    rank_candidate_rules,
    validate_dynamic_rule,
)
from log_analysis.parser import parse_log_line


class AdaptiveParserTests(unittest.TestCase):
    def test_normalize_dynamic_rule_accepts_candidate_selection(self) -> None:
        parsed = {
            "candidate_name": "wrapper_embedded_spring",
            "tuned_fields": {
                "logger_literal": "jvm",
            },
        }
        rule = _normalize_dynamic_rule(parsed)
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertEqual(rule.strategy, "wrapper_embedded")
        self.assertEqual(rule.logger_literal, "jvm")

    def test_rank_candidate_rules_prefers_wrapper_candidate_for_wrapper_sample(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": (
                    "INFO   | jvm 1    | 2026/06/27 10:13:24 | "
                    "10:13:23.895 [http-nio-9001-exec-32] WARN  "
                    "o.s.w.s.m.m.a.ExceptionHandlerExceptionResolver - "
                    "[doResolveHandlerMethodException,434] - Failure in @ExceptionHandler"
                ),
            }
        ]
        ranked = rank_candidate_rules(sample_lines, "wrapper.log.1")
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], "wrapper_embedded_spring")
        self.assertEqual(ranked[0][1], RULE_CANDIDATES["wrapper_embedded_spring"])

    def test_rank_candidate_rules_prefers_spring_json_plain(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": (
                    "2026-07-01 18:09:32.776 [http-nio-9504-exec-49] INFO "
                    "com.umsin.base.config.filter.ReqRespLoggerFilter - "
                    'Incoming response:{"cost":24,"httpStatus":0,"logHeader":{"requestUri":"/api/email/getSendErrorEmailCount"}}'
                ),
            }
        ]
        ranked = rank_candidate_rules(sample_lines, "spring-json.log")
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], "spring_json_plain")

    def test_rank_candidate_rules_prefers_spring_boot_pid_thread(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": (
                    "2026-06-30 20:07:34.009  WARN 1 --- [           main] "
                    "c.a.c.n.c.NacosPropertySourceBuilder     : Ignore the empty nacos configuration"
                ),
            }
        ]
        ranked = rank_candidate_rules(sample_lines, "esg-system.log")
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], "spring_boot_pid_thread")

    def test_rank_candidate_rules_prefers_nacos_dubbo_bootstrap(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": (
                    "2026-06-25 09:15:44.899 [main] INFO  "
                    "org.apache.dubbo.common.logger.LoggerFactory [TID: N/A] [] - "
                    "using logger: org.apache.dubbo.common.logger.log4j.Log4jLoggerAdapter"
                ),
            }
        ]
        ranked = rank_candidate_rules(sample_lines, "tenant-ms-server.2026-06-25.0.log")
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], "nacos_dubbo_bootstrap")

    def test_rank_candidate_rules_prefers_tanuki_wrapper_plain(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": "INFO   | jvm 1    | 2026/06/27 10:45:32 | Caused by: java.net.SocketTimeoutException: Read timed out",
            }
        ]
        ranked = rank_candidate_rules(sample_lines, "wrapper.log.1")
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], "tanuki_wrapper_plain")

    def test_rank_candidate_rules_prefers_tomcat_catalina_juli(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": (
                    "03-Jul-2026 10:13:23.895 INFO [http-nio-8080-exec-32] "
                    "org.apache.catalina.core.StandardWrapperValve.invoke "
                    "Servlet.service() for servlet [dispatcherServlet] in context with path [] threw exception"
                ),
            }
        ]
        ranked = rank_candidate_rules(sample_lines, "catalina.out")
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][0], "tomcat_catalina_juli")

    def test_validate_dynamic_rule_enables_wrapper_candidate_on_tie(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": (
                    "INFO   | jvm 1    | 2026/06/27 10:13:24 | "
                    "10:13:23.895 [http-nio-9001-exec-32] WARN  "
                    "o.s.w.s.m.m.a.ExceptionHandlerExceptionResolver - "
                    "[doResolveHandlerMethodException,434] - Failure in @ExceptionHandler"
                ),
            }
        ]
        decision = validate_dynamic_rule(
            "wrapper.log.1",
            sample_lines,
            RULE_CANDIDATES["wrapper_embedded_spring"],
            parse_log_line,
            min_improvement=1,
        )
        self.assertTrue(decision.enabled)
        self.assertIn(
            decision.reason,
            {"dynamic_rule_improved_sample_score", "dynamic_rule_tie_enabled_for_whitelist_candidate"},
        )

    def test_validate_dynamic_rule_wrapper_sample_is_not_penalized_by_plain_wrapper_lines(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": "INFO   | jvm 1    | 2026/06/27 10:45:32 | Caused by: java.net.SocketTimeoutException: Read timed out",
            },
            {
                "line_number": 2,
                "text": (
                    "INFO   | jvm 1    | 2026/06/27 10:13:24 | "
                    "10:13:23.895 [http-nio-9001-exec-32] WARN  "
                    "o.s.w.s.m.m.a.ExceptionHandlerExceptionResolver - "
                    "[doResolveHandlerMethodException,434] - Failure in @ExceptionHandler"
                ),
            },
        ]
        decision = validate_dynamic_rule(
            "wrapper.log.1",
            sample_lines,
            RULE_CANDIDATES["wrapper_embedded_spring"],
            parse_log_line,
            min_improvement=1,
        )
        self.assertGreaterEqual(decision.dynamic_score, decision.builtin_score)

    def test_fallback_rule_from_ranked_candidates_uses_clear_leader(self) -> None:
        ranked = [
            ("spring_boot_pid_thread", RULE_CANDIDATES["spring_boot_pid_thread"], 200),
            ("nacos_dubbo_bootstrap", RULE_CANDIDATES["nacos_dubbo_bootstrap"], 120),
        ]
        fallback = _fallback_rule_from_ranked_candidates(ranked)
        self.assertEqual(fallback, RULE_CANDIDATES["spring_boot_pid_thread"])

    def test_time_only_rule_uses_source_file_date(self) -> None:
        rule = RULE_CANDIDATES["time_only_method"]
        event = parse_log_line(
            "06:30:00.176 [RuoyiScheduler_Worker-12] ERROR c.r.c.u.PushUtils - [pushPunchAndroid,223] - ErrCode: MissingTargetValue",
            "sys-info.2026-07-01.log",
            1,
            dynamic_rule=rule,
        )
        self.assertIsNotNone(event.timestamp)
        assert event.timestamp is not None
        self.assertEqual(event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"), "2026-07-01 06:30:00.176000")

    def test_validate_dynamic_rule_time_only_sample_is_not_worse_than_builtin(self) -> None:
        sample_lines = [
            {
                "line_number": 1,
                "text": "06:30:00.176 [RuoyiScheduler_Worker-12] ERROR c.r.c.u.PushUtils - [pushPunchAndroid,223] - ErrCode: MissingTargetValue",
            },
            {
                "line_number": 2,
                "text": "06:30:00.201 [RuoyiScheduler_Worker-12] ERROR c.r.c.u.PushUtils - [pushPunchIOS,148] - ErrMsg: TargetValue is mandatory for this action.",
            },
        ]
        decision = validate_dynamic_rule(
            "sys-info.2026-07-01.log",
            sample_lines,
            RULE_CANDIDATES["time_only_method"],
            parse_log_line,
            min_improvement=1,
        )
        self.assertTrue(decision.enabled)
        self.assertIn(
            decision.reason,
            {"dynamic_rule_improved_sample_score", "dynamic_rule_enabled_with_whitelist_gap"},
        )


if __name__ == "__main__":
    unittest.main()
