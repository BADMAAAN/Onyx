"""Native UIA metadata events. No key events, text values or pointer stream."""
from time import monotonic, time
from collections import Counter
from threading import Lock


SEMANTIC_EVENTS = {20009: 'invoke', 20010: 'select', 20012: 'select'}
# Names of edit/document/text controls can expose typed/private content. Only
# named controls that support a meaningful action are admitted here.
SEMANTIC_CONTROLS = {50000: 'ButtonControl', 50002: 'CheckBoxControl',
                     50007: 'ListItemControl', 50011: 'MenuItemControl',
                     50013: 'RadioButtonControl', 50019: 'TabItemControl',
                     50024: 'TreeItemControl', 50005: 'HyperlinkControl'}


def cached_semantic_metadata(sender, event_id, rejected=None):
    """Read ONLY the event cache. A stale/incomplete cache yields no event."""
    try:
        operation = SEMANTIC_EVENTS.get(int(event_id))
        if not operation or sender.CachedIsPassword or sender.CachedIsOffscreen:
            if rejected: rejected('protected_or_offscreen_or_event')
            return None
        control_type = SEMANTIC_CONTROLS.get(int(sender.CachedControlType))
        if not control_type:
            if rejected: rejected('control_type')
            return None
        return {'operation': operation, 'process_id': int(sender.CachedProcessId),
                'control_type': control_type, 'name': str(sender.CachedName or '')[:120],
                'automation_id': str(sender.CachedAutomationId or '')[:100],
                'protected': False, 'source': 'uia_event', 'uia_event_id': int(event_id),
                'timestamp': time(), 'observed_monotonic': monotonic()}
    except Exception as exc:
        if rejected: rejected('cache_' + type(exc).__name__)
        return None
class UIAFocusEvents:
    def __init__(self, callback):
        import comtypes
        import comtypes.client
        core=comtypes.client.GetModule('UIAutomationCore.dll')
        self.client=comtypes.client.CreateObject('{ff48dba4-60ef-4201-aa87-54103eef594e}',interface=core.IUIAutomation)
        class Handler(comtypes.COMObject):
            _com_interfaces_=[core.IUIAutomationFocusChangedEventHandler]
            def HandleFocusChangedEvent(self,*args):
                sender=args[-1]
                try:
                    protected=bool(sender.CurrentIsPassword)
                    callback({'process_id':int(sender.CurrentProcessId),
                        'control_type':int(sender.CurrentControlType),
                        'automation_id':'' if protected else str(sender.CurrentAutomationId),
                        'protected':protected})
                except Exception:
                    pass
                return 0
        self.handler=Handler()
        self.client.AddFocusChangedEventHandler(None,self.handler)

    def close(self):
        self.client.RemoveFocusChangedEventHandler(self.handler)


class UIASemanticEvents:
    """Foreground-subtree Invoke/Selection subscription owned by one MTA thread.

    Callback only converts cached primitives and enqueues them. Registration,
    rebind and removal happen on the ambient worker, never in callback/audio/UI.
    """
    def __init__(self, callback, enabled=lambda: True):
        import comtypes
        import comtypes.client
        self.core = core = comtypes.client.GetModule('UIAutomationCore.dll')
        self.client = comtypes.client.CreateObject(core.CUIAutomation8, interface=core.IUIAutomation2)
        self.client.ConnectionTimeout = 500
        self.client.TransactionTimeout = 500
        self.cache = self.client.CreateCacheRequest()
        for name in ('UIA_NamePropertyId', 'UIA_ControlTypePropertyId', 'UIA_ProcessIdPropertyId',
                     'UIA_AutomationIdPropertyId', 'UIA_IsPasswordPropertyId', 'UIA_IsOffscreenPropertyId'):
            self.cache.AddProperty(getattr(core, name))
        self.cache.TreeScope = core.TreeScope_Element
        self.cache.AutomationElementMode = core.AutomationElementMode_None
        self.callback, self.enabled = callback, enabled
        self.root, self.handler, self.identity = None, None, None
        self.registered = []
        self._comtypes = comtypes
        self._diagnostic_lock = Lock()
        self._counts = Counter()
        self._stage = 'ready'
        self._last_error = None

    def _count(self, reason):
        with self._diagnostic_lock:
            self._counts[reason] += 1

    def diagnostics(self):
        # No cached names, window titles, automation ids or COM reads here.
        with self._diagnostic_lock:
            return {'stage': self._stage, 'counts': dict(self._counts),
                    'last_error': self._last_error, 'event_ids': list(self.registered),
                    'hwnd': self.identity[0] if self.identity else None,
                    'process_id': self.identity[1] if self.identity else None}

    def watch(self, window):
        identity = (window.get('hwnd'), window.get('process_id'), window.get('observation_epoch', 0)) if window else None
        if identity == self.identity:
            return
        self.close()
        if not identity or not all(identity[:2]) or not self.enabled():
            self._stage = 'inactive'
            return
        self._count('bind_attempts')
        self._stage = 'binding'
        try:
            root = self.client.ElementFromHandle(identity[0])
            if int(root.CurrentProcessId) != identity[1]:
                self._count('bind_process_mismatch')
                self._stage = 'inactive'
                return
        except Exception as exc:
            self._last_error = {'stage': 'bind', 'type': type(exc).__name__, 'hresult': getattr(exc, 'hresult', None)}
            self._stage = 'error'
            raise
        owner = self
        bound_window = dict(window)
        class Handler(self._comtypes.COMObject):
            _com_interfaces_ = [owner.core.IUIAutomationEventHandler]
            def HandleAutomationEvent(self, *args):
                owner._count('callbacks')
                if not owner.enabled() or owner.identity != identity:
                    owner._count('inactive_callback')
                    return 0
                sender, event_id = args[-2:]
                metadata = cached_semantic_metadata(sender, event_id, owner._count)
                if metadata and metadata['process_id'] == identity[1]:
                    metadata['window'] = bound_window
                    metadata['observation_epoch'] = identity[2]
                    try:
                        owner.callback(metadata)
                        owner._count('delivered')
                    except Exception as exc:
                        owner._count('callback_' + type(exc).__name__)
                elif metadata:
                    owner._count('callback_process_mismatch')
                return 0
        self.root, self.handler, self.identity = root, Handler(), identity
        try:
            for event_id in SEMANTIC_EVENTS:
                self.client.AddAutomationEventHandler(event_id, root, self.core.TreeScope_Subtree, self.cache, self.handler)
                self.registered.append(event_id)
            self._stage = 'subscribed'
            self._last_error = None
            self._count('subscriptions')
        except Exception as exc:
            self.close()
            self._last_error = {'stage': 'subscribe', 'type': type(exc).__name__, 'hresult': getattr(exc, 'hresult', None)}
            self._stage = 'error'
            raise

    def close(self):
        self.identity = None
        for event_id in self.registered:
            try:
                self.client.RemoveAutomationEventHandler(event_id, self.root, self.handler)
            except Exception:
                pass
        self.registered.clear()
        self.root, self.handler = None, None
        self._stage = 'inactive'
