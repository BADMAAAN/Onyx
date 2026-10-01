from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from .base import LLMProvider, LLMResponse, ToolCall
from .http import request_json


class LMStudioProvider(LLMProvider):
    name = "lmstudio"

    def __init__(self, base_url: str, timeout: float = 180):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        try:
            request_json(f"{self.base_url}/models", timeout=3)
            return True
        except Exception:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        return request_json(f"{self.base_url}/models", timeout=10).get("data", [])

    def chat(self, model, messages, tools=None, temperature=0.2) -> LLMResponse:
        normalized_messages: list[dict[str, Any]] = []
        for original in messages:
            message = dict(original)
            if message.get("tool_calls"):
                converted = []
                for call in message["tool_calls"]:
                    item = dict(call)
                    function = dict(item.get("function", {}))
                    if isinstance(function.get("arguments"), dict):
                        function["arguments"] = json.dumps(function["arguments"], ensure_ascii=False)
                    item["function"] = function
                    converted.append(item)
                message["tool_calls"] = converted
            normalized_messages.append(message)
        body: dict[str, Any] = {
            "model": model,
            "messages": normalized_messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        raw = request_json(
            f"{self.base_url}/chat/completions",
            method="POST",
            body=body,
            timeout=self.timeout,
        )
        message = raw.get("choices", [{}])[0].get("message", {})
        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function", {})
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            calls.append(
                ToolCall(call.get("id") or f"lmstudio-{index}", function.get("name", ""), arguments)
            )
        return LLMResponse(message.get("content") or "", calls, raw)

    def describe_image(self, model: str, prompt: str, image_path: str) -> str:
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        }
        return self.chat(model, [message], temperature=0.1).content
