"""Audio-grounded second pass and fragment clarification, without LLM repair."""
import re
from kk.core.desktop_commands import parse_plan
from kk.core.fast_commands import normalize


def suspicion(result, resolver=None, intents=None):
    reasons = []
    confidence = result.intent_confidence if result.intent_confidence is not None else result.confidence
    if not result.accepted or confidence < .65:
        reasons.append('low_confidence')
    text = normalize(result.text)
    from .streaming import unfinished
    if unfinished(text): reasons.append('unfinished_phrase')
    plan = parse_plan(text, intents=intents)
    if plan:
        for step in plan:
            if step['kind'] == 'volume' and not step['valid']: reasons.append('invalid_number')
            if step.get('monitor') is not None and step['monitor'] < 1: reasons.append('invalid_monitor')
            # Installation/discovery is a desktop concern, not an acoustic rejection.
            # A clear transcript is allowed to reach the resolver and its fallbacks.
            if step.get('app') and resolver:
                from kk.core.personal_intents import DEFAULT_INTENTS
                known = (intents or DEFAULT_INTENTS).resolve(step['app'])
                if not known and not resolver.inspect(step['app'])['found']:
                    reasons.append('application_not_found')
    # A fluent multi-step request need not fit the fast-command grammar.
    # Acoustic confidence still gates the general agent just as it gates aliases.
    if re.search(r'(?i)\b(?:громкость|volume)\b', text) and re.search(r'(?i)\b(?:стол|стул|стоп|table)\b', text):
        reasons.append('suspicious_phrase')
    if re.search(r'(?i)спасибо за просмотр|подписывайтесь на канал|субтитры', text):
        reasons.append('suspicious_phrase')
    return list(dict.fromkeys(reasons))


def fragment_question(result):
    text = normalize(result.text)
    passes = getattr(result, 'passes', [])
    if 'passes_disagree' in getattr(result, 'rejection_reason', ''):
        variants = [p.get('normalized_transcript') or p.get('raw_transcript', '') for p in passes]
        targets = [re.search(r'(?i)\b(папку|файл|документ)\s+(.+?)(?:\s+на рабочем столе|[.!?]|$)', v) for v in variants]
        if targets and all(targets) and len({m[2].strip() for m in targets}) > 1:
            names = list(dict.fromkeys(m[2].strip() for m in targets))
            return 'Уточни название ' + {'папку':'папки','файл':'файла','документ':'документа'}[targets[-1][1].casefold()] + ': ' + ' или '.join('«'+n+'»' for n in names) + '?'
        from kk.core.command_language import has_unquoted_negation
        if any(has_unquoted_negation(v) for v in variants):
            return "Should I perform or avoid that action?" if getattr(result, 'language', '') == 'en' else 'Это действие выполнить или не выполнять?'
    if re.search(r'(?i)\b(?:папку|файл|документ)\b', text):
        return 'Не расслышала название объекта. Повтори его, пожалуйста.'
    if re.match(r'(?i)^(?:расскажи|объясни|почему|что такое|кто такой)\b', text):
        return 'Не расслышала вопрос целиком. Повтори, пожалуйста.'
    plan = parse_plan(text)
    if plan and any(s.get('monitor') is not None and s['monitor'] < 1 for s in plan):
        return 'На какой экран перенести окно?'
    if plan and len(plan) > 1:
        return 'Не расслышала часть команды. Повтори её, пожалуйста.'
    if plan and plan[0]['kind'] == 'volume':
        value = plan[0]['level']
        return f'{value} процентов?' if plan[0]['valid'] else 'Какую громкость поставить — от 0 до 100 процентов?'
    if plan and plan[0].get('app'):
        return 'Не уверена, что расслышала команду целиком. Правильно: «' + result.text.strip(' .!?') + '»?'
    words = sorted((w for w in result.words if w.get('word', '').strip()), key=lambda w: w.get('probability', 1))
    if words and words[0].get('probability', 1) < .65:
        return 'Не расслышала реплику целиком. Повтори, пожалуйста.'
    if re.search(r'(?i)открой|запусти|open', text): return 'Какое приложение открыть?'
    return 'Какое действие выполнить?'


