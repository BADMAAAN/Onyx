from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kk.memory.database import MemoryDatabase


class SkillRegistry:
    def __init__(self, pending_dir: Path, memory: MemoryDatabase):
        self.pending_dir = pending_dir
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.memory = memory

    def propose(self, name: str, description: str, steps: list[dict[str, Any]]) -> Path:
        if not name.replace("_", "").isalnum():
            raise ValueError("Имя навыка может содержать буквы, цифры и подчёркивания")
        payload = {"name": name, "description": description, "steps": steps, "status": "pending"}
        path = self.pending_dir / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.memory.save_skill(name, description, steps, "pending")
        return path

    def propose_task(self, task):
        if task is None or not task.actions:
            raise ValueError('Нет действий для сохранения')
        if task.status.value in {'FAILED', 'CANCELLED', 'BLOCKED', 'UNVERIFIED', 'WAITING'}:
            raise ValueError('Нельзя сохранять неуспешную задачу как успешный навык')
        steps = []
        for action in task.actions:
            if not action.get('result', {}).get('ok'):
                continue
            if action.get('verification',{}).get('verified') is False:
                continue
            if action['tool'] in {'observe_context','see_screen','get_ui_tree'}:
                continue
            args = dict(action['arguments'])
            # User content becomes an explicit parameter, not a silently replayed message.
            if action['tool'] in {'type_text', 'write_clipboard'}:
                args['text'] = '{text}'
            steps.append({'tool':action['tool'],'arguments':args})
        if not steps:
            raise ValueError('Нет успешных действий для сохранения')
        name = 'scenario_' + task.id[:8]
        path = self.propose(name, task.user_request, steps)
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload.update({'trigger_examples':[task.user_request], 'parameters':{'text':'Текст пользователя'} if any('{text}' in str(s) for s in steps) else {},
            'planner_hint':'Повторно наблюдай контекст перед каждым действием; адаптируй шаги к текущему UI.',
            'preconditions':['Проверить целевое приложение и доступность элементов'],
            'verification':['Сверить UIA после каждого действия с целью текущего запроса'],
            'risk_level':'ACTION', 'statistics':{'executions':0,'successes':0,'failures':0,'last_used':None,'corrections':[]}})
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        return payload

    def confirm(self, name):
        if not name.replace('_','').isalnum():
            raise ValueError('Invalid skill name')
        path = self.pending_dir / f'{name}.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['status'] = 'active'
        user_dir = self.pending_dir.parent / 'user'
        user_dir.mkdir(parents=True,exist_ok=True)
        destination = user_dir / f'{name}.yaml'
        # JSON is a strict subset of YAML 1.2; no unsafe YAML object constructors.
        destination.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        self.memory.save_skill(name,payload['description'],payload['steps'],'active')
        path.unlink()
        return destination

    def retrieve(self, query, limit=3):
        import re
        words = set(re.findall(r'\w+',query.casefold()))
        ranked = []
        for path in (self.pending_dir.parent/'user').glob('*.yaml'):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                score = len(words & set(re.findall(r'\w+',payload['description'].casefold())))
                if score and payload.get('status') == 'active':
                    ranked.append((score,payload))
            except (ValueError,KeyError):
                continue
        return [p for _,p in sorted(ranked,key=lambda x:x[0],reverse=True)[:limit]]

    def record_execution(self, name, success, correction=None):
        from kk.memory.database import utc_now
        if not name.replace('_','').isalnum():
            raise ValueError('Invalid skill name')
        path = self.pending_dir.parent/'user'/f'{name}.yaml'
        payload = json.loads(path.read_text(encoding='utf-8'))
        stats = payload['statistics']
        stats['executions'] += 1
        stats['successes' if success else 'failures'] += 1
        stats['last_used'] = utc_now()
        if correction:
            stats['corrections'].append(correction)
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
