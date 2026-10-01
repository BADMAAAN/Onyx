"""Text preparation and short PCM ramps; no unsupported pitch/speed controls."""
import re
import numpy as np

PRONUNCIATION = {'Onyx':'Оникс','ChatGPT':'Чат Джи Пи Ти','GPT':'Джи Пи Ти','Firefox':'Файерфокс','YouTube':'Ютуб',
    'AyuGram':'Аюграм','Discord':'Дискорд','Hearthstone':'Хартстоун','Battle.net':'Батл нет','Codex':'Кодекс','PowerShell':'Пауэршелл','VS Code':'Ви Эс Код',
    'ifconfig':'иф конфиг','ipconfig':'ай пи конфиг', 'OK':'Окей', 'Python':'Пайтон',
    'Whisper':'Виспер', 'Qwen':'Квен', 'Ollama':'Оллама', 'Focusrite':'Фокусрайт',
    'CUDA':'Куда', 'STT':'эс ти ти', 'TTS':'ти ти эс', 'RAM':'рам', 'SQLite':'эс кью лайт'}

LETTER_NAMES = dict(zip('abcdefghijklmnopqrstuvwxyz',
    ('эй','би','си','ди','и','эф','джи','эйч','ай','джей','кей','эл','эм','эн','оу','пи','кью','ар','эс','ти','ю','ви','дабл ю','экс','уай','зед')))
LATIN_SOUNDS = dict(zip('abcdefghijklmnopqrstuvwxyz',
    ('а','б','к','д','е','ф','г','х','и','дж','к','л','м','н','о','п','к','р','с','т','у','в','в','кс','и','з')))


def _latin(match):
    word = match.group()
    if word.isupper() and len(word) <= 8:
        return ' '.join(LETTER_NAMES[c] for c in word.casefold())
    value = word.casefold()
    for letters, sound in (('tion','шн'),('sh','ш'),('ch','ч'),('th','т'),('ph','ф'),('oo','у'),('ee','и')):
        value = value.replace(letters, sound)
    return ''.join(LATIN_SOUNDS.get(c, c) for c in value)

def prepare(text, pronunciation=None):
    text=re.sub(r'\[([^]]+)\]\([^)]+\)',r'\1',text)
    text=re.sub(r'[`*_#]','',text)
    dictionary = dict(PRONUNCIATION)
    dictionary.update(pronunciation or {})
    for word,sound in sorted(dictionary.items(),key=lambda row:-len(row[0])):
        text=re.sub(r'(?<!\w)'+re.escape(word)+r'(?!\w)',sound,text,flags=re.I)
    text=re.sub(r'\s*[—–]\s*',', ',text)
    # Silero RU v4 removes Latin-only words and raises on an empty remainder.
    # Keep original tool/search text untouched; pronunciation is a TTS-only view.
    text=re.sub(r'[A-Za-z]+', _latin, text)
    return text.strip()

def ramp(audio,rate):
    audio=np.asarray(audio,dtype=np.float32).copy()
    count=min(len(audio)//2,int(rate*.006))
    if count:
        window=np.linspace(0,1,count,dtype=np.float32)
        audio[:count]*=window; audio[-count:]*=window[::-1]
    return audio