def recognize_final(stt, audio, cancelled=lambda: False):
    decode = getattr(stt, 'decode_full', None)
    first = decode(audio) if decode else stt.transcribe(audio)
    first.chosen_stt_pass = 'fast'
    reasons = suspicion(first, getattr(stt, 'resolver', None), getattr(stt, 'personal_intents', None))
    first.passes = [dict(pass_name='fast', raw_transcript=first.raw_transcript,
        normalized_transcript=first.normalized_transcript, confidence=first.confidence,
        accepted=first.accepted, reasons=reasons, latency_seconds=first.latency_seconds)]
    if not reasons or cancelled(): return first
    second_stt = stt if decode else getattr(stt, 'second_pass', None)
    if second_stt is None:
        first.accepted = False
        first.rejection_reason = ','.join(reasons + ['second_pass_unavailable'])
        first.clarification = fragment_question(first)
        return first
    try:
        second = decode(audio, retry=True) if decode else second_stt.transcribe(audio)
        second.chosen_stt_pass = 'turbo_retry' if decode else 'accurate'
        second_reasons = suspicion(second, getattr(stt, 'resolver', None), getattr(stt, 'personal_intents', None))
        second.passes = first.passes + [dict(pass_name=second.chosen_stt_pass, raw_transcript=second.raw_transcript,
            normalized_transcript=second.normalized_transcript, confidence=second.confidence,
            accepted=second.accepted, reasons=second_reasons, latency_seconds=second.latency_seconds)]
        second.latency_seconds += first.latency_seconds
        # A changed *valid* application/number requires explicit confirmation.
        # Invalid first grammar (e.g. "стол процентов") can be replaced only by
        # the second decoder's acoustic result, never a dictionary substitution.
        from kk.tools.windows.app_resolver import canonical
        def signature(text):
            plan = parse_plan(text, intents=getattr(stt, 'personal_intents', None))
            return [dict(s, app=canonical(s['app'])) if s.get('app') else s for s in plan] if plan else plan
        first_plan, second_plan = signature(first.text), signature(second.text)
        first_valid = bool(first_plan) and not any(r in reasons for r in ('invalid_number','invalid_monitor','application_not_found','unparsed_command','suspicious_phrase'))
        if first_valid and first_plan != second_plan:
            second_reasons.append('passes_disagree')
        elif not first_plan:
            # No parsed plan is not evidence of agreement. In particular, a
            # negative/semantic request must not become an executable positive
            # fragment just because the retry is more confident. Ignore only
            # wake addressing and outer sentence punctuation, not inner scope.
            first_request = normalize(first.text).casefold()
            second_request = normalize(second.text).casefold()
            if first_request != second_request:
                second_reasons.append('passes_disagree')
        second.accepted = not second_reasons and not cancelled()
        second.rejection_reason = ','.join(second_reasons)
        if not second.accepted: second.clarification = fragment_question(second)
        return second
    except Exception as exc:
        first.accepted = False
        first.rejection_reason = ','.join(reasons + ['second_pass_failed:' + type(exc).__name__])
        first.clarification = fragment_question(first)
        return first


class Clarification:
    """One expiring pending fragment. Only explicit acoustic confirmation resumes."""
    def __init__(self):
        self.pending = None
        self.deadline = 0

    def ask(self, result):
        from time import monotonic
        self.pending, self.deadline = result, monotonic() + 30
        return result.clarification or fragment_question(result)

    def consume(self, result):
        from time import monotonic
        pending, self.pending = self.pending, None
        if not pending or monotonic() > self.deadline or not result.accepted: return result
        answer = normalize(result.text).casefold()
        plan = parse_plan(pending.text)
        if answer in {'нет', 'отмена', 'no', 'cancel'}: return result
        if answer in {'да', 'верно', 'правильно', 'yes'}:
            if not plan or len(plan) != 1 or (plan[0]['kind'] == 'volume' and not plan[0]['valid']) or 'application_not_found' in pending.rejection_reason:
                return result
            result.text = pending.text
            result.normalized_transcript = normalize(pending.text)
            result.passes += [dict(pass_name='user_confirmation', confirmed=pending.normalized_transcript)]
        elif plan and plan[0]['kind'] == 'volume':
            from kk.core.numbers import spoken_number
            number = spoken_number(answer)
            if number is not None and 0 <= number <= 100:
                result.text = f'громкость {number}'
                result.normalized_transcript = result.text
        elif plan and plan[0]['kind'] == 'open' and len(plan) == 1 and not parse_plan(result.text):
            result.text = 'открой ' + result.text
            result.normalized_transcript = normalize(result.text)
        return result


class RecognitionRecovery:
    """A failure chain gets one spoken prompt; bad audio cannot rearm it.

    Reset the budget only on explicit recording/session control or an accepted
    non-echo final. Duplicate finals remain consumed even across budget resets.
    No audio is retained here.
    """
    def __init__(self):
        from collections import deque
        self.seen = deque(maxlen=128)
        self.prompted = False

    def claim(self, result):
        identity = (getattr(result, 'turn_id', None) or getattr(result, 'trace_id', None),
                    getattr(result, 'audio_id', None))
        keys = {value for value in identity if value}
        if keys and any(keys.intersection(previous) for previous in self.seen):
            return False
        if keys:
            self.seen.append(keys)
        return True

    def reject(self):
        speak = not self.prompted
        self.prompted = True
        return speak

    def reset(self):
        self.prompted = False
