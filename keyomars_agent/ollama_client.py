"""Local-only Ollama chat client. No external API or paid key.

The old demo used a 180 s read timeout and did not catch socket.timeout.
This version makes the timeout configurable and surfaces useful errors to the UI.
"""
from __future__ import annotations
import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaClient:
    API_URL = "http://127.0.0.1:11434/api/chat"

    def __init__(self, model: str = "qwen3:8b", timeout: int = 600):
        self.model = model
        self.timeout = int(timeout)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "think": False,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_ctx": 6144, "num_predict": 1600},
        }
        return self._post_chat(body)

    def chat_structured(self, messages: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
        """Ollama's /api/chat structured-output format (JSON schema).

        We do NOT give the model tool selection authority for the validated plan.
        """
        body = {
            "model": self.model, "messages": messages,
            "format": schema,
            "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0.1, "num_ctx": 6144, "num_predict": 1900},
        }
        return self._post_chat(body)

    def _post_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        req = Request(self.API_URL,
                      json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except HTTPError as e:
            detail = e.read(1600).decode("utf-8", errors="replace")
            raise RuntimeError(f"خطای Ollama HTTP {e.code}: {detail}") from e
        except (socket.timeout, TimeoutError) as e:
            raise RuntimeError(
                f"زمان پاسخ Ollama بیشتر از {self.timeout} ثانیه شد. مدل ممکن است بخشی روی CPU اجرا شود. "
                "دوباره تلاش کن یا از یک مدل کوچک‌تر دارای tool calling استفاده کن. "
                "دستور ollama ps را برای بررسی CPU/GPU اجرا کن."
            ) from e
        except URLError as e:
            raise RuntimeError("ارتباط با Ollama محلی برقرار نشد. Ollama را اجرا کن و ollama list را بزن.") from e
        except (json.JSONDecodeError, ValueError) as e:
            raise RuntimeError("پاسخ Ollama ساختار JSON معتبر نداشت.") from e
        if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
            raise RuntimeError("ساختار پاسخ Ollama غیرمنتظره بود.")
        return data["message"]
