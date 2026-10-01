"""Conservative signed command scope, checked at the shared executor boundary.

Unknown negative syntax may converse/clarify, but cannot authorize side effects.
Only complete positive clauses in the SAME committed request grant exceptions.
"""
import re

from .command_language import has_unquoted_negation, polite_request


def request_scope(text):
    from .fast_commands import normalize
    from .literal_intents import literal_intent
    from .desktop_commands import parse_plan
    from .personal_intents import corrected
    text = polite_request(normalize(text))
    literal = literal_intent(text)
    if literal:
        return dict(restricted=False, prohibited=[], allowed=[], literal=literal)
    correction = bool(re.search(r'(?i)(?:[,;]|\.{2,}|…)\s*(?:нет|no|actually)\b', text))
    restricted = has_unquoted_negation(text) or correction
    scope = dict(restricted=restricted, prohibited=[], allowed=[], unresolved=[])
    if not restricted:
        return scope
    if correction:
        revised = corrected(text)
        if revised != text and not has_unquoted_negation(revised):
            scope['allowed'] = parse_plan(revised) or []
            scope['prohibited'] = [text.split(',')[0]]
            return scope
        # A withdrawn request grants no action, even if its earlier clause did.
        scope['prohibited'] = [text]
        return scope
    # Split only command boundaries, never arbitrary names or quoted data.
    clauses = re.split(r'(?i)(?:[,;]\s*|\s+(?:и|and)\s+)(?=(?:открой|открыть|запусти|open|launch|закрой|close|не|don[’\x27]t|do not)\b)', text)
    for clause in clauses:
        if has_unquoted_negation(clause):
            scope['prohibited'].append(clause)
        else:
            clause = re.sub(r'(?i)\s+instead$', '', clause).strip()
            plan = parse_plan(clause)
            if plan:
                scope['allowed'].extend(plan)
            else:
                scope['unresolved'].append(clause)
    # Contradictory same-target instructions need clarification, not a guess.
    from kk.tools.windows.app_resolver import canonical
    for clause in scope['prohibited']:
        positive = re.sub(r"(?i)^(?:не\s+|do not\s+|don['’]t\s+|never\s+)", '', clause.strip())
        positive = re.sub(r'(?i)^открывай\b', 'открой', positive)
        positive = re.sub(r'(?i)^закрывай\b', 'закрой', positive)
        denied = parse_plan(positive) or []
        if any(a['kind'] == d['kind'] and a.get('operation') == d.get('operation') and
               canonical(a.get('app', '')) == canonical(d.get('app', ''))
               for a in scope['allowed'] for d in denied):
            scope['unresolved'].append('contradictory_same_target')
            scope['allowed'] = []
    return scope


def validate_action(scope, name, arguments):
    if not scope.get('restricted'):
        return
    from kk.tools.windows.app_resolver import canonical
    for step in scope['allowed']:
        kind = step['kind']
        if kind == 'open' and name == 'open_application' and canonical(arguments.get('name', '')) == canonical(step['app']):
            return
        if kind == 'target' and name == 'open_desktop_target' and all(arguments.get(k, '') == step.get(k, '') for k in ('name','type','location')):
            return
        if kind == 'url' and name == 'open_url' and arguments.get('url') == step['url']:
            return
        if kind == 'window' and step['operation'] == 'close' and name == 'close_application' and canonical(arguments.get('query', '')) == canonical(step['app']):
            return
    raise PermissionError('Действие не разрешено областью отрицания/самоисправления. Уточни только спорное действие.')
