from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from contextvars import ContextVar
from contextlib import nullcontext
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel, PermissionPolicy


@dataclass(slots=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    permission: PermissionLevel
    handler: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, policy: PermissionPolicy, memory: MemoryDatabase, logger: logging.Logger):
        self.policy = policy
        self.memory = memory
        self.logger = logger
        self._tools: dict[str, Tool] = {}
        self.cancellation = ContextVar('tool_cancellation', default=None)
        self.ambient = None

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def check_cancel(self):
        self.policy.check_stop()
        token = self.cancellation.get()
        if token:
            token.check()

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def schemas_for(self, request: str) -> list[dict[str, Any]]:
        """Scope the advertised catalog by domain, never execute a special scenario.

        A large catalog crowds local-model context. The full registry and policy
        remain authoritative, and desktop/mixed requests keep the full catalog.
        """
        if self.policy.autonomy == PermissionLevel.DANGEROUS:
            return self.schemas()
        text = request.casefold()
        common = {'observe_context','get_desktop_context','get_monitors','get_system_info','recall_memories'}
        selected = set()
        if re.search(r'файл|папк|каталог|readme|\bfile\b|directory|folder',text):
            selected |= {'search_files','list_directory','read_file','read_text_file','write_file','replace_in_file',
                'create_file','create_directory','copy','move','rename','delete','get_file_info','open_file','open_folder'}
        if re.search(r'\bgit\b|терминал|тест|build|terminal|powershell|\bcmd\b|codex|кодекс',text):
            selected |= {'run_terminal','git_status','codex_status','open_codex','run_codex_task','codex_last_result',
                         'list_directory','read_file','search_files'}
        if re.search(r'музык|трек|песн|проигрывател|\bmusic\b|\btrack\b|\bmedia\b',text):
            selected |= {'get_media_sessions','get_current_media','get_media_info','pause_media','resume_media',
                         'play_pause_media','next_track','previous_track','get_audio_sessions','get_volume','set_volume'}
        desktop = re.search(r'запомни|открой|запусти|напиши|поиск|open|launch|search|блокнот|окно|экран|монитор|браузер|кнопк|notepad|window|screen|monitor|browser|button',text)
        if not selected or desktop: return self.schemas()
        return [tool.schema() for name,tool in self._tools.items() if name in selected | common]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def planning_schemas(self, request):
        from kk.core.brain import ToolCapabilityRouter
        context = getattr(self, 'desktop_context', None)
        return ToolCapabilityRouter().select(self.schemas(), request,
            context.recent() if context else '')

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        arguments = arguments or {}
        settings=getattr(self,'internet_settings',None)
        if settings and settings.get('internet','off')=='off' and (
                name in {'open_url','run_terminal','run_codex_task','open_codex'} or name=='browser_control'):
            return ToolResult(False,error='Internet is off; external search/open is disabled')
        safe_arguments = self._safe_arguments(name, arguments)
        started = monotonic()
        token = self.cancellation.get()
        if token:token.mark('tool_execute_start', tool=name)
        tool = self._tools.get(name)
        if not tool:
            result=ToolResult(False,error=f'Неизвестный инструмент: {name}')
            self.memory.record_action(name,safe_arguments,result.as_dict(),False,'UNKNOWN',0)
            if token:token.mark('tool_execute_end',tool=name,ok=False,error_type='UnknownTool')
            return result
        self.logger.info("[TOOL] %s session=%s turn=%s response=%s task=%s", name,
            getattr(token,"session_id",None),getattr(token,"turn_id",None),getattr(token,"response_id",None),
            getattr(token,"trace_id",getattr(token,"generation_id",None)))
        self.logger.info("[ARGS] %s", json.dumps(safe_arguments, ensure_ascii=False))
        try:
            token = self.cancellation.get()
            if token:
                token.check()
            self._validate(tool, arguments)
            clarification = getattr(token, 'target_confirmation_required', None) if token else None
            if clarification and tool.permission >= PermissionLevel.ACTION:
                from kk.tools.windows.app_resolver import TargetClarificationRequired
                raise TargetClarificationRequired(clarification['question'])
            evidence = getattr(token, 'voice_evidence', None) if token else None
            if evidence is not None and tool.permission >= PermissionLevel.ACTION and not getattr(evidence, 'committed', False):
                raise PermissionError('Only a committed full-utterance transcript can authorize an action')
            if evidence is not None and tool.permission >= PermissionLevel.ACTION and not evidence.accepted:
                raise PermissionError('Uncertain speech cannot authorize an action; ask the user to repeat')
            from kk.tools.windows.codex_integration import explicit_task_request, explicit_draft_request, submission_requested
            request = getattr(token, 'user_request', '')
            if token and tool.permission >= PermissionLevel.ACTION:
                from kk.core.request_scope import request_scope, validate_action
                scope = getattr(token, 'request_scope', None)
                if scope is None:
                    scope = request_scope(request)
                    token.request_scope = scope
                validate_action(scope, name, arguments)
                if evidence is not None and tool.permission >= PermissionLevel.DANGEROUS:
                    raise PermissionError('Irreversible voice actions require verified human-speech acceptance; use explicit text input for this action')
            from kk.core.literal_intents import literal_intent
            literal = literal_intent(request)
            if literal and not literal['submit']:
                if name == 'run_terminal' or (name == 'press_hotkey' and any(str(k).casefold() in {'enter','return'} for k in arguments.get('keys',[]))) or (name == 'text_entry' and arguments.get('submit')):
                    raise PermissionError('Literal typing never authorizes execution, even when payload mentions send/run')
                if name in {'type_text','text_entry','open_obsidian_draft'} and arguments.get('text') != literal['text']:
                    raise PermissionError('Literal text must remain exact')
            if name == 'text_entry' and arguments.get('submit') and not submission_requested(request):
                raise PermissionError('Typing does not authorize Enter or sending')
            if not explicit_task_request(getattr(token, 'user_request', '')):
                if name == 'run_codex_task' or (name == 'run_terminal' and re.search(r'(?i)\bcodex\b', str(arguments))):
                    raise PermissionError('An explicit user instruction to send a task to Codex is required')
                if name in {'text_entry', 'type_text', 'paste_clipboard', 'press_hotkey', 'set_ui_value', 'click_ui_element', 'click_mouse', 'click_screen_position'}:
                    from kk.tools.windows.native import foreground_window
                    foreground = foreground_window()
                    if 'codex' in (foreground.get('process','') + foreground.get('executable','')).casefold():
                        draft = explicit_draft_request(request)
                        safe_draft = draft and (name in {'type_text','set_ui_value'} or
                            (name == 'text_entry' and not arguments.get('submit')) or
                            (name == 'press_hotkey' and 'enter' not in [str(k).casefold() for k in arguments.get('keys',[])]))
                        if not safe_draft:
                            raise PermissionError('Codex submission requires explicit send/run; drafts permit literal typing only')
            self.policy.authorize(tool.permission, f"{name}({arguments})")
            if token:
                token.check()
            if token and tool.permission >= PermissionLevel.ACTION:
                pending = getattr(token, 'interruption_pending', None)
                while pending is not None and pending.is_set():
                    # Candidate speech pauses only the next side effect. Noise
                    # may release it; a confirmed replacement cancels this task.
                    self.policy.check_stop()
                    token.check()
                    token.event.wait(.05)
                token.check()
            own_ui_scope = (self.ambient.tool_scope(name) if self.ambient and
                tool.permission >= PermissionLevel.ACTION and hasattr(self.ambient, 'tool_scope') else nullcontext())
            with own_ui_scope:
                data = tool.handler(**arguments)
            if name in {'open_application','open_url','browser_control','open_desktop_target','show_explorer_locations'} and isinstance(data,dict) and data.get('verified') and data.get('hwnd'):
                from kk.core.desktop_commands import DesktopContext
                from kk.tools.windows.app_resolver import canonical
                from pathlib import PureWindowsPath
                context=getattr(self,'desktop_context',None) or DesktopContext()
                same_window = context.hwnd == data['hwnd']
                window = data.get('window') or data.get('after') or data
                browser = PureWindowsPath(data.get('browser') or '').stem
                target = data.get('target') or {}
                app = arguments['name'] if name == 'open_application' else (
                    browser or window.get('process') or PureWindowsPath(window.get('executable') or '').stem or context.browser)
                context.app=app; context.hwnd=data['hwnd']; context.updated=monotonic()
                context.process_id=window.get('process_id', context.process_id if same_window else 0)
                context.last_monitor=window.get('monitor_index', context.last_monitor if same_window else None)
                context.last_window=window
                context.last_entity=target.get('name') or app; context.last_action=name
                if canonical(app) in {'browser','firefox','chrome','msedge','brave','opera'}:
                    context.browser=app
                self.desktop_context=context
            result = ToolResult(True, data=data)
            if self.ambient and tool.permission >= PermissionLevel.ACTION:
                self.ambient.record_tool(name, safe_arguments, True)
            self.logger.info("[RESULT] success")
        except Exception as exc:
            from kk.tools.windows.app_resolver import TargetClarificationRequired
            if isinstance(exc, TargetClarificationRequired):
                result = ToolResult(False, data={'status':'needs_clarification', 'question':exc.question},
                                    error=str(exc))
            else:
                result = ToolResult(False, error=f"{type(exc).__name__}: {exc}")
            if self.ambient and tool and tool.permission >= PermissionLevel.ACTION:
                self.ambient.record_tool(name, safe_arguments, False)
            self.logger.exception("[RESULT] error: %s", result.error)
        if token:token.mark('tool_execute_end',tool=name,ok=result.ok,seconds=monotonic()-started)
        self.memory.record_action(
            name,
            safe_arguments,
            self._audit_result(name, result),
            result.ok,
            tool.permission.name,
            int((monotonic() - started) * 1000),
        )
        return result

    @staticmethod
    def _safe_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        sensitive = re.compile(r"(?i)password|passwd|token|secret|api.?key|authorization|otp")
        safe: dict[str, Any] = {}
        for key, value in arguments.items():
            if sensitive.search(key) or key in {'content','command','environment','old','new','task'} or (name in {"text_entry", "type_text", "write_clipboard", "search_and_type_in_app", "set_ui_value"} and key in {"text", "value"}):
                safe[key] = f"[REDACTED length={len(str(value))}]"
            elif name == 'open_obsidian_draft' and key == 'text':
                safe[key] = f"[REDACTED length={len(str(value))}]"
            elif name == "open_url" and key == "url" and isinstance(value, str):
                parts = urlsplit(value)
                safe[key] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            else:
                safe[key] = value
        return safe

    @staticmethod
    def _audit_result(name: str, result: ToolResult) -> dict[str, Any]:
        if not result.ok:
            return {"ok": False, "error": result.error}
        data = result.data
        if name in {'run_codex_task', 'codex_last_result', 'search_and_type_in_app', 'set_ui_value', 'open_obsidian_draft'}:
            return {'ok':True,'data':'[REDACTED Codex output]'}
        if name in {"text_entry", "run_terminal", "read_file", "read_text_file", "read_clipboard", "recall_memories", "see_screen", "get_ui_tree", "observe_context", "get_recent_context", "get_desktop_context"}:
            count = len(data) if isinstance(data, (list, dict, str)) else None
            return {"ok": True, "data": f"[REDACTED payload; size={count}]"}
        if isinstance(data, list) and len(data) > 50:
            return {"ok": True, "data": f"[list with {len(data)} items]"}
        return result.as_dict()

    def permission_for(self, name: str) -> PermissionLevel | None:
        tool = self._tools.get(name)
        return tool.permission if tool else None

    @staticmethod
    def _validate(tool: Tool, arguments: dict[str, Any]) -> None:
        schema = tool.parameters
        required = schema.get("required", [])
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ValueError(f"Отсутствуют аргументы: {', '.join(missing)}")
        properties = schema.get("properties", {})
        unknown = set(arguments) - set(properties)
        if unknown and schema.get("additionalProperties", True) is False:
            raise ValueError(f"Неизвестные аргументы: {', '.join(sorted(unknown))}")
        types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
        for key, value in arguments.items():
            expected = properties.get(key, {}).get("type")
            if expected in types and not isinstance(value, types[expected]):
                raise TypeError(f"{key} должен иметь тип {expected}")
            if expected in {'integer', 'number'} and isinstance(value, bool):
                raise TypeError(f"{key} должен иметь тип {expected}, не boolean")
            spec = properties.get(key, {})
            if 'enum' in spec and value not in spec['enum']:
                raise ValueError(f'{key}: unsupported value {value!r}; allowed: {spec["enum"]}')
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value < spec.get('minimum', float('-inf')) or value > spec.get('maximum', float('inf')):
                    raise ValueError(f'{key}: outside allowed range')
