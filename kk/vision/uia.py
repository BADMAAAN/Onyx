from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from time import monotonic, sleep
from typing import Any

import uiautomation as auto

from kk.tools.windows.native import foreground_window


def clean_text(value):
    # UIA providers can split UTF-16 surrogate pairs at their own text limits.
    return str(value).encode('utf-8', 'replace').decode('utf-8')


INTERACTIVE_TYPES = {
    "ButtonControl", "EditControl", "CheckBoxControl", "ComboBoxControl",
    "MenuItemControl", "ListItemControl", "TabItemControl", "HyperlinkControl",
    "RadioButtonControl", "TreeItemControl", "DataItemControl", "SliderControl",
}


def _rect(control: Any) -> dict[str, int] | None:
    try:
        rectangle = control.BoundingRectangle
        if rectangle.width() <= 0 or rectangle.height() <= 0:
            return None
        return {
            "left": int(rectangle.left), "top": int(rectangle.top),
            "right": int(rectangle.right), "bottom": int(rectangle.bottom),
            "center_x": int((rectangle.left + rectangle.right) / 2),
            "center_y": int((rectangle.top + rectangle.bottom) / 2),
        }
    except Exception:
        return None


def _element(control: Any, depth: int = 0, include_text: bool = True) -> dict[str, Any]:
    item = {
        "name": str(getattr(control, "Name", "") or ""),
        "control_type": str(getattr(control, "ControlTypeName", "") or ""),
        "automation_id": str(getattr(control, "AutomationId", "") or ""),
        "class_name": str(getattr(control, "ClassName", "") or ""),
        "enabled": bool(getattr(control, "IsEnabled", True)),
        "offscreen": bool(getattr(control, "IsOffscreen", False)),
        "depth": depth,
        "rect": _rect(control),
        "focused": bool(getattr(control, 'HasKeyboardFocus', False)),
        "password": bool(getattr(control, 'IsPassword', False)),
    }
    if item['password']:
        item['name'] = '[protected]'
        return item
    for pattern, attribute, key in [('GetValuePattern', 'Value', 'value'),
                                     ('GetTogglePattern', 'ToggleState', 'toggle_state'),
                                     ('GetSelectionItemPattern', 'IsSelected', 'selected')]:
        if key == 'value' and not include_text:
            continue
        try:
            value = getattr(getattr(control, pattern)(), attribute)
            item[key] = value if isinstance(value, (bool, int)) else str(value)[:4000]
        except Exception:
            pass
    if include_text and (item['control_type'] in {'DocumentControl', 'EditControl'} or
                         'termcontrol' in item['class_name'].casefold()):
        try:
            pattern = control.GetTextPattern()
            visible = pattern.GetVisibleRanges() if 'termcontrol' in item['class_name'].casefold() else []
            item['text'] = '\n'.join(r.GetText(4000) for r in visible) if visible else pattern.DocumentRange.GetText(4000)
        except Exception:
            pass
    return {key:clean_text(value) if isinstance(value,str) else value for key,value in item.items()}


