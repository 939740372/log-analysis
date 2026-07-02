from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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
DATABASE_SIGNAL_TOKENS = {
    "druid",
    "datasource",
    "dynamicdatasource",
    "dynamicdatasourcefactory",
    "jdbc",
    "mysql",
    "validation",
    "testwhileidle",
    "test-while-idle",
    "db",
    "database",
    "connection",
    "pool",
}
DATABASE_CONFIG_TERMS = (
    "druid",
    "validation-query",
    "test-while-idle",
    "initial-size",
    "min-idle",
    "max-active",
    "max-wait",
    "time-between-eviction-runs-millis",
    "min-evictable-idle-time-millis",
    "max-evictable-idle-time-millis",
    "validationQuery",
    "testWhileIdle",
)
DATABASE_HIGH_VALUE_CONFIG_TOKENS = (
    "validation-query",
    "validationquery",
    "test-while-idle",
    "testwhileidle",
    "min-evictable-idle-time-millis",
    "max-evictable-idle-time-millis",
    "time-between-eviction-runs-millis",
    "keepalive",
    "initial-size",
    "min-idle",
    "max-active",
    "max-wait",
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
            for config_term in DATABASE_CONFIG_TERMS:
                token = ("config", config_term, None)
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


def extract_issue_method_names(issue: Issue) -> set[str]:
    methods: set[str] = set()
    for _, method_name in _extract_class_method_pairs(issue):
        methods.add(method_name.lower())
    return methods


def _issue_source_path_tokens(issue: Issue) -> set[str]:
    tokens: set[str] = set()
    stop_words = {"log", "logs", "app", "application", "service", "target", "classes", "esg"}
    for evidence in issue.evidence:
        basename = Path(evidence.source).stem.lower()
        for part in re.split(r"[^a-z0-9]+", basename):
            if len(part) < 3 or part in stop_words:
                continue
            tokens.add(part)
    return tokens


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
    if term_kind == "config" and path.name.startswith("application"):
        score += 90
    if term_kind == "config" and path.suffix in {".yml", ".yaml", ".properties"}:
        score += 70
    if "datasource" in line.lower() or "druid" in line.lower():
        score += 25
    if "validation-query" in line.lower() or "test-while-idle" in line.lower():
        score += 25
    if path.name == "pom.xml":
        score -= 100
    if line.strip().startswith("import "):
        score -= 40
    if len(term_value) > 30:
        score += 10
    return score


def match_belongs_to_issue(
    issue: Issue,
    *,
    file_path: str,
    snippet: str,
    reason: str,
    class_name: str | None,
    method_name: str | None,
) -> bool:
    issue_class_names = {clazz.split(".")[-1].lower() for clazz in issue.related_classes}
    issue_method_names = extract_issue_method_names(issue)
    keyword_tokens = {keyword.lower() for keyword in issue.related_keywords}
    endpoint_tokens = {endpoint.lower() for endpoint in issue.related_endpoints}

    haystack = " ".join(
        [
            file_path.lower(),
            snippet.lower(),
            reason.lower(),
            (class_name or "").lower(),
            (method_name or "").lower(),
        ]
    )

    if issue.category == "数据库":
        db_tokens = DATABASE_SIGNAL_TOKENS | keyword_tokens | issue_class_names
        return any(token and token in haystack for token in db_tokens)

    if endpoint_tokens and any(token in haystack for token in endpoint_tokens):
        return True
    if issue_class_names and any(token in haystack for token in issue_class_names):
        return True
    if issue_method_names and any(token in haystack for token in issue_method_names):
        return True
    if keyword_tokens and any(token in haystack for token in keyword_tokens):
        return True
    return False


def _issue_match_score(
    issue: Issue,
    *,
    file_path: str,
    snippet: str,
    reason: str,
    class_name: str | None,
    method_name: str | None,
) -> int:
    path_lower = file_path.lower()
    preferred_path_lower = _prefer_source_equivalent_path(file_path).lower()
    snippet_lower = snippet.lower()
    haystack = " ".join(
        [
            preferred_path_lower,
            snippet_lower,
            reason.lower(),
            (class_name or "").lower(),
            (method_name or "").lower(),
        ]
    )
    score = 10
    source_tokens = _issue_source_path_tokens(issue)
    if source_tokens and any(token in preferred_path_lower for token in source_tokens):
        score += 35
    if "/src/main/resources/" in preferred_path_lower:
        score += 40
    if "/src/main/java/" in preferred_path_lower:
        score += 20
    if "/resources/common/" in preferred_path_lower:
        score += 35
    if "/target/classes/" in path_lower and preferred_path_lower == path_lower:
        score -= 50
    if issue.category == "数据库":
        if "application" in haystack and any(token in haystack for token in {"yaml", "yml", "properties"}):
            score += 60
        if "dynamicdatasourcefactory" in haystack:
            score += 50
        if any(token in haystack for token in {"validation", "testwhileidle", "keepalive", "datasource", "jdbc", "mysql"}):
            score += 40
        if ".set" in snippet_lower or "seturl" in haystack or "setdriverclassname" in haystack:
            score += 20
        if file_path.endswith("pom.xml"):
            score -= 80
        if snippet.strip().startswith("import "):
            score -= 30
    return score


def _canonical_issue_match_key(file_path: str, snippet: str) -> tuple[str, str]:
    return _prefer_source_equivalent_path(file_path), snippet.strip()


def _prefer_source_equivalent_path(file_path: str) -> str:
    normalized_path = file_path.replace("\\", "/")
    if "/target/classes/" not in normalized_path:
        return normalized_path

    src_candidate = Path(normalized_path.replace("/target/classes/", "/src/main/resources/"))
    if src_candidate.exists():
        return str(src_candidate)

    target_path = Path(normalized_path)
    parts = list(target_path.parts)
    if "target" in parts:
        target_index = parts.index("target")
        repo_root = Path(*parts[: target_index - 2]) if target_index >= 2 else None
        if repo_root is not None:
            common_candidate = repo_root / "resources" / "common" / target_path.name
            if common_candidate.exists():
                return str(common_candidate)
    return normalized_path


def _config_snippet_priority(issue: Issue, snippet: str, file_path: str) -> int:
    if issue.category != "数据库":
        return 0
    snippet_lower = snippet.lower()
    path_lower = file_path.lower()
    score = 0
    if any(token in snippet_lower for token in DATABASE_HIGH_VALUE_CONFIG_TOKENS):
        score += 80
    if "datasource" in snippet_lower and "druid" in snippet_lower:
        score += 50
    if "jdbc:mysql" in snippet_lower or "url:" in snippet_lower:
        score += 40
    if "username:" in snippet_lower or "password:" in snippet_lower:
        score += 20
    if "/src/main/resources/" in path_lower:
        score += 20
    if "/target/classes/" in path_lower:
        score -= 40
    return score


def _with_preferred_file_path(match: CodeReference) -> CodeReference:
    preferred_path = _prefer_source_equivalent_path(match.file_path)
    if preferred_path == match.file_path:
        return match
    return CodeReference(
        file_path=preferred_path,
        line_number=match.line_number,
        snippet=match.snippet,
        reason=match.reason,
        class_name=match.class_name,
        method_name=match.method_name,
    )


def _with_preferred_file_path_dict(match: dict[str, Any]) -> dict[str, Any]:
    preferred_path = _prefer_source_equivalent_path(str(match.get("file_path") or ""))
    if preferred_path == str(match.get("file_path") or ""):
        return match
    cloned = dict(match)
    cloned["file_path"] = preferred_path
    return cloned


def select_related_code_matches(issue: Issue, code_matches: list[CodeReference], limit: int = 5) -> list[CodeReference]:
    ranked_by_canonical: dict[tuple[str, str], tuple[int, CodeReference]] = {}
    seen_lines: set[tuple[str, int]] = set()
    for match in code_matches:
        if not match_belongs_to_issue(
            issue,
            file_path=match.file_path,
            snippet=match.snippet,
            reason=match.reason,
            class_name=match.class_name,
            method_name=match.method_name,
        ):
            continue
        line_key = (match.file_path, match.line_number)
        if line_key in seen_lines:
            continue
        seen_lines.add(line_key)
        canonical_key = _canonical_issue_match_key(match.file_path, match.snippet)
        score = _issue_match_score(
            issue,
            file_path=match.file_path,
            snippet=match.snippet,
            reason=match.reason,
            class_name=match.class_name,
            method_name=match.method_name,
        )
        score += _config_snippet_priority(issue, match.snippet, match.file_path)
        current = ranked_by_canonical.get(canonical_key)
        normalized_match = _with_preferred_file_path(match)
        if current is None or score > current[0]:
            ranked_by_canonical[canonical_key] = (score, normalized_match)
    ranked = list(ranked_by_canonical.values())
    ranked.sort(key=lambda item: (-item[0], item[1].file_path, item[1].line_number))
    return [match for _, match in ranked[:limit]]


def select_related_code_match_dicts(issue: Issue, code_matches: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked_by_canonical: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    seen_lines: set[tuple[str, int]] = set()
    for match in code_matches:
        file_path = str(match.get("file_path") or "")
        snippet = str(match.get("snippet") or "")
        reason = str(match.get("reason") or "")
        class_name = str(match.get("class_name") or "") or None
        method_name = str(match.get("method_name") or "") or None
        if not match_belongs_to_issue(
            issue,
            file_path=file_path,
            snippet=snippet,
            reason=reason,
            class_name=class_name,
            method_name=method_name,
        ):
            continue
        line_key = (file_path, int(match.get("line_number") or 0))
        if line_key in seen_lines:
            continue
        seen_lines.add(line_key)
        canonical_key = _canonical_issue_match_key(file_path, snippet)
        score = _issue_match_score(
            issue,
            file_path=file_path,
            snippet=snippet,
            reason=reason,
            class_name=class_name,
            method_name=method_name,
        )
        score += _config_snippet_priority(issue, snippet, file_path)
        current = ranked_by_canonical.get(canonical_key)
        normalized_match = _with_preferred_file_path_dict(match)
        if current is None or score > current[0]:
            ranked_by_canonical[canonical_key] = (score, normalized_match)
    ranked = list(ranked_by_canonical.values())
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("file_path") or ""), int(item[1].get("line_number") or 0)))
    return [match for _, match in ranked[:limit]]


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
