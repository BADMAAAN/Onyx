from __future__ import annotations

from collections import deque
from typing import Any


class ShortTermMemory:
    def __init__(self, max_messages: int = 24):
        self.messages: deque[dict[str, Any]] = deque(maxlen=max_messages)

    def add(self, role: str, content: str, **extra: Any) -> None:
        self.messages.append({"role": role, "content": content, **extra})

    def snapshot(self, max_characters=10000) -> list[dict[str, Any]]:
        result=[]; used=0
        for message in reversed(self.messages):
            content=str(message.get('content',''))
            if result and used+len(content)>max_characters: break
            result.append(message)
            used+=len(content)
        return list(reversed(result))