class UIAutomationService:
    def __init__(self, max_nodes: int = 600, max_depth: int = 18):
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    def _root(self):
        hwnd = foreground_window()["hwnd"]
        from kk.ui.capture_guard import is_own_window
        if is_own_window(hwnd):
            raise RuntimeError('Собственное окно Onyx исключено из целей UI Automation')
        if not hwnd:
            raise RuntimeError("Нет активного окна")
        return auto.ControlFromHandle(hwnd)

    def document_root(self):
        document=self._root().DocumentControl(searchDepth=12)
        if document.Exists(.3,.05) and not document.IsOffscreen:
            return document
        return None

    def document_tree(self):
        root=self.document_root()
        return self.tree(root=root) if root is not None else []

    def tree(self, interactive_only: bool = False, include_text: bool = True, root=None) -> list[dict[str, Any]]:
        root = root if root is not None else self._root()
        viewport = _rect(root)
        result: list[dict[str, Any]] = []
        stack = deque([(root, 0)])
        visited = 0
        deadline = monotonic() + 2
        while stack and visited < self.max_nodes and monotonic() < deadline:
            visited += 1
            control, depth = stack.popleft()
            item = _element(control, depth, include_text)
            self._clip(item, viewport)
            if not interactive_only or item["control_type"] in INTERACTIVE_TYPES:
                result.append(item)
            if depth < self.max_depth and not item['offscreen'] and not self._virtual_history(item):
                try:
                    children = control.GetChildren()
                except Exception:
                    children = []
                stack.extend((child, depth + 1) for child in children)
        return result

    @staticmethod
    def _virtual_history(item):
        # Telegram exposes its entire history in a single blocking GetChildren
        # call, including tens of thousands of offscreen messages. Keep its
        # viewport as evidence and let OCR read visible messages on demand.
        return item.get('class_name') == 'class HistoryInner'

    @staticmethod
    def _clip(item, viewport):
        # Some Qt providers report old, scrolled-out messages as on screen.
        # Reject their geometry before traversing descendants or acting.
        rect = item.get('rect')
        if rect and viewport and (rect['right'] <= viewport['left'] or
                rect['left'] >= viewport['right'] or rect['bottom'] <= viewport['top'] or
                rect['top'] >= viewport['bottom']):
            item['offscreen'] = True

    def _find_control(self, name: str, control_type: str = "", timeout: float = 0) -> tuple[Any | None, dict[str, Any] | None]:
        deadline = monotonic() + max(timeout, 0)
        needle = name.casefold().strip()
        while True:
            root = self._root()
            viewport = _rect(root)
            stack = deque([(root, 0)])
            partial = None
            visited = 0
            while stack and visited < self.max_nodes:
                visited += 1
                control, depth = stack.popleft()
                item = _element(control, depth)
                self._clip(item, viewport)
                type_matches = not control_type or control_type.casefold() in item["control_type"].casefold()
                candidate = item["name"].casefold()
                if type_matches and candidate == needle and not item['offscreen'] and item['enabled']:
                    return control, item
                if type_matches and needle and needle in candidate and partial is None and not item['offscreen'] and item['enabled']:
                    partial = (control, item)
                if depth < self.max_depth and not item['offscreen'] and not self._virtual_history(item):
                    try:
                        stack.extend((child, depth + 1) for child in control.GetChildren())
                    except Exception:
                        pass
            if partial:
                return partial
            if monotonic() >= deadline:
                return None, None
            sleep(0.2)

    def find(self, name: str, control_type: str = "", timeout: float = 0) -> dict[str, Any]:
        _control, item = self._find_control(name, control_type, timeout)
        return {"found": item is not None, "element": item}

    def click(self, name: str, control_type: str = "", timeout: float = 2) -> dict[str, Any]:
        control, item = self._find_control(name, control_type, timeout)
        if control is None or item is None:
            raise LookupError(f"UI element not found: {name}")
        before = foreground_window()
        try:
            control.GetInvokePattern().Invoke()
        except Exception:
            control.Click(simulateMove=False)
        sleep(0.2)
        return {"clicked": item, "before": before, "after": foreground_window(), "verified": False,
                "verification_required": 'Observe UI and check requested postcondition'}

    def click_observed(self, element: dict[str, Any]) -> dict[str, Any]:
        """Invoke the observed control, disambiguating duplicate chat titles."""
        target = element.get('rect') or {}
        matches = [(control, item) for control, item in self.controls()
                   if item.get('control_type') == element.get('control_type')
                   and item.get('name') == element.get('name')
                   and item.get('automation_id') == element.get('automation_id')
                   and item.get('rect') == target]
        if len(matches) != 1:
            raise LookupError('Наблюдаемый элемент больше не однозначен')
        control, item = matches[0]
        try: control.GetInvokePattern().Invoke()
        except Exception: control.Click(simulateMove=False)
        return {'clicked': item, 'verified': False}

    def controls(self, root=None):
        root = root if root is not None else self._root()
        viewport = _rect(root)
        stack, visited = deque([(root, 0)]), 0
        deadline = monotonic() + 3
        while stack and visited < self.max_nodes and monotonic() < deadline:
            control, depth = stack.popleft()
            visited += 1
            item = _element(control, depth)
            self._clip(item, viewport)
            if not item['offscreen'] and not item['password'] and item['enabled']:
                yield control, item
            if depth < self.max_depth and not item['offscreen'] and not self._virtual_history(item):
                try: stack.extend((c, depth + 1) for c in control.GetChildren())
                except Exception: pass

    def focused(self):
        control = auto.GetFocusedControl()
        return control, _element(control)

    def set_value(self, name, value, control_type='Edit'):
        control, item = self._find_control(name, control_type)
        if control is None or item['password'] or not item['enabled'] or item['offscreen']:
            raise LookupError('Editable UIA control not found')
        control.SetFocus()
        control.GetValuePattern().SetValue(value)
        actual = _element(control)
        if actual.get('value') != value: raise RuntimeError('UIA value verification failed')
        return {'verified': True, 'element': actual}

    def set_toggle(self, name, enabled):
        matches=[(control,item) for control,item in self.controls()
                 if name.casefold() in item['name'].casefold() and 'toggle_state' in item]
        if len(matches)!=1: raise LookupError('Не найден однозначный переключатель '+name)
        control,item=matches[0]
        expected=int(enabled)
        if item['toggle_state']!=expected: control.GetTogglePattern().Toggle()
        deadline=monotonic()+2
        while monotonic()<deadline:
            actual=_element(control)
            if actual.get('toggle_state')==expected:
                return {'verified':True,'element':actual,'enabled':enabled}
            sleep(.05)
        raise RuntimeError('Состояние переключателя не подтверждено')
