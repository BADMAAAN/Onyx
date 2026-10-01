"""Telegram shortcuts are read from the installed client; recording never sends."""
import json
from pathlib import Path
import re
from time import monotonic, sleep


def shortcuts(executable):
    bindings={}
    folder=Path(executable).parent/'tdata'
    for name in ('shortcuts-default.json','shortcuts-custom.json'):
        path=folder/name
        if not path.exists(): continue
        text=re.sub(r'^\s*//.*$', '', path.read_text(encoding='utf-8-sig'), flags=re.M)
        for row in json.loads(text):
            if row.get('keys') and 'command' in row: bindings[row['keys']]=row['command']
    return bindings


class TelegramActions:
    def __init__(self, registry, uia, screen=None):
        self.registry,self.uia=registry,uia
        self.screen=screen
        self.recording_hwnd=None

    def focus(self):
        from .native import foreground_window
        result=self.registry.execute('open_application',{'name':'telegram'})
        if not result.ok or not result.data.get('verified'): raise RuntimeError(result.error or 'Telegram launch not verified')
        window=foreground_window()
        if window.get('hwnd') != result.data.get('hwnd'):
            raise RuntimeError('Telegram не получил фокус')
        return window

    def press_command(self, window, command):
        from .keyboard import press_hotkey
        from .native import foreground_window
        keys=next((k for k,v in shortcuts(window['executable']).items() if v==command),None)
        if not keys: raise LookupError('Клиент не объявляет shortcut '+command)
        self.registry.check_cancel()
        if foreground_window()['hwnd']!=window['hwnd']: raise InterruptedError('Telegram потерял фокус')
        press_hotkey(keys.split('+'))

    def observe(self):
        return [e for e in self.uia.tree(False) if not e['offscreen'] and e.get('rect')]

    @staticmethod
    def recording(elements):
        return any('RecordLock' in e.get('automation_id','') for e in elements)

    def first_chat(self):
        from .native import foreground_window
        window=self.focus()
        before=self.observe()
        lists=[e['rect'] for e in before if e.get('class_name')=='class Dialogs::InnerWidget' and e.get('rect')]
        rows=[e for e in before if e.get('control_type') in {'ListItemControl','DataItemControl'}
              and e.get('name') and not e.get('offscreen') and e.get('rect')
              and not re.match(r'(?i)^(?:все чаты|all chats|непрочитанные|unread|папка|folder|archive|архив)\b',e['name'])
              and any(r['left']<=e['rect']['center_x']<=r['right'] and
                      r['top']<=e['rect']['center_y']<=r['bottom'] for r in lists)]
        first=min(rows,key=lambda e:e['rect']['top']) if rows else None
        before_header=self._header_text(before)
        if first:
            self.registry.check_cancel()
            if foreground_window()['hwnd']!=window['hwnd']: raise InterruptedError('Telegram потерял фокус')
            self.uia.click_observed(first)
        else:
            self.press_command(window,'first_chat')
        normalize=lambda text:' '.join(re.findall(r'\w+',text.casefold()))
        expected=normalize(re.sub(r'(?i)^(?:канал|группа|channel|group),\s*','',first['name']).split(',')[0]) if first else ''
        verified=False
        deadline=monotonic()+3
        while monotonic()<deadline:
            self.registry.check_cancel()
            if foreground_window()['hwnd']!=window['hwnd']:
                return dict(verified=False,hwnd=window['hwnd'],reason='Telegram lost focus before verification')
            header=self._header_text(self.observe())
            verified=bool(header and ((expected and len(expected)>=3 and expected in normalize(header))
                                      or (not first and normalize(header)!=normalize(before_header))))
            if verified: break
            sleep(.15)
        return dict(verified=verified,hwnd=window['hwnd'],
            method='first visible UIA row + conversation header' if first else 'installed first_chat shortcut',
            verification_required=None if verified else 'Inspect first visible row and conversation header')

    def _header_text(self, elements):
        headers=[e for e in elements if e.get('class_name')=='class HistoryView::TopBarWidget' and e.get('rect')]
        if not headers: return ''
        header=headers[0]
        names=[e.get('name','') for e in elements if e is not header and e.get('name') and e.get('rect')
               and (e.get('control_type') in {'TextControl','EditControl'} or e.get('class_name') in {'Text','Static'})
               and header['rect']['left']<=e['rect']['center_x']<=header['rect']['right']
               and header['rect']['top']<=e['rect']['center_y']<=header['rect']['bottom']]
        if names: return ' '.join(names)
        if not self.screen: return ''
        r=header['rect']
        picture,_=self.screen.capture.capture_image(region=[r[k] for k in ('left','top','right','bottom')])
        try:
            ocr=self.screen.ocr.read(picture)
            return ' '.join(line['text'] for result in ocr.get('results',[]) for line in result.get('lines',[]))
        finally: picture.close()

    def start_recording(self):
        window=self.focus()
        before=self.observe()
        if self.recording(before):
            self.recording_hwnd=window['hwnd']
            return dict(verified=True,recording=True,sent=False,hwnd=window['hwnd'])
        if not any('SendButton' in e.get('automation_id','') and
                   re.search(r'голосов|voice',e.get('name',''),re.I) for e in before):
            raise LookupError('Нет доступной кнопки записи голосового в текущем чате')
        # Telegram's record_voice calls startRecordingAndLock. It does not submit.
        self.press_command(window,'record_voice')
        deadline=monotonic()+3
        while monotonic()<deadline:
            self.registry.check_cancel()
            if self.recording(self.observe()):
                self.recording_hwnd=window['hwnd']
                return dict(verified=True,recording=True,sent=False,hwnd=window['hwnd'],method='record_voice → locked recording')
            sleep(.1)
        self.recording_hwnd=None
        return dict(verified=False,sent=False,recording='unconfirmed',hwnd=window['hwnd'])

    def finish(self, send=False):
        from .native import foreground_window
        from .windows import focus_window_handle
        if not self.recording_hwnd: raise LookupError('Нет текущей записи Onyx')
        token=self.registry.cancellation.get()
        from kk.core.fast_commands import normalize
        request=normalize(getattr(token,'user_request',''))
        if send and not re.fullmatch(r'(?i)(?:отправь|отправить|send)(?:\s+(?:это|голосовое|запись|текущую запись|сообщение|it|the recording))?',request):
            raise PermissionError('Отправка требует отдельной явной команды пользователя')
        focus_window_handle(self.recording_hwnd)
        rows=self.observe()
        def voice_messages(elements):
            return {(e.get('automation_id'),e.get('name'),str(e.get('rect')))
                for e in elements if 'voice' in (e.get('automation_id','')+' '+e.get('name','')).casefold()
                or 'голосов' in e.get('name','').casefold()
                if 'VoiceRecordBar' not in e.get('automation_id','') and 'SendButton' not in e.get('automation_id','')}
        previous=voice_messages(rows)
        pattern=r'^(?:отправить|send)(?: сообщение| message)?$' if send else r'отмен|удалить.*запис|delete recording|cancel|discard'
        matches=[e for e in rows if e.get('control_type')=='ButtonControl' and re.search(pattern,e.get('name',''),re.I)
                 and ('VoiceRecordBar' in e.get('automation_id','') or
                      (send and 'VoiceRecordButton' in e.get('automation_id','')))]
        if len(matches)!=1: raise LookupError('Кнопка завершения записи не найдена однозначно')
        self.registry.check_cancel()
        if foreground_window()['hwnd']!=self.recording_hwnd: raise InterruptedError('Telegram потерял фокус')
        self.uia.click(matches[0]['name'],'ButtonControl')
        deadline=monotonic()+3
        ended=outgoing=False
        while monotonic()<deadline:
            self.registry.check_cancel()
            after=self.observe()
            ended=not self.recording(after)
            outgoing=bool(voice_messages(after)-previous) if send else False
            if ended and (not send or outgoing): break
            sleep(.1)
        if ended: self.recording_hwnd=None
        return dict(verified=ended and (outgoing if send else True),recording=not ended,
            sent=bool(send and outgoing),send_requested=send,
            verification_required=None if ended and (outgoing or not send) else 'Verify outgoing voice message in chat')
