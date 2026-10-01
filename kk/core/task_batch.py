"""A committed utterance and its ordered, dependency-aware action results (RAM)."""
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class BatchTask:
    spec: dict
    id: str = field(default_factory=lambda: uuid4().hex)
    dependencies: list = field(default_factory=list)
    target_app: str | None = None
    target_window: int | None = None
    target_monitor: int | None = None
    status: str = 'pending'
    result: object = None


@dataclass
class TaskBatch:
    original_transcript: str
    tasks: list = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = 'pending'
    result: str = ''
    constraints: dict = field(default_factory=dict)

    @classmethod
    def from_plan(cls, transcript, plan):
        batch = cls(transcript)
        for spec in plan:
            spec = dict(spec)
            monitor = spec.get('monitor')
            if spec['kind'] == 'open' and monitor:
                spec['monitor'] = None
            task = BatchTask(spec, target_app=spec.get('app'), target_monitor=spec.get('monitor'))
            if (spec['kind']=='window' and not spec.get('app')) or spec.get('referent'):
                parent=next((t for t in reversed(batch.tasks) if t.spec['kind'] in {'open','place','move','window'}),None)
                if parent: task.dependencies=[parent.id]
            batch.tasks.append(task)
            if spec['kind'] == 'open' and monitor:
                batch.tasks.append(BatchTask(dict(kind='place', app=spec['app'], monitor=monitor),
                    dependencies=[task.id], target_app=spec['app'], target_monitor=monitor))
            if spec['kind'] == 'url':
                browser = next((t for t in reversed(batch.tasks[:-1]) if t.spec['kind'] == 'open' and t.spec.get('app','').casefold() in {'browser','браузер','firefox','chrome','msedge','brave'}), None)
                if browser: task.dependencies = [browser.id]
        return batch
