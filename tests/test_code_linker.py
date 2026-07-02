import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from log_analysis.code_linker import select_related_code_match_dicts, select_related_code_matches
from log_analysis.code_display import group_code_references
from log_analysis.models import CodeReference, EvidenceItem, FixSuggestion, Issue


class CodeLinkerSelectionTests(unittest.TestCase):
    def test_group_code_references_aggregates_same_file_lines(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "app.yaml"
            file_path.write_text(
                "line8: a\nline9: b\nvalidationQuery: select 1\ntestWhileIdle: true\nline12: c\n",
                encoding="utf-8",
            )
            groups = group_code_references(
                [
                    CodeReference(
                        file_path=str(file_path),
                        line_number=3,
                        snippet="validationQuery: select 1",
                        reason="命中配置项: validationQuery",
                        class_name=None,
                        method_name=None,
                    ),
                    CodeReference(
                        file_path=str(file_path),
                        line_number=4,
                        snippet="testWhileIdle: true",
                        reason="命中配置项: testWhileIdle",
                        class_name=None,
                        method_name=None,
                    ),
                ]
            )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["file_path"], str(file_path))
        self.assertEqual(len(groups[0]["lines"]), 2)
        self.assertEqual(len(groups[0]["blocks"]), 1)
        self.assertEqual(groups[0]["blocks"][0]["start_line"], 3)
        self.assertEqual(groups[0]["blocks"][0]["end_line"], 4)
        self.assertEqual(groups[0]["blocks"][0]["context_start_line"], 1)
        self.assertEqual(groups[0]["blocks"][0]["context_end_line"], 6)
        self.assertEqual(groups[0]["blocks"][0]["hit_line_numbers"], [3, 4])
        self.assertGreaterEqual(len(groups[0]["blocks"][0]["context_lines"]), 5)

    def test_fix_suggestion_to_dict_contains_grouped_code(self) -> None:
        payload = FixSuggestion(
            title="数据库建议",
            confidence="高",
            observed_facts=["存在连接池告警"],
            linked_code=[
                CodeReference(
                    file_path="/repo/app.yaml",
                    line_number=10,
                    snippet="validationQuery: select 1",
                    reason="命中配置项: validationQuery",
                )
            ],
            fix_direction="检查连接池参数",
            llm_suggestion="优先核对 validationQuery",
            side_effects=[],
            evidence=[],
        ).to_dict()
        self.assertIn("linked_code_groups", payload)
        self.assertEqual(payload["linked_code_groups"][0]["file_path"], "/repo/app.yaml")
        self.assertIn("blocks", payload["linked_code_groups"][0])

    def test_database_issue_excludes_business_method_match(self) -> None:
        issue = Issue(
            title="数据库空闲连接被丢弃",
            category="数据库",
            severity="中",
            description="Druid 丢弃空闲连接",
            related_classes=["com.alibaba.druid.pool.DruidAbstractDataSource"],
            related_keywords=["discard long time none received connection"],
        )
        matches = [
            CodeReference(
                file_path="/tmp/ConfigTrafficDistanceServiceImpl.java",
                line_number=90,
                snippet="public void addDistance(List<ConfigTrafficDistanceEntity> distanceList) {",
                reason="命中方法名: addDistance",
                class_name="ConfigTrafficDistanceServiceImpl",
                method_name="addDistance",
            ),
            CodeReference(
                file_path="/tmp/application-com.yaml",
                line_number=71,
                snippet="type: com.alibaba.druid.pool.DruidDataSource",
                reason="命中配置项: druid",
                class_name=None,
                method_name=None,
            ),
        ]
        selected = select_related_code_matches(issue, matches, limit=5)
        self.assertEqual(len(selected), 1)
        self.assertIn("DruidDataSource", selected[0].snippet)

    def test_react_issue_payload_selection_uses_same_database_guard(self) -> None:
        issue = Issue(
            title="数据库空闲连接被丢弃",
            category="数据库",
            severity="中",
            description="Druid 丢弃空闲连接",
            related_classes=["com.alibaba.druid.pool.DruidAbstractDataSource"],
            related_keywords=["discard long time none received connection"],
        )
        matches = [
            {
                "file_path": "/tmp/ConfigTrafficDistanceServiceImpl.java",
                "line_number": 90,
                "snippet": "public void addDistance(List<ConfigTrafficDistanceEntity> distanceList) {",
                "reason": "命中方法名: addDistance",
                "class_name": "ConfigTrafficDistanceServiceImpl",
                "method_name": "addDistance",
            },
            {
                "file_path": "/tmp/application-com.yaml",
                "line_number": 71,
                "snippet": "type: com.alibaba.druid.pool.DruidDataSource",
                "reason": "命中配置项: druid",
                "class_name": None,
                "method_name": None,
            },
        ]
        selected = select_related_code_match_dicts(issue, matches, limit=5)
        self.assertEqual(len(selected), 1)
        self.assertIn("application-com.yaml", selected[0]["file_path"])

    def test_database_issue_prefers_runtime_config_over_pom_and_import(self) -> None:
        issue = Issue(
            title="数据库空闲连接被丢弃",
            category="数据库",
            severity="中",
            description="Druid 丢弃空闲连接",
            related_classes=["com.alibaba.druid.pool.DruidAbstractDataSource"],
            related_keywords=["discard long time none received connection"],
        )
        matches = [
            CodeReference(
                file_path="/tmp/pom.xml",
                line_number=43,
                snippet="<artifactId>druid</artifactId>",
                reason="命中配置项: druid",
                class_name=None,
                method_name=None,
            ),
            CodeReference(
                file_path="/tmp/DynamicDataSourceFactory.java",
                line_number=16,
                snippet="import com.alibaba.druid.pool.DruidDataSource;",
                reason="命中配置项: druid",
                class_name=None,
                method_name=None,
            ),
            CodeReference(
                file_path="/tmp/application-com.yaml",
                line_number=71,
                snippet="type: com.alibaba.druid.pool.DruidDataSource",
                reason="命中配置项: druid",
                class_name=None,
                method_name=None,
            ),
        ]
        selected = select_related_code_matches(issue, matches, limit=3)
        self.assertEqual(selected[0].file_path, "/tmp/application-com.yaml")

    def test_database_issue_global_scoring_prefers_application_config_line(self) -> None:
        from log_analysis.code_linker import _score_match  # noqa: PLC2701

        application_score = _score_match(
            "config",
            "druid",
            Path("/tmp/application-com.yaml"),
            "type: com.alibaba.druid.pool.DruidDataSource",
            None,
            None,
            None,
        )
        java_score = _score_match(
            "config",
            "druid",
            Path("/tmp/DynamicDataSourceFactory.java"),
            "druidDataSource.setUrl(properties.getUrl());",
            "DynamicDataSourceFactory",
            "buildDruidDataSource",
            None,
        )
        self.assertGreater(application_score, java_score)

    def test_database_issue_prefers_source_module_over_target_output(self) -> None:
        issue = Issue(
            title="数据库空闲连接被丢弃",
            category="数据库",
            severity="中",
            description="Druid 丢弃空闲连接",
            evidence=[],
            related_classes=["com.alibaba.druid.pool.DruidAbstractDataSource"],
            related_keywords=["discard long time none received connection"],
        )
        issue.evidence.append(
            EvidenceItem(
                label="数据库空闲连接被丢弃",
                source="/tmp/esg-system.log",
                line_number=1,
                excerpt="discard long time none received connection",
            )
        )
        matches = [
            CodeReference(
                file_path="/repo/esg-service/esg-system/target/classes/application-com.yaml",
                line_number=71,
                snippet="type: com.alibaba.druid.pool.DruidDataSource",
                reason="命中配置项: druid",
                class_name=None,
                method_name=None,
            ),
            CodeReference(
                file_path="/repo/esg-service/esg-system/src/main/resources/application-com.yaml",
                line_number=71,
                snippet="type: com.alibaba.druid.pool.DruidDataSource",
                reason="命中配置项: druid",
                class_name=None,
                method_name=None,
            ),
        ]
        selected = select_related_code_matches(issue, matches, limit=2)
        self.assertEqual(selected[0].file_path, "/repo/esg-service/esg-system/src/main/resources/application-com.yaml")

    def test_database_issue_deduplicates_target_copy_of_same_config_line(self) -> None:
        issue = Issue(
            title="数据库空闲连接被丢弃",
            category="数据库",
            severity="中",
            description="Druid 丢弃空闲连接",
            related_classes=["com.alibaba.druid.pool.DruidAbstractDataSource"],
            related_keywords=["discard long time none received connection"],
        )
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            src_path = root / "esg-service" / "esg-system" / "src" / "main" / "resources" / "application-com.yaml"
            target_path = root / "esg-service" / "esg-system" / "target" / "classes" / "application-com.yaml"
            src_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.write_text("type: com.alibaba.druid.pool.DruidDataSource\n", encoding="utf-8")
            target_path.write_text("type: com.alibaba.druid.pool.DruidDataSource\n", encoding="utf-8")
            matches = [
                CodeReference(
                    file_path=str(src_path),
                    line_number=71,
                    snippet="type: com.alibaba.druid.pool.DruidDataSource",
                    reason="命中配置项: druid",
                    class_name=None,
                    method_name=None,
                ),
                CodeReference(
                    file_path=str(target_path),
                    line_number=71,
                    snippet="type: com.alibaba.druid.pool.DruidDataSource",
                    reason="命中配置项: druid",
                    class_name=None,
                    method_name=None,
                ),
            ]
            selected = select_related_code_matches(issue, matches, limit=5)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].file_path, str(src_path))

    def test_database_issue_prefers_tuning_keys_over_type_line(self) -> None:
        issue = Issue(
            title="数据库空闲连接被丢弃",
            category="数据库",
            severity="中",
            description="Druid 丢弃空闲连接",
            related_classes=["com.alibaba.druid.pool.DruidAbstractDataSource"],
            related_keywords=["discard long time none received connection"],
        )
        matches = [
            CodeReference(
                file_path="/repo/esg-service/esg-system/src/main/resources/application-com.yaml",
                line_number=71,
                snippet="type: com.alibaba.druid.pool.DruidDataSource",
                reason="命中配置项: druid",
                class_name=None,
                method_name=None,
            ),
            CodeReference(
                file_path="/repo/esg-service/esg-system/src/main/resources/application-com.yaml",
                line_number=44,
                snippet="validation-query: SELECT 1",
                reason="命中配置项: validation-query",
                class_name=None,
                method_name=None,
            ),
        ]
        selected = select_related_code_matches(issue, matches, limit=2)
        self.assertEqual(selected[0].line_number, 44)

    def test_database_issue_maps_target_application_yaml_to_common_source(self) -> None:
        issue = Issue(
            title="数据库空闲连接被丢弃",
            category="数据库",
            severity="中",
            description="Druid 丢弃空闲连接",
            related_classes=["com.alibaba.druid.pool.DruidAbstractDataSource"],
            related_keywords=["discard long time none received connection"],
        )
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            common_path = root / "resources" / "common" / "application.yaml"
            target_path = root / "esg-service" / "esg-hazsub" / "target" / "classes" / "application.yaml"
            common_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            common_path.write_text("validationQuery: select 1 from dual\n", encoding="utf-8")
            target_path.write_text("validationQuery: select 1 from dual\n", encoding="utf-8")
            matches = [
                CodeReference(
                    file_path=str(target_path),
                    line_number=202,
                    snippet="validationQuery: select 1 from dual",
                    reason="命中配置项: validationQuery",
                    class_name=None,
                    method_name=None,
                ),
                CodeReference(
                    file_path=str(common_path),
                    line_number=202,
                    snippet="validationQuery: select 1 from dual",
                    reason="命中配置项: validationQuery",
                    class_name=None,
                    method_name=None,
                ),
            ]
            selected = select_related_code_matches(issue, matches, limit=5)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].file_path, str(common_path))


if __name__ == "__main__":
    unittest.main()
