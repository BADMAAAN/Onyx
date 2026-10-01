"""A voice session outlives individual turns and closes after farewell playback."""
from time import monotonic

CLOSINGS = {'всё','все','спасибо','остановись','хватит','до связи','на этом всё','на этом все'}


class ConversationSession:
    def __init__(self, timeout=25, state=None):
        self.timeout, self.deadline, self.state = timeout, 0, state
        self._active=False; self.phase='sleeping'; self.farewells=0
    @property
    def active(self): return self._active
    @active.setter
    def active(self,value):
        self._active=bool(value)
        if not value: self.phase='sleeping'
        if self.state:
            self.state.conversation_active=self._active
            if not self._active:
                self.state.set_desktop_inline_live(False)
    def touch(self,now=None):
        self.active=True
        self.phase='active'
        self.deadline=(monotonic() if now is None else now)+self.timeout
    def expire(self,busy=False,now=None):
        now=monotonic() if now is None else now
        if self.active and busy:
            self.deadline=now+self.timeout
        if self.active and now>=self.deadline:
            self.active=False
            return True
        return False
    def transition(self, state, detail=''):
        if self.active and self.phase!='closing':
            name=state.value.casefold()
            self.phase=name if name in {'thinking','acting','speaking','listening'} else 'active'
    def closing(self,text):
        return self.active and text.casefold().strip(' .!?') in CLOSINGS
    def farewell(self):
        self.phase='closing'
        answer=('Хорошо.','Окей.','Если что — я рядом.')[self.farewells%3]
        self.farewells+=1
        return answer
