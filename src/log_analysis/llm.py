from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    raw_text: str
    parsed_json: dict | list | None


class OpenAICompatibleLLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def health_check(self) -> dict:
        return self._get_json("/health")

    def list_models(self) -> dict:
        return self._get_json("/v1/models")

    def create_chat_completion(self, messages: list[dict], max_completion_tokens: int | None = None) -> LLMResult:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "stream": False,
            "max_completion_tokens": max_completion_tokens or self.config.max_completion_tokens,
            "reasoning_effort": "none",
            "include_reasoning": False,
            "messages": messages,
        }
        data = self._post_json(self.config.chat_path, payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {data}") from exc
        parsed = _extract_json(content)
        return LLMResult(raw_text=content, parsed_json=parsed)

    def _get_json(self, path: str) -> dict:
        request = Request(f"{self.config.base_url.rstrip('/')}{path}")
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMError(str(exc)) from exc

    def _post_json(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.config.base_url.rstrip('/')}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMError(str(exc)) from exc


def _extract_json(text: str) -> dict | list | None:
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    candidates = [stripped]
    if "```json" in stripped:
        candidates.append(stripped.split("```json", 1)[1].split("```", 1)[0].strip())
    if "```" in stripped:
        candidates.append(stripped.split("```", 1)[1].rsplit("```", 1)[0].strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
        extracted = _find_json_substring(candidate)
        if extracted is not None:
            return extracted
    fallback = _find_json_substring(text)
    if fallback is not None:
        return fallback
    return None


def _find_json_substring(text: str) -> dict | list | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def build_stage_one_messages(summary_payload: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是一名生产日志分析专家。只能依据已观察到的事实进行总结。"
                "请仅返回简体中文 JSON，不要返回 Markdown。"
                "返回对象必须包含 executive_summary、probable_risks、next_checks 三个键。"
                "每个键对应的数组最多 5 条，每条一句话，简洁明确。"
                "不要提供代码级修复建议。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(summary_payload, ensure_ascii=False),
        },
    ]


def build_stage_two_messages(input_payload: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是一名资深 Java 故障分析专家。请仅返回简体中文 JSON。"
                "返回对象必须包含 suggestions 键。"
                "suggestions 中每项都必须包含 title、confidence、observed_facts、fix_direction、"
                "llm_suggestion、side_effects。"
                "所有建议只能基于给定日志证据和代码片段推断。"
                "不要编造缺失配置、业务规则或未提供的实现细节。"
                "列表保持简短。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(input_payload, ensure_ascii=False),
        },
    ]


def build_stage_two_issue_messages(input_payload: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是一名资深 Java 故障分析专家。请仅返回简体中文 JSON，不要返回 Markdown。"
                "只分析当前这一个问题。"
                "返回对象必须包含 title、confidence、observed_facts、fix_direction、"
                "llm_suggestion、side_effects。"
                "所有建议只能基于给定日志证据和代码片段推断。"
                "如果证据不足，请明确写出证据不足，不要编造。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(input_payload, ensure_ascii=False),
        },
    ]
