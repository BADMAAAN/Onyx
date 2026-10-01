from __future__ import annotations

from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel

from .registry import Tool, ToolRegistry


def register_memory_tools(registry: ToolRegistry, memory: MemoryDatabase) -> None:
    registry.register(
        Tool(
            "remember_preference",
            "Сохранить явно сообщённый пользователем факт, алиас или предпочтение в долговременную память.",
            {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Краткий устойчивый ключ"},
                    "value": {"type": "string", "description": "Факт или предпочтение"},
                    "category": {"type": "string", "enum": ["preference", "alias", "habit", "fact"]},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            PermissionLevel.ACTION,
            lambda key, value, category="preference": _remember(memory, key, value, category, registry),
        )
    )
    registry.register(Tool('forget_memory',
        'Forget one exact saved memory key, including application preferences and its search index. Backups and conversation history are outside this operation.',
        {'type':'object','properties':{'key':{'type':'string'}},'required':['key'],'additionalProperties':False},
        PermissionLevel.ACTION, lambda key: _forget(memory, registry, key)))
    registry.register(
        Tool(
            "recall_memories",
            "Найти сохранённые предпочтения и факты пользователя.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": [],
                "additionalProperties": False,
            },
            PermissionLevel.READ,
            lambda query="", limit=10: memory.recall(query, max(1, min(limit, 50))),
        )
    )


def _remember(memory: MemoryDatabase, key: str, value: str, category: str, registry=None) -> dict[str, str]:
    preference = key.removeprefix('application.')
    if preference in {'browser','music_app','telegram_client'} and registry is not None:
        decision = getattr(registry, 'decision_policy', None)
        if decision:
            resolver = getattr(registry, 'app_resolver', None)
            if resolver: resolver.resolve(value)
            decision.remember(preference, value)
            key = 'application.' + preference
    memory.remember(key, value, category)
    changed = getattr(registry, 'memory_context_changed', None)
    if callable(changed): changed()
    return {"saved": key, "category": category}


def _forget(memory, registry, key):
    preference = key.removeprefix('application.')
    decision = getattr(registry, 'decision_policy', None)
    forgotten = decision.forget(preference) if decision and preference in {'browser','music_app','telegram_client'} else False
    forgotten = memory.forget(key) or forgotten
    if key == 'ui.visual_visible' and getattr(registry, 'visual_state', None) is not None:
        registry.visual_state.forget_visual_preference()
    changed = getattr(registry, 'memory_context_changed', None)
    if callable(changed): changed()
    return {'forgotten': forgotten, 'key': key, 'scope':'saved_fact_and_search_index',
            'retained':'Existing backups and conversation/action history are not erased by this exact-key operation.'}

