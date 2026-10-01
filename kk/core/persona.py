"""Configurable response style, separate from authorization and action planning."""
import re

class Persona:
    def __init__(self, gender='female', style='Естественно, коротко, тепло; лёгкий юмор только к месту.'):
        self.gender,self.style=gender,style
    def prompt(self):
        return '\nСтиль общения: '+self.style+' Не повторяй команду. Не начинай каждый ответ с «Конечно». '+('О себе в русском говори в женском роде.' if self.gender=='female' else '')
    def render(self,text):
        if self.gender!='female': return text
        forms={'Открыл':'Открыла','Нашёл':'Нашла','Перенёс':'Перенесла','Запомнил':'Запомнила','Скрыл':'Скрыла','Показал':'Показала','Готов':'Готова'}
        return re.sub(r'\b(?:'+ '|'.join(forms) +r')\b',lambda m:forms[m[0]],text)
