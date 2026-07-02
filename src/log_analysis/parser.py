from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from .adaptive_parser import DynamicParseRule, parse_with_dynamic_rule
from .models import LogEvent

DATE_FROM_FILENAME_PATTERN = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) "
    r"\[(?P<thread>[^\]]*)\] "
    r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
    r"(?P<logger>[^\[]+?) "
    r"\[TID: [^\]]*\] "
    r"\[(?P<trace_id>[^\]]*)\] - "
    r"(?P<message>.*)$"
)
TIME_ONLY_LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{2}:\d{2}:\d{2}\.\d{3}) "
    r"\[(?P<thread>[^\]]*)\] "
    r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
    r"(?P<logger>\S+)\s+-\s+"
    r"\[(?P<location>[^\]]+)\]\s+-\s+"
    r"(?P<message>.*)$"
)
WRAPPER_LOG_PATTERN = re.compile(
    r"^(?P<outer_level>TRACE|DEBUG|INFO|WARN|ERROR)\s+\|\s+"
    r"(?P<channel>[^|]+?)\s+\|\s+"
    r"(?P<outer_timestamp>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+\|\s+"
    r"(?P<message>.*)$"
)
EMBEDDED_TIME_ONLY_LOG_PATTERN = re.compile(
    r"(?P<timestamp>\d{2}:\d{2}:\d{2}\.\d{3}) "
    r"\[(?P<thread>[^\]]*)\] "
    r"(?P<level>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
    r"(?P<logger>\S+)\s+-\s+"
    r"\[(?P<location>[^\]]+)\]\s+-\s+"
    r"(?P<message>.*)$"
)

TRACE_FALLBACK_PATTERN = re.compile(r"\[(?P<trace_id>\d{8,})\]")
REQUEST_URI_PATTERN = re.compile(r'"requestUri"\s*:\s*"([^"]+)"')
SPAN_ID_PATTERN = re.compile(r'"spanId"\s*:\s*"([^"]+)"')
COST_PATTERN = re.compile(r'"cost"\s*:\s*(\d+)')
DOWNSTREAM_URI_PATTERN = re.compile(r"http[s]?://[^\s\"}]+")
EXCEPTION_PATTERNS = [
    re.compile(r"\bException\b"),
    re.compile(r"Caused by:"),
    re.compile(r"timed out", re.IGNORECASE),
    re.compile(r"Fallback Reason", re.IGNORECASE),
    re.compile(r"discard long time none received connection", re.IGNORECASE),
    re.compile(r"失败"),
    re.compile(r"异常"),
]

JSON_MARKERS = [
    "Incoming request:",
    "Incoming response:",
    "Outgoing request:",
    "Outgoing response:",
]


