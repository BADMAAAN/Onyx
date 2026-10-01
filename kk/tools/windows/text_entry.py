"""Literal focused input with independently observed text and explicit submission."""
from time import monotonic, sleep
import re
from .native import foreground_window
from .keyboard import type_text, press_hotkey


class TextEntry:
    def __init__(self, registry, uia):
        self.registry, self.uia = registry, uia
        self.pending = None

    def _text(self):
        try:
            _, focused = self.uia.focused()
            if focused.get('text'): return str(focused['text'])
            return '\n'.join(str(e.get('value') or e.get('text') or e.get('name') or '')
                for e in self.uia.tree(False) if not e.get('password') and not e.get('offscreen'))
        except Exception:
            return ''

    def enter(self, text='', submit=False, target_guard=None):
        self.registry.check_cancel()
        before = foreground_window()
        hwnd = before.get('hwnd')
        terminal = any(s in (before.get('process','')+' '+before.get('title','')).casefold()
                       for s in ('powershell','pwsh','windowsterminal','command prompt'))
        if not hwnd: raise LookupError('Нет активного окна для ввода')
        def guard():
            self.registry.check_cancel()
            current = foreground_window()
            if current.get('hwnd') != hwnd or (before.get('process_id') and current.get('process_id') != before['process_id']):
                raise InterruptedError('Целевое окно изменилось во время ввода')
            if target_guard:
                target_guard()
        if not text and submit:
            if not self.pending or self.pending['hwnd'] != hwnd or (self.pending.get('process_id') and self.pending['process_id'] != before.get('process_id')):
                raise LookupError('Нет подтверждённого текста для выполнения в текущем окне')
            text = self.pending['text']
            if terminal and not self._text().rstrip().endswith(text):
                raise LookupError('Подтверждённый ввод изменился; выполнение отменено')
            typed = True
        else:
            if not text: raise ValueError('Пустой текст')
            if terminal and not submit and any(c in text for c in '\r\n'):
                raise ValueError('Многострочный ввод в терминал может выполнить команду; требуется явное исполнение')
            if terminal:
                deadline = monotonic()+3
                while monotonic()<deadline:
                    self.registry.check_cancel()
                    if foreground_window().get('hwnd') != hwnd:
                        raise InterruptedError('Окно потеряло фокус до ввода')
                    candidates=[(c,e) for c,e in self.uia.controls() if 'termcontrol' in e.get('class_name','').casefold()]
                    if candidates:
                        candidates[0][0].SetFocus()
                        sleep(.15)
                        break
                    sleep(.1)
            control, element = self.uia.focused()
            if element.get('password'):
                raise PermissionError('Защищённое поле не является обычным полем ввода')
            editable = element.get('control_type') in {'EditControl','DocumentControl','ComboBoxControl'}
            if not editable and not terminal:
                raise LookupError('Сначала найди и сфокусируй редактируемое поле через UIA')
            # Unicode input inserts at the caret and never replaces a user's draft.
            baseline = str(element.get('value') or element.get('text') or '') or self._text()
            self.pending = None
            guard()
            try:
                type_text(text, guard)
            except (OSError,InterruptedError):
                self.registry.check_cancel()
                return dict(verified=False,typed=True,submitted=False,hwnd=hwnd,
                            reason='Ввод прерван; часть текста могла быть введена. Повторять ввод нельзя')
            deadline = monotonic() + 3
            typed = False
            while monotonic() < deadline:
                self.registry.check_cancel()
                if foreground_window().get('hwnd') != hwnd:
                    return dict(verified=False, typed=True, submitted=False, hwnd=hwnd,
                                reason='Окно потеряло фокус после ввода; повторять ввод нельзя')
                try:
                    _, current = self.uia.focused()
                    visible = str(current.get('value') or current.get('text') or '') or self._text()
                except Exception:
                    return dict(verified=False, typed=True, submitted=False, hwnd=hwnd,
                                reason='Ввод выполнен, но чтение поля завершилось ошибкой; повторять ввод нельзя')
                if text in visible and visible.count(text) > baseline.count(text):
                    typed = True
                    break
                sleep(.1)
            if typed: self.pending = {'hwnd':hwnd, 'process_id':before.get('process_id',0), 'text':text}
        if not typed:
            return dict(verified=False, typed=True, submitted=False, hwnd=hwnd,
                        reason='Ввод выполнен, но текст не прочитан обратно; повторять ввод нельзя')
        if not submit:
            return dict(verified=True, typed=True, submitted=False, hwnd=hwnd, characters=len(text))
        self.registry.check_cancel()
        if foreground_window().get('hwnd') != hwnd:
            raise InterruptedError('Окно потеряло фокус перед выполнением')
        old = self._text()
        press_hotkey(['enter'])
        self.pending = None
        deadline = monotonic() + 5
        after = old
        completed = False
        while monotonic() < deadline:
            self.registry.check_cancel()
            if foreground_window().get('hwnd') != hwnd: break
            after = self._text()
            completed = bool(terminal and after != old and re.search(r'(?:^|\n)\s*(?:\([^\n]*\)\s*)?PS [^\r\n]*>\s*$',after.rstrip()))
            if completed: break
            sleep(.15)
        return dict(verified=completed, typed=True, submitted=True, hwnd=hwnd, command_completed=completed,
                    output=after[-10000:], evidence='PowerShell prompt returned after Enter' if completed else 'Submission observed; completion needs further verification')
