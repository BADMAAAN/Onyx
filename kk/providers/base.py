from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def list_models(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse: ...

    @abstractmethod
    def describe_image(self, model: str, prompt: str, image_path: str) -> str: ...

    def check_inference(self, model: str) -> str:
        result = self.chat(
            model,
            [{"role": "user", "content": "Ответь одним словом: готов"}],
            temperature=0.0,
        )
        return result.content.strip()