def _try_parse_timestamp(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _try_parse_time_only_timestamp(raw: str, source_file: str) -> datetime | None:
    date_match = DATE_FROM_FILENAME_PATTERN.search(Path(source_file).name)
    if not date_match:
        return None
    try:
        return datetime.strptime(f"{date_match.group('date')} {raw}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _try_parse_wrapper_timestamp(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


def _try_parse_time_only_timestamp_with_date(raw: str, base_date: date) -> datetime | None:
    try:
        return datetime.strptime(f"{base_date.isoformat()} {raw}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _parse_json_payload(message: str) -> dict | None:
    for marker in JSON_MARKERS:
        if marker in message:
            candidate = message.split(marker, 1)[1].strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None
    return None


def _extract_exception_keywords(line: str) -> list[str]:
    found: list[str] = []
    for pattern in EXCEPTION_PATTERNS:
        match = pattern.search(line)
        if match:
            token = match.group(0)
            if token not in found:
                found.append(token)
    return found


def build_structured_event(
    *,
    timestamp: datetime | None,
    level: str,
    thread: str | None,
    logger: str | None,
    message: str,
    source_file: str,
    line_number: int,
    raw_line: str,
) -> LogEvent:
    parsed_json = _parse_json_payload(message)
    request_uri = None
    span_id = None
    cost_ms = None
    downstream_uri = None
    trace_id = None

    if parsed_json:
        log_header = parsed_json.get("logHeader") or {}
        request_uri = log_header.get("requestUri")
        span_id = log_header.get("spanId")
        trace_id = log_header.get("traceId")
        cost_value = parsed_json.get("cost")
        if isinstance(cost_value, int):
            cost_ms = cost_value
    else:
        request_uri_match = REQUEST_URI_PATTERN.search(raw_line)
        span_id_match = SPAN_ID_PATTERN.search(raw_line)
        cost_match = COST_PATTERN.search(raw_line)
        trace_match = TRACE_FALLBACK_PATTERN.search(raw_line)
        request_uri = request_uri_match.group(1) if request_uri_match else None
        span_id = span_id_match.group(1) if span_id_match else None
        cost_ms = int(cost_match.group(1)) if cost_match else None
        trace_id = trace_match.group("trace_id") if trace_match else None

    downstream_match = DOWNSTREAM_URI_PATTERN.search(raw_line)
    downstream_uri = downstream_match.group(0) if downstream_match else None
    direction = None
    if "Incoming request:" in message:
        direction = "incoming_request"
    elif "Incoming response:" in message:
        direction = "incoming_response"
    elif "Outgoing request:" in message:
        direction = "outgoing_request"
    elif "Outgoing response:" in message:
        direction = "outgoing_response"

    return LogEvent(
        timestamp=timestamp,
        level=level,
        thread=thread,
        logger=logger,
        trace_id=trace_id,
        span_id=span_id,
        request_uri=request_uri,
        cost_ms=cost_ms,
        message=message,
        source_file=source_file,
        line_number=line_number,
        direction=direction,
        exception_keywords=_extract_exception_keywords(raw_line),
        downstream_uri=downstream_uri,
        raw_json=parsed_json,
        raw_line=raw_line,
    )


def _parse_time_only_match(
    match: re.Match[str],
    source_file: str,
    line_number: int,
    raw_line: str,
    base_date: date | None = None,
) -> LogEvent:
    timestamp = (
        _try_parse_time_only_timestamp_with_date(match.group("timestamp"), base_date)
        if base_date is not None
        else _try_parse_time_only_timestamp(match.group("timestamp"), source_file)
    )
    return build_structured_event(
        timestamp=timestamp,
        level=match.group("level"),
        thread=match.group("thread") or None,
        logger=match.group("logger").strip(),
        message=match.group("message"),
        source_file=source_file,
        line_number=line_number,
        raw_line=raw_line,
    )


def parse_log_line(
    line: str,
    source_file: str,
    line_number: int,
    dynamic_rule: DynamicParseRule | None = None,
) -> LogEvent:
    raw_line = line.rstrip("\n")
    if dynamic_rule is not None:
        dynamic_event = parse_with_dynamic_rule(raw_line, source_file, line_number, dynamic_rule)
        if dynamic_event is not None:
            return dynamic_event
    match = LOG_PATTERN.match(raw_line)
    if match:
        event = build_structured_event(
            timestamp=_try_parse_timestamp(match.group("timestamp")),
            level=match.group("level"),
            thread=match.group("thread") or None,
            logger=match.group("logger").strip(),
            message=match.group("message"),
            source_file=source_file,
            line_number=line_number,
            raw_line=raw_line,
        )
        event.trace_id = match.group("trace_id") or event.trace_id
        return event

    time_only_match = TIME_ONLY_LOG_PATTERN.match(raw_line)
    if time_only_match:
        return _parse_time_only_match(time_only_match, source_file, line_number, raw_line)

    wrapper_match = WRAPPER_LOG_PATTERN.match(raw_line)
    if wrapper_match:
        outer_timestamp = _try_parse_wrapper_timestamp(wrapper_match.group("outer_timestamp"))
        wrapped_message = wrapper_match.group("message")
        embedded_match = EMBEDDED_TIME_ONLY_LOG_PATTERN.search(wrapped_message)
        if embedded_match:
            return _parse_time_only_match(
                embedded_match,
                source_file,
                line_number,
                raw_line,
                base_date=outer_timestamp.date() if outer_timestamp is not None else None,
            )
        return build_structured_event(
            timestamp=outer_timestamp,
            level=wrapper_match.group("outer_level"),
            thread=wrapper_match.group("channel").strip() or None,
            logger="wrapper",
            message=wrapped_message,
            source_file=source_file,
            line_number=line_number,
            raw_line=raw_line,
        )

    trace_id = None
    fallback_match = TRACE_FALLBACK_PATTERN.search(raw_line)
    if fallback_match:
        trace_id = fallback_match.group("trace_id")

    return LogEvent(
        timestamp=None,
        level="UNKNOWN",
        thread=None,
        logger=None,
        trace_id=trace_id,
        span_id=None,
        request_uri=None,
        cost_ms=None,
        message=raw_line,
        source_file=source_file,
        line_number=line_number,
        exception_keywords=_extract_exception_keywords(raw_line),
        downstream_uri=DOWNSTREAM_URI_PATTERN.search(raw_line).group(0)
        if DOWNSTREAM_URI_PATTERN.search(raw_line)
        else None,
        raw_json=None,
        raw_line=raw_line,
    )


def iter_log_events(log_path: Path, dynamic_rule: DynamicParseRule | None = None):
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            yield parse_log_line(line, str(log_path), line_number, dynamic_rule=dynamic_rule)
