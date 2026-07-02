from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import CodeReference


def _read_context_lines(file_path: str, start_line: int, end_line: int) -> list[dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    start_index = max(0, start_line - 1)
    end_index = min(len(raw_lines), end_line)
    return [
        {
            "line_number": index + 1,
            "content": raw_lines[index],
        }
        for index in range(start_index, end_index)
    ]


def _build_line_blocks(lines: list[dict[str, Any]], file_path: str, max_gap: int = 3, context_padding: int = 2) -> list[dict[str, Any]]:
    if not lines:
        return []
    sorted_lines = sorted(lines, key=lambda item: (item["line_number"], item["snippet"]))
    blocks: list[dict[str, Any]] = []
    current = {
        "start_line": sorted_lines[0]["line_number"],
        "end_line": sorted_lines[0]["line_number"],
        "lines": [sorted_lines[0]],
    }
    for line in sorted_lines[1:]:
        if line["line_number"] - current["end_line"] <= max_gap:
            current["end_line"] = line["line_number"]
            current["lines"].append(line)
            continue
        blocks.append(current)
        current = {
            "start_line": line["line_number"],
            "end_line": line["line_number"],
            "lines": [line],
        }
    blocks.append(current)
    for block in blocks:
        hit_line_numbers = [item["line_number"] for item in block["lines"]]
        context_start = max(1, block["start_line"] - context_padding)
        context_end = block["end_line"] + context_padding
        block["context_start_line"] = context_start
        block["context_end_line"] = context_end
        block["hit_line_numbers"] = hit_line_numbers
        block["context_lines"] = _read_context_lines(file_path, context_start, context_end)
    return blocks


def group_code_references(matches: list[CodeReference], per_file_line_limit: int = 6) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for match in matches:
        group = groups.get(match.file_path)
        if group is None:
            group = {
                "file_path": match.file_path,
                "class_names": [],
                "method_names": [],
                "reasons": [],
                "lines": [],
            }
            groups[match.file_path] = group
            order.append(match.file_path)

        if match.class_name and match.class_name not in group["class_names"]:
            group["class_names"].append(match.class_name)
        if match.method_name and match.method_name not in group["method_names"]:
            group["method_names"].append(match.method_name)
        if match.reason not in group["reasons"]:
            group["reasons"].append(match.reason)

        existing_lines = {(item["line_number"], item["snippet"]) for item in group["lines"]}
        line_key = (match.line_number, match.snippet)
        if line_key not in existing_lines and len(group["lines"]) < per_file_line_limit:
            group["lines"].append(
                {
                    "line_number": match.line_number,
                    "snippet": match.snippet,
                    "reason": match.reason,
                }
            )

    grouped = [groups[file_path] for file_path in order]
    for group in grouped:
        group["lines"].sort(key=lambda item: (item["line_number"], item["snippet"]))
        group["blocks"] = _build_line_blocks(group["lines"], group["file_path"])
    return grouped


def group_code_reference_dicts(matches: list[dict[str, Any]], per_file_line_limit: int = 6) -> list[dict[str, Any]]:
    normalized = [
        CodeReference(
            file_path=str(item.get("file_path") or ""),
            line_number=int(item.get("line_number") or 0),
            snippet=str(item.get("snippet") or ""),
            reason=str(item.get("reason") or ""),
            class_name=str(item.get("class_name") or "") or None,
            method_name=str(item.get("method_name") or "") or None,
        )
        for item in matches
    ]
    return group_code_references(normalized, per_file_line_limit=per_file_line_limit)
