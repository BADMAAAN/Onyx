"""Small safe display parser. Model output is never HTML, QML or an action."""
import re


def blocks(content):
    result, lines, language, code = [], [], '', False
    for line in content.split('\n'):
        if line.startswith('```'):
            if lines: result.append(dict(code=code,language=language,text='\n'.join(lines)))
            lines=[]
            code=not code
            language=line[3:].strip()[:30] if code else ''
        else: lines.append(line)
    if lines: result.append(dict(code=code,language=language,text='\n'.join(lines)))
    return result or [dict(code=False,language='',text='')]
