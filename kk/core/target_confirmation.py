"""One immediate, expiring in-memory answer to one typed-target question."""
from copy import deepcopy
from pathlib import Path
from time import monotonic
from .task import TaskStatus


def remember(task, session_id):
    constraint = getattr(task.cancellation_token, 'target_confirmation_required', None)
    if task.status != TaskStatus.WAITING or not isinstance(constraint, dict):
        return None
    return dict(task_id=task.id, session_id=session_id, expires=monotonic()+60,
                constraint=deepcopy(constraint))


def consume(pending, previous, text, session_id):
    if not pending or previous is None or previous.status != TaskStatus.WAITING:
        return None
    if (pending['task_id'] != previous.id or pending['session_id'] != session_id
            or monotonic() > pending['expires']):
        return None
    from .fast_commands import normalize
    answer = normalize(text).casefold()
    if answer in {'нет', 'не надо', 'отмена', 'не открывай', 'no', 'cancel'}:
        return dict(cancelled=True, source_task_id=previous.id)
    if answer not in {'да', 'да открой', 'да, открой', 'открой её', 'открой ее', 'подтверждаю', 'yes'}:
        return None
    constraint = pending['constraint']
    candidates = constraint.get('candidates', [])
    if len(candidates) != 1:
        return dict(repeat_question=True, source_task_id=previous.id, constraint=deepcopy(constraint))
    candidate = candidates[0]
    path = candidate.get('path', '')
    kind = candidate.get('type') or constraint.get('type')
    if kind not in {'folder', 'file', 'image', 'document'} or not Path(path).is_absolute():
        return dict(repeat_question=True, source_task_id=previous.id, constraint=deepcopy(constraint))
    return dict(cancelled=False, source_task_id=previous.id, tool='open_desktop_target',
                arguments={'name':path, 'type':kind, 'location':constraint.get('location','')})


def run(intent, task, tools, state):
    from .state import AgentState
    task.metrics.update(brain_route='confirmed_target_no_llm', confirmed_intent=intent)
    task.cancellation_token.mark('target_confirmation', intent=intent,
                                 final_transcript=task.final_transcript)
    if intent.get('repeat_question'):
        task.cancellation_token.target_confirmation_required=deepcopy(intent['constraint'])
        task.status=TaskStatus.WAITING
        return intent['constraint']['question']
    if intent['cancelled']:
        return 'Не открывала.'
    name, args = intent['tool'], intent['arguments']
    task.before_action(name,args,{})
    state.set(AgentState.ACTING,name)
    task.cancellation_token.mark('tool_start',tool=name,arguments=args)
    result=tools.execute(name,args)
    task.actions[-1]['result']=result.as_dict()
    task.cancellation_token.mark('tool_end',tool=name,ok=result.ok,result=result.as_dict())
    task.check()
    data=result.data if isinstance(result.data,dict) else {}
    if data.get('status') == 'needs_clarification':
        task.status=TaskStatus.WAITING
        return data['question']
    if not result.ok or not data.get('verified'):
        task.status=TaskStatus.FAILED
        return 'Открытие не подтверждено: '+str(result.error or data.get('reason') or 'цель изменилась после уточнения')
    task.metrics['resolved_target']=data.get('target')
    return 'Открыла '+str((data.get('target') or {}).get('name') or Path(args['name']).name)+'.'
