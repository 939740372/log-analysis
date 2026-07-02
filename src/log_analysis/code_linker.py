from __future__ import annotations

import re
from pathlib import Path

from .config import AnalysisConfig
from .models import CodeReference, Issue

JAVA_FILE_SUFFIXES = {".java", ".kt", ".xml", ".yml", ".yaml", ".properties"}
CLASS_PATTERN = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")
METHOD_PATTERN = re.compile(r"\b(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
ASYNC_METHOD_PATTERN = re.compile(
    r"async method:\s+.*?\s+(?P<class>[A-Za-z_][\w.$]*)\.(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
FQCN_METHOD_PATTERN = re.compile(
    r"(?P<class>[A-Za-z_][\w.$]*)\.(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)


def _build_search_terms(issues: list[Issue]) -> list[tuple[str, str, str | None]]:
    terms: list[tuple[str, str, str | None]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for issue in issues:
        for endpoint in issue.related_endpoints:
            token = ("endpoint", endpoint, None)
            if token not in seen:
                seen.add(token)
                terms.append(token)
        for class_name in issue.related_classes:
            short_name = class_name.split(".")[-1]
            for token in (("class", short_name, short_name), ("fqcn", class_name, short_name)):
                if token not in seen:
                    seen.add(token)
                    terms.append(token)
        for keyword in issue.related_keywords:
            token = ("keyword", keyword, None)
            if token not in seen:
                seen.add(token)
                terms.append(token)
        for class_name, method_name in _extract_class_method_pairs(issue):
            short_name = class_name.split(".")[-1]
            for token in (
                ("class", short_name, short_name),
                ("fqcn", class_name, short_name),
                ("method", method_name, short_name),
            ):
                if token not in seen:
                    seen.add(token)
                    terms.append(token)
        if issue.category == "数据库":
            for token in (
                ("config", "druid", None),
                ("config", "validation-query", None),
                ("config", "test-while-idle", None),
                ("config", "keepAlive", None),
                ("config", "keep-alive", None),
            ):
                if token not in seen:
                    seen.add(token)
                    terms.append(token)
    return terms


def _extract_class_method_pairs(issue: Issue) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for evidence in issue.evidence:
        for pattern in (ASYNC_METHOD_PATTERN, FQCN_METHOD_PATTERN):
            for match in pattern.finditer(evidence.excerpt):
                class_name = match.group("class")
                method_name = match.group("method")
                if "." not in class_name:
                    continue
                pair = (class_name, method_name)
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)
    return pairs


def _nearest_context(lines: list[str], line_number: int) -> tuple[str | None, str | None]:
    class_name = None
    method_name = None
    start = max(0, line_number - 60)
    for index in range(line_number - 1, start - 1, -1):
        line = lines[index]
        if class_name is None:
            class_match = CLASS_PATTERN.search(line)
            if class_match:
                class_name = class_match.group(1)
        if method_name is None:
            method_match = METHOD_PATTERN.search(line)
            if method_match:
                method_name = method_match.group(1)
        if class_name and method_name:
            break
    return class_name, method_name


def _file_likely_contains_target(path: Path, content: str, target_class: str | None) -> bool:
    if target_class is None:
        return True
    if path.stem == target_class:
        return True
    class_declaration = f"class {target_class}"
    interface_declaration = f"interface {target_class}"
    return class_declaration in content or interface_declaration in content


def _match_reason(term_kind: str, term_value: str) -> str:
    labels = {
        "endpoint": "命中接口",
        "class": "命中类名",
        "fqcn": "命中完整类名",
        "method": "命中方法名",
        "keyword": "命中关键词",
        "config": "命中配置项",
    }
    return f"{labels.get(term_kind, '命中')}: {term_value}"


def _score_match(
    term_kind: str,
    term_value: str,
    path: Path,
    line: str,
    class_name: str | None,
    method_name: str | None,
    target_class: str | None,
) -> int:
    base_scores = {
        "method": 120,
        "fqcn": 110,
        "class": 100,
        "endpoint": 80,
        "config": 60,
        "keyword": 40,
    }
    score = base_scores.get(term_kind, 10)
    if target_class and path.stem == target_class:
        score += 50
    if target_class and class_name == target_class:
        score += 40
    if term_kind == "method" and method_name == term_value:
        score += 40
    if term_kind == "fqcn" and term_value in line:
        score += 30
    if len(term_value) > 30:
        score += 10
    return score


def link_issues_to_code(source_root: Path, issues: list[Issue], config: AnalysisConfig) -> list[CodeReference]:
    terms = _build_search_terms(issues)
    if not source_root.exists():
        return []

    collected: list[tuple[int, CodeReference]] = []
    seen_match_keys: set[tuple[str, int, str]] = set()

    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix not in JAVA_FILE_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()
        for line_index, line in enumerate(lines, start=1):
            for term_kind, term_value, target_class in terms:
                if not _file_likely_contains_target(path, content, target_class):
                    continue
                if term_value and term_value in line:
                    class_name, method_name = _nearest_context(lines, line_index)
                    match_key = (str(path), line_index, term_value)
                    if match_key in seen_match_keys:
                        break
                    seen_match_keys.add(match_key)
                    reference = CodeReference(
                            file_path=str(path),
                            line_number=line_index,
                            snippet=line.strip()[:500],
                            reason=_match_reason(term_kind, term_value),
                            class_name=class_name,
                            method_name=method_name,
                        )
                    score = _score_match(term_kind, term_value, path, line, class_name, method_name, target_class)
                    collected.append((score, reference))
                    break
    collected.sort(key=lambda item: (-item[0], item[1].file_path, item[1].line_number))
    return [reference for _, reference in collected[: config.max_code_matches]]
