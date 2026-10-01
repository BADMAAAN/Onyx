"""Perception failures with a stable stage and a short user-facing explanation."""
from __future__ import annotations


class PerceptionError(RuntimeError):
    def __init__(self, stage: str, reason: str, message: str):
        self.stage, self.reason = stage, reason
        super().__init__(f'{stage}/{reason}: {message}')
