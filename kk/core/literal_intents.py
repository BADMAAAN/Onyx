"""Exact payload extraction before normalization; arbitrary goals stay with Brain."""
import re
from .task import TaskStatus


def literal_intent(text):
    text = re.sub(r'(?i)^(?:оникс|onyx)\b[\s,.!?-]*', '', text.strip())
    # Parse destinations and punctuation before normalization so dictation is
    # never converted into an application search or case-folded app name.
    prefix = re.fullmatch(r'(?is)в\s+(.+?)\s+(напиши|введи|вставь|выполни)\s*(?:[:,]\s*|\s+)(.+)', text)
    if prefix and re.search(r'(?i)\b(?:открой|поиск|найди|и)\b', prefix[1]):
        prefix = None
    destination = re.fullmatch(r'(?is)(напиши|введи|вставь|type)\s+(?:в|in)\s+([^:]+):\s*(.+)', text)
    if destination:
        verb, app, payload = destination.groups()
        text = f'открой {app} и {verb}: {payload}'
    match = prefix or re.fullmatch(r'(?is)(?:(?:открой|open)\s+(.+?)\s+(?:и|and)\s+)?(напиши|введи|вставь|выполни|type|execute)\s*(?:[:,]\s*|\s+)(.+)', text)
    if not match: return None
    app, verb, payload = match.groups()
    if app:
        app = re.sub(r'(?i)^(?:мне\s+|пожалуйста[,\s]+)', '', app).strip()
        if re.search(r'(?i)\b(?:не|нет|хотя)\b', app): return None
    # App-specific search/destination wording needs the semantic planner.
    if not app and re.match(r'(?i)^(?:в |туда\b|код\b|письмо\b|рассказ\b)', payload): return None
    quoted = len(payload) >= 2 and (payload[0], payload[-1]) in {('"','"'), ('«','»')}
    if not quoted and re.search(r'(?i)\b(?:ничего не пиши|не выполняй|не отправляй)\b', payload): return None
    if quoted:
        payload = payload[1:-1]
    return dict(app=app, text=payload, submit=verb.casefold() in {'выполни','execute'})


def run_literal(text, task, tools):
    intent = literal_intent(text)
    if intent is None: return None
    if 'text_entry' not in getattr(tools, '_tools', {}): return None
    task.metrics['brain_route'] = 'literal_no_llm'
    def call(name, args):
        task.before_action(name, args, {})
        task.cancellation_token.mark('tool_start', tool=name, arguments=args)
        result = tools.execute(name,args)
        task.actions[-1]['result'] = result.as_dict()
        task.cancellation_token.mark('tool_end', tool=name, ok=result.ok, result=result.as_dict())
        task.check()
        return result
    task.cancellation_token.mark('intent_decided', route='literal_no_llm', intent=intent)
    from kk.tools.windows.app_resolver import canonical
    if intent['app'] and canonical(intent['app']) == 'obsidian' and not intent['submit']:
        result = call('open_obsidian_draft', {'text':intent['text']})
        if result.ok and isinstance(result.data, dict) and result.data.get('verified'):
            return 'Написала в новом черновике Obsidian.'
        task.status = TaskStatus.FAILED
        return 'Ввод в Obsidian не подтверждён. Повторно не вводила. ' + str(result.error or (result.data or {}).get('reason') or 'Не найдено доступное поле новой заметки.')
    if intent['app']:
        result = call('open_application', {'name':intent['app']})
        if not result.ok or not result.data.get('verified'):
            task.metrics['context_needs_planner'] = True
            return None
    result = call('text_entry', {'text':intent['text'], 'submit':intent['submit']})
    if not result.ok:
        # A failed read-back can follow a successful insertion. Do not send the
        # same literal action to the planner again after an uncertain outcome.
        task.status = TaskStatus.FAILED
        return 'Результат ввода не подтверждён. Повторно не вводила. ' + (result.error or '')
    if not result.data.get('verified'):
        task.status = TaskStatus.FAILED
        return 'Текст введён, но результат не удалось прочитать обратно. Повторно не вводила.'
    if intent['submit']:
        return 'Выполнила.\n' + result.data.get('output','')
    return 'Написала.'
