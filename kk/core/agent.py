from __future__ import annotations

from .orchestrator import Orchestrator


class Agent:
    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator

    def ask(self, text: str, on_token=None, cancellation=None) -> str:
        return self.orchestrator.respond(text, on_token, cancellation)

    def cancel(self):
        self.orchestrator.cancel()
