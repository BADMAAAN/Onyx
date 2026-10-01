"""Explicit boundary between intent routing and a user-facing answer."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RouteResult:
    status: Literal['handled', 'not_handled', 'error']
    answer: str | None = None
    route: str | None = None
    reason: str | None = None

    def __post_init__(self):
        if self.status == 'not_handled' and self.answer is not None:
            raise ValueError('An unresolved target is not an answer')
        if self.status != 'not_handled' and not isinstance(self.answer, str):
            raise ValueError('Handled/error routes require a complete answer')
