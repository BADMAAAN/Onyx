"""Window intent grammar; referents are bound at execution time, not plan time."""
import re


def window_intent(text, context=''):
    text = re.sub(r'(?i)(?:^|[,\s]+)(?:пожалуйста|please)(?:[,\s]+|$)', ' ', text).strip(' ,.!?')
    text = re.sub(r'(?i)^close\b', 'закрой', text)
    text = re.sub(r'(?i)\bclose$', 'закрой', text)
    # STT may place one prosodic comma next to the window verb. Consume that
    # syntax boundary only; commas in names/payloads and multiple clauses stay
    # outside this grammar. The final transcript itself remains unchanged.
    verbs = r'разверни|сверни|восстанови|закрой'
    comma = re.fullmatch(rf'({verbs})\s*,\s*([^,]+)|([^,]+?)\s*,\s*({verbs})', text, re.I)
    if comma:
        verb, target = (comma[1], comma[2]) if comma[1] else (comma[4], comma[3])
        if re.search(r'(?i)\b(?:открой|запусти|напиши|введи|вставь|выполни|'
                     r'разверни|сверни|восстанови|закрой|не|нет|хотя)\b', target):
            return None
        text = verb + ' ' + target
    folded=text.casefold().strip(' .!?')
    if re.search(r',|\sи\s',folded): return None
    operation=None; target=''
    if re.fullmatch(r'(?:сделай\s+)?(?:(?:его|ее|её|окно)\s+)?(?:на\s+)?(?:full|полный)', folded): operation='maximize'
    elif re.fullmatch(r'(?:сделай\s+)?(?:(?:его|ее|её|окно|это окно)\s+)?(?:на\s+)?(?:полный|весь)\s+экран',folded): operation='maximize'
    elif folded in {'включи полноэкранный режим','выключи полноэкранный режим'}: operation='fullscreen'
    else:
        match=re.fullmatch(r'(разверни|сверни|восстанови|закрой)(?:\s+(.+?))?(?:\s+на весь экран)?',text,re.I)
        if match:
            operation={'разверни':'maximize','сверни':'minimize','восстанови':'restore','закрой':'close'}[match[1].casefold()]
            target=match[2] or ''
        else:
            match=re.fullmatch(r'(?:а\s+)?(.+?)\s+(разверни|сверни|восстанови|закрой)',text,re.I)
            if match:
                operation={'разверни':'maximize','сверни':'minimize','восстанови':'restore','закрой':'close'}[match[2].casefold()]
                target=match[1]
    if operation:
        if re.search(r'(?i)\b(?:не|нет|хотя)\b', target): return None
        if target.casefold() in {'его','её','ее','окно','это окно'}: target=''
        if operation == 'close' and target.casefold().startswith('окно '):
            return dict(kind='window',operation=operation,app=target[5:].strip(),scope='window')
        return dict(kind='window',operation=operation,app=target)
    return None
