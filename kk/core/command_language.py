"""Small syntax helpers; literal payloads remain outside command rewriting."""
import re


def english_request(text):
    from .fast_commands import normalize
    text = normalize(text)
    if re.match(r"(?i)^(?:please|open|close|type|don't|do not|what|can you|explain|hello)\b", text):
        return True
    return not re.search(r'[А-Яа-яЁё]', text) and len(re.findall(r'[A-Za-z]+', text)) >= 3


def render_action_result(text, request):
    if not english_request(request): return text
    for source, target in (('Открыла ', 'Opened '), ('Закрыла ', 'Closed ')):
        if text.startswith(source): return target + text[len(source):]
    return {'Написала.':'Typed.', 'Написала в новом черновике Obsidian.':'Typed in a new Obsidian draft.'}.get(text, text)


def polite_request(text):
    """Only a leading conventional request; never touch dictated data."""
    return re.sub(r'(?i)^не\s+мог(?:ла)?\s+бы\s+(?:ты\s+)?', '', text)


def has_unquoted_negation(text):
    # A quoted filename/payload containing «не» is data. Unquoted negation
    # needs semantic scope resolution before any deterministic action occurs.
    unquoted = re.sub(r'"[^"\n]*"|«[^»\n]*»', '', polite_request(text))
    return bool(re.search(r"(?i)\b(?:не|нельзя|not|never|don['’]t)\b", unquoted))


TARGET_WORDS = {'папку':'folder', 'папка':'folder', 'каталог':'folder',
                'файл':'file', 'изображение':'image', 'картинку':'image', 'документ':'document'}
LOCATIONS = {'на рабочем столе':'desktop', 'в загрузках':'downloads', 'из загрузок':'downloads'}


def typed_open(command):
    """Return (explicit type present, complete step or None).

    A partial explicit target must fall back to Brain, never to AppResolver.
    Location can precede or follow the target; it is removed only at boundaries.
    """
    opened = re.fullmatch(r'(?is)(?:открой|открыть|open)\s+(.+)', command)
    if not opened:
        return False, None
    body, location = opened[1].strip(), ''
    for phrase, value in LOCATIONS.items():
        if body.casefold().startswith(phrase+' '):
            body, location = body[len(phrase):].strip(), value
            break
    first, _, name = body.partition(' ')
    target_type = TARGET_WORDS.get(first.casefold())
    if not target_type:
        unquoted = re.sub(r'"[^"\n]*"|«[^»\n]*»', '', body)
        words = re.findall(r'\w+', unquoted.casefold())
        return bool(set(words) & set(TARGET_WORDS)), None
    name = name.strip()
    for phrase, value in LOCATIONS.items():
        if name.casefold().endswith(' '+phrase):
            if location and location != value:
                return True, None
            name, location = name[:-len(phrase)].strip(), value
            break
    # A quoted name is opaque; conjunctions there belong to the filename.
    quoted = len(name) >= 2 and (name[0], name[-1]) in {('"','"'),('«','»')}
    if not name or (not quoted and re.search(r'(?i)\b(?:и|не|нет|хотя|напиши|введи|выполни)\b', name)):
        return True, None
    return True, dict(kind='target', name=name[1:-1] if quoted else name,
                      type=target_type, location=location)
