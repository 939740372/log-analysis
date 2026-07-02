from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class DingTalkConfig:
    webhook: str
    secret: str | None = None
    timeout_seconds: int = 10


def _signed_webhook(config: DingTalkConfig) -> str:
    if not config.secret:
        return config.webhook
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{config.secret}"
    digest = hmac.new(config.secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(digest))
    separator = "&" if "?" in config.webhook else "?"
    return f"{config.webhook}{separator}timestamp={timestamp}&sign={sign}"


def send_markdown_message(config: DingTalkConfig, title: str, text: str) -> dict:
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": text,
        },
    }
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        _signed_webhook(config),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=config.timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))
