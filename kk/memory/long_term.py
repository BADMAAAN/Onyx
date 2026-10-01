from __future__ import annotations

from .database import MemoryDatabase


class LongTermMemory:
    def __init__(self, database: MemoryDatabase):
        self.database = database

    def context(self, query: str, limit: int = 8) -> str:
        facts = self.database.recall(query, limit)
        return "\n".join(
            f"- {item['key']}: {item['value']} "
            f"[source={item.get('source') or 'unknown'}; category={item['category']}; "
            f"updated={item['updated_at']}; confidence={item['confidence']}; "
            f"status={'user_stated' if item.get('source') == 'user' else 'stored_report'}]"
            for item in facts)
