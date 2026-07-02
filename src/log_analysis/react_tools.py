from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .aggregator import analyze_logs
from .code_linker import link_issues_to_code
from .config import AnalysisConfig
from .models import Issue


@dataclass
class ToolSpec:
    name: str
    description: str
    arguments_schema: dict

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "arguments_schema": self.arguments_schema,
        }


class ReactToolRegistry:
    def __init__(self, log_files: list[Path], source_root: Path | None, analysis_config: AnalysisConfig):
        self.log_files = log_files
        self.source_root = source_root
        self.analysis_config = analysis_config
        self._tools: dict[str, Callable[..., dict]] = {
            "list_log_files": self.list_log_files,
            "sample_log_head_tail": self.sample_log_head_tail,
            "search_logs": self.search_logs,
            "search_code_symbols": self.search_code_symbols,
            "open_code_context": self.open_code_context,
            "get_config_matches": self.get_config_matches,
        }

    def specs(self) -> list[dict]:
        return [
            ToolSpec("list_log_files", "列出当前分析使用的日志文件。", {}).to_dict(),
            ToolSpec(
                "sample_log_head_tail",
                "读取指定日志文件的头尾若干行。",
                {"file": "string", "head": "int", "tail": "int"},
            ).to_dict(),
            ToolSpec(
                "search_logs",
                "按关键字搜索日志并返回上下文片段。",
                {"query": "string", "limit": "int"},
            ).to_dict(),
            ToolSpec(
                "search_code_symbols",
                "在源码中搜索类名、方法名或配置项。",
                {"query": "string", "limit": "int"},
            ).to_dict(),
            ToolSpec(
                "open_code_context",
                "读取源码文件指定行附近的上下文。",
                {"file_path": "string", "line_number": "int", "before": "int", "after": "int"},
            ).to_dict(),
            ToolSpec(
                "get_config_matches",
                "在源码和配置文件中搜索配置关键字。",
                {"query": "string", "limit": "int"},
            ).to_dict(),
        ]

    def run(self, tool_name: str, arguments: dict) -> dict:
        if tool_name not in self._tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        return self._tools[tool_name](**arguments)

    def list_log_files(self) -> dict:
        return {
            "files": [
                {"path": str(path), "size_bytes": path.stat().st_size if path.exists() else None}
                for path in self.log_files
            ]
        }

    def sample_log_head_tail(self, file: str, head: int = 10, tail: int = 10) -> dict:
        path = Path(file)
        if not path.exists():
            return {"error": f"File not found: {file}"}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {
            "file": str(path),
            "head": lines[:head],
            "tail": lines[-tail:] if tail else [],
        }

    def search_logs(self, query: str, limit: int = 20) -> dict:
        results: list[tuple[int, dict]] = []
        pattern = query.lower()
        for path in self.log_files:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    lower_line = line.lower()
                    if pattern in lower_line:
                        entry = {
                            "file": str(path),
                            "line_number": line_number,
                            "excerpt": line.rstrip("\n")[:500],
                        }
                        results.append(
                            (
                                _score_log_match(pattern, lower_line),
                                entry,
                            )
                        )
        results.sort(key=lambda item: (-item[0], item[1]["file"], item[1]["line_number"]))
        return {"query": query, "matches": [item for _, item in results[:limit]]}

    def search_code_symbols(self, query: str, limit: int = 20) -> dict:
        if self.source_root is None or not self.source_root.exists():
            return {"query": query, "matches": []}
        matches: list[tuple[int, dict]] = []
        pattern = query.lower()
        for path in self.source_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".java", ".kt", ".xml", ".yml", ".yaml", ".properties"}:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        lower_line = line.lower()
                        if pattern in lower_line:
                            match = {
                                "file_path": str(path),
                                "line_number": line_number,
                                "snippet": line.rstrip("\n")[:300],
                            }
                            matches.append(
                                (
                                    _score_code_match(query, path, line, line_number),
                                    match,
                                )
                            )
            except OSError:
                continue
        matches.sort(key=lambda item: (-item[0], item[1]["file_path"], item[1]["line_number"]))
        return {"query": query, "matches": [item for _, item in matches[:limit]]}

    def open_code_context(self, file_path: str, line_number: int, before: int = 10, after: int = 20) -> dict:
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, line_number - 1 - before)
        end = min(len(lines), line_number + after)
        context = [
            {"line_number": index + 1, "content": lines[index]}
            for index in range(start, end)
        ]
        return {"file_path": str(path), "line_number": line_number, "context": context}

    def get_config_matches(self, query: str, limit: int = 20) -> dict:
        if self.source_root is None or not self.source_root.exists():
            return {"query": query, "matches": []}
        matches: list[tuple[int, dict]] = []
        pattern = query.lower()
        preferred_suffixes = {".yml", ".yaml", ".properties", ".xml", ".java"}
        for path in self.source_root.rglob("*"):
            if not path.is_file() or path.suffix not in preferred_suffixes:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        lower_line = line.lower()
                        if pattern in lower_line:
                            match = {
                                "file_path": str(path),
                                "line_number": line_number,
                                "snippet": line.rstrip("\n")[:300],
                            }
                            matches.append(
                                (
                                    _score_config_match(query, path, line),
                                    match,
                                )
                            )
            except OSError:
                continue
        matches.sort(key=lambda item: (-item[0], item[1]["file_path"], item[1]["line_number"]))
        return {"query": query, "matches": [item for _, item in matches[:limit]]}


def build_issue_bundle(log_files: list[Path], source_root: Path | None, analysis_config: AnalysisConfig) -> tuple[dict, list[Issue], list[dict]]:
    summary = analyze_logs(log_files, analysis_config)
    code_matches = []
    if source_root is not None:
        code_matches = [match.to_dict() for match in link_issues_to_code(source_root, summary.issues, analysis_config)]
    return summary.to_dict(), summary.issues, code_matches


def _score_log_match(query: str, lower_line: str) -> int:
    score = 10
    if query == lower_line.strip():
        score += 60
    if "error" in lower_line:
        score += 40
    if "exception" in lower_line:
        score += 35
    if "warn" in lower_line:
        score += 20
    if "async" in lower_line or "fallback" in lower_line or "timed out" in lower_line:
        score += 30
    if query in lower_line:
        score += min(30, len(query))
    return score


def _score_code_match(query: str, path: Path, line: str, line_number: int) -> int:
    lower_query = query.lower()
    lower_line = line.lower()
    score = 10
    if path.stem.lower() == lower_query:
        score += 100
    if f"class {lower_query}" in lower_line or f"interface {lower_query}" in lower_line:
        score += 90
    if f" {lower_query}(" in lower_line:
        score += 80
    if lower_query in lower_line:
        score += 20
    if path.suffix == ".java":
        score += 20
    if line_number < 120:
        score += 5
    return score


def _score_config_match(query: str, path: Path, line: str) -> int:
    lower_query = query.lower()
    lower_line = line.lower()
    score = 10
    if path.suffix in {".yml", ".yaml", ".properties"}:
        score += 40
    if "datasource" in lower_line or "druid" in lower_line:
        score += 30
    if lower_query in lower_line:
        score += 20
    if path.name.startswith("application"):
        score += 10
    return score
