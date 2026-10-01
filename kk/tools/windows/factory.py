from __future__ import annotations

from typing import Any

from kk.permissions.policy import PermissionLevel
from kk.tools.registry import Tool, ToolRegistry
from kk.vision.screen_understanding import ScreenUnderstanding
from kk.vision.uia import UIAutomationService

from .apps import ApplicationLauncher, open_settings
from .audio import set_volume, get_volume
from .browser import open_url
from .clipboard import read_clipboard, write_clipboard
from .files import list_directory, directory_observation, read_text_file, list_downloads
from .keyboard import press_hotkey, type_text
from .mouse import click, drag, scroll
from .native import foreground_window, list_monitors, list_processes, system_info
from .windows import (find_window_across_monitors, focus_window, list_visible_windows, windows_observation,
                      move_window_to_monitor)
from .waits import wait_for_window


def obj(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


def register_windows_tools(registry: ToolRegistry, aliases: dict[str, str], screen: ScreenUnderstanding, uia: UIAutomationService) -> None:
    screen.cancellation_provider = registry.cancellation.get
    from .text_entry import TextEntry
    registry.text_entry = TextEntry(registry, uia)
    registry.register(Tool('text_entry', 'Insert EXACT literal text into the focused editable field, verify read-back. submit=false types only; submit=true presses Enter ONLY when user explicitly requested execution/send. Empty text with submit executes previously verified input.',
        obj({'text':{'type':'string'}, 'submit':{'type':'boolean'}}), PermissionLevel.ACTION, registry.text_entry.enter))
    def discover(names=None):
        if names:
            return {'schemas':[registry._tools[name].schema() for name in names if name in registry._tools]}
        return {'tools':[{'name':tool.name,'description':tool.description} for tool in registry._tools.values()]}
    registry.register(Tool('discover_tools',
        'Discover the complete toolset. Empty names lists all capabilities; pass names to load their callable schemas.',
        obj({'names':{'type':'array','items':{'type':'string'}}}), PermissionLevel.READ, discover))
    from .companion_tools import register_companion_tools
    register_companion_tools(registry)
    from .app_resolver import ApplicationResolver
    resolver = ApplicationResolver(aliases, registry.memory.path.parent/'app_corrections.json')
    launcher = ApplicationLauncher(resolver=resolver)
    registry.app_resolver = resolver
    registry.app_launcher = launcher
    from .desktop_targets import register_desktop_target_tools
    register_desktop_target_tools(registry, resolver, uia)
    from .browser import BrowserController, BrowserTabs
    from .media import MediaController
    registry.browser = BrowserController(registry, uia, MediaController())
    registry.browser_tabs = BrowserTabs()
    registry.register(Tool('list_browser_tabs',
        'Enumerate browser tabs with windowId/tabId/index/title/active and coverage. Policy block or missing adapter means unknown, never a single active-window guess.',
        obj({'scope': {'type':'string', 'enum':['all_windows','current_window','window']},
             'window_id': {'type':'integer','minimum':0}}), PermissionLevel.READ, registry.browser_tabs.list))
    registry.register(Tool('browser_control', 'Browser shortcuts, web/video search, choose first observed YouTube video, verify actual browser playback. Unknown UI returns unverified for OCR/Vision replan.',
        obj({'action':{'type':'string','enum':['new_tab','close_tab','restore_tab','first_tab','back','forward','search','search_video','play_video','play_first_video','verify_playback','resume_video','pause_video']},
             'query':{'type':'string'}}, ['action']), PermissionLevel.ACTION, registry.browser.control))
    from .telegram import TelegramActions
    telegram = TelegramActions(registry, UIAutomationService(max_nodes=600,max_depth=18), screen)
    registry.telegram = telegram
    from .yandex_music import YandexMusicActions
    from .media import MediaController
    music = YandexMusicActions(registry, uia, MediaController(), screen)
    registry.register(Tool('play_latest_added_yandex',
        'Open Yandex Music, select newest saved track by semantic UI, verify actual media playback.',
        obj(), PermissionLevel.ACTION, music.play_latest_added))
    registry.register(Tool('open_first_telegram_chat','Open first chat using the installed client shortcut, then inspect header.',
        obj(),PermissionLevel.ACTION,telegram.first_chat))
    registry.register(Tool('start_voice_recording','Start locked voice recording in the current Telegram chat. NEVER sends.',
        obj(),PermissionLevel.ACTION,telegram.start_recording))
    registry.register(Tool('finish_voice_recording','Cancel recording, or send ONLY on a separate explicit send request.',
        obj({'send':{'type':'boolean'}}),PermissionLevel.ACTION,telegram.finish))
    from kk.core.decision_policy import DecisionPolicy
    decision = getattr(registry, 'decision_policy', None) or DecisionPolicy()
    resolver.decision_policy = decision
    def remember_preference(preference, application):
        resolver.resolve(application)
        return decision.remember(preference, application)
    registry.register(Tool('set_application_preference', 'Save an explicitly requested preferred Telegram client, browser or music app.',
        obj({'preference':{'type':'string','enum':['telegram_client','browser','music_app']},'application':{'type':'string'}},
            ['preference','application']), PermissionLevel.ACTION, remember_preference))
    def launch(name):
        context = getattr(registry, 'desktop_context', None)
        monitor = decision.monitor(list_monitors(), context, foreground_window())
        result = launcher.open(name, registry.check_cancel)
        if monitor and result.get('monitor_index') != monitor:
            placed = move_window_handle_to_monitor(result['hwnd'], monitor)
            result.update(monitor_index=placed['after']['monitor_index'], window=placed['after'])
        return result
    from .window_control import WindowController
    from .mouse import move_mouse
    windows = WindowController(registry)
    hwnd_schema = {'hwnd': {'type':'integer', 'description':'Target HWND; omitted uses recent referenced window'}}
    for operation in ('maximize', 'restore', 'minimize', 'close'):
        registry.register(Tool(operation+'_window', 'Native '+operation+' with HWND read-back verification.',
            obj(hwnd_schema), PermissionLevel.ACTION, lambda hwnd=None, op=operation: windows.change(op, hwnd)))
    registry.register(Tool('get_window_state', 'Read HWND, process, monitor, bounds, maximized/minimized.',
        obj(hwnd_schema), PermissionLevel.READ, windows.state))
    registry.register(Tool('toggle_fullscreen', 'Toggle verified app-specific fullscreen (browser/VLC only).',
        obj(hwnd_schema), PermissionLevel.ACTION, windows.fullscreen))
    registry.register(Tool('set_window_bounds', 'Set physical virtual-desktop bounds and verify.',
        obj(dict(hwnd_schema, **{key:{'type':'integer'} for key in ('x','y','width','height')}), ['x','y','width','height']),
        PermissionLevel.ACTION, lambda **args: windows.change('bounds', **args)))
    registry.register(Tool('move_mouse', 'Move pointer to an observed physical display position.',
        obj({'x':{'type':'integer'},'y':{'type':'integer'}}, ['x','y']), PermissionLevel.ACTION, move_mouse))
    from kk.core.personal_intents import PersonalIntentRegistry
    from kk.skills.games import GameRegistry, GameLauncher
    registry.personal_intents = PersonalIntentRegistry(registry.memory.path.parent/'personal_intents.json')
    registry.games = GameRegistry(registry.memory.path.parent/'games.json')
    games = GameLauncher(registry.games, launcher, registry.check_cancel)
    registry.register(Tool('remember_personal_intent', 'Сохранить явно заданный пользователем alias.',
        obj({'phrase': {'type':'string'}, 'kind': {'type':'string','enum':['app','url','skill','task']},
             'target': {}}, ['phrase','kind','target']), PermissionLevel.ACTION, registry.personal_intents.remember))
    registry.register(Tool('remember_game_launcher', 'Сохранить явное исправление game → launcher.',
        obj({'game': {'type':'string'}, 'launcher': {'type':'string'}}, ['game','launcher']), PermissionLevel.ACTION, registry.games.remember))
    registry.register(Tool('launch_game', 'Запустить известную игру через launcher и проверить окно игры.',
        obj({'game': {'type':'string'}}, ['game']), PermissionLevel.ACTION, games.launch))
    def run_skill(name):
        import json
        row = registry.memory.connection.execute("SELECT steps_json FROM skills WHERE name=? AND status='active'", (name,)).fetchone()
        if not row: raise LookupError('Активный навык не найден')
        results = []
        for step in json.loads(row['steps_json']):
            registry.check_cancel()
            if step['tool'] == 'run_personal_skill': raise ValueError('Recursive skills are not allowed')
            result = registry.execute(step['tool'], step['arguments'])
            if not result.ok: raise RuntimeError(result.error)
            results.append(result.data)
        return dict(verified=bool(results), results=results)
    registry.register(Tool('run_personal_skill', 'Выполнить активный пользовательский навык через registry.',
        obj({'name': {'type':'string'}}, ['name']), PermissionLevel.ACTION, run_skill))
    from .search import AppSearch
    search = AppSearch(launcher, UIAutomationService(max_nodes=600, max_depth=20), screen, registry.check_cancel)
    from .mouse import double_click
    registry.register(Tool('double_click', 'Двойной клик по наблюдаемому элементу.',
        obj({'x':{'type':'integer'}, 'y':{'type':'integer'}, 'button':{'type':'string','enum':['left','right']}}, ['x','y']), PermissionLevel.ACTION, double_click))
    registry.register(Tool('set_ui_value', 'Ввести значение через UIA ValuePattern и проверить его.',
        obj({'name':{'type':'string'}, 'value':{'type':'string'}, 'control_type':{'type':'string'}}, ['name','value']), PermissionLevel.ACTION, uia.set_value))
    registry.register(Tool('set_ui_toggle', 'Set one observed accessible toggle and verify its state.',
        obj({'name':{'type':'string'},'enabled':{'type':'boolean'}},['name','enabled']), PermissionLevel.ACTION, uia.set_toggle))
    from .windows import move_window_handle_to_monitor
    registry.register(Tool('move_window_handle_to_monitor', 'Переместить конкретный HWND и проверить монитор.',
        obj({'hwnd': {'type':'integer'}, 'monitor_index': {'type':'integer','minimum':1}}, ['hwnd','monitor_index']),
        PermissionLevel.ACTION, lambda hwnd, monitor_index: move_window_handle_to_monitor(windows.target(hwnd)['hwnd'], monitor_index)))
    registry.register(Tool('resolve_application', 'Найти установленное приложение без запуска.',
        obj({'name': {'type':'string'}}, ['name']), PermissionLevel.READ, resolver.inspect))
    registry.register(Tool('remember_app_alias', 'Запомнить явно указанное пользователем соответствие имени приложению.',
        obj({'spoken': {'type':'string'}, 'application': {'type':'string'}}, ['spoken','application']), PermissionLevel.ACTION, resolver.remember))
    registry.register(Tool('search_and_type_in_app', 'Открыть поиск приложения и ввести точный текст. submit=false не нажимает Enter.',
        obj({'app': {'type':'string'}, 'text': {'type':'string'}, 'submit': {'type':'boolean'}}, ['app','text']), PermissionLevel.ACTION, search.run))
    see_monitor = screen.see_monitor
    tools = [
        Tool('observe_context', 'Быстро наблюдать окно, процесс, монитор, курсор, UIA и текст без Vision.', obj(), PermissionLevel.READ, screen.fast_context),
        Tool('get_volume', 'Прочитать фактическую системную громкость и mute.', obj(), PermissionLevel.READ, get_volume),
        Tool('open_settings', 'Открыть настройки Windows или страницу Bluetooth.', obj({'page': {'type':'string','enum':['','bluetooth','sound','display']}}), PermissionLevel.ACTION, open_settings),
        Tool("get_running_processes", "Получить список запущенных процессов Windows.", obj(), PermissionLevel.READ, list_processes),
        Tool("get_foreground_window", "Получить активное окно и его приложение.", obj(), PermissionLevel.READ, foreground_window),
        Tool("list_windows", "Observe visible titled windows with source, time and partial coverage; not a list of browser/Explorer tabs.", obj(), PermissionLevel.READ, windows_observation),
        Tool("find_window_across_monitors", "Найти окно по заголовку или процессу на всём виртуальном рабочем столе и вернуть его монитор.", obj({"query": {"type":"string"}}, ["query"]), PermissionLevel.READ, find_window_across_monitors),
        Tool("get_system_info", "Получить базовую информацию о системе.", obj(), PermissionLevel.READ, system_info),
        Tool("get_monitors", "Получить координаты и разрешения всех мониторов.", obj(), PermissionLevel.READ, list_monitors),
        Tool("open_application", "Open/focus an installed app using preferences and context/primary monitor; verify HWND and process.", obj({"name": {"type": "string", "description": "Имя приложения"}}, ["name"]), PermissionLevel.ACTION, launch),
        Tool("focus_window", "Focus HWND or unique title; omitted uses referenced window.", obj(dict(hwnd_schema, title={'type':'string'})), PermissionLevel.ACTION, lambda title='', hwnd=None: focus_window(title) if title and hwnd is None else windows.change('focus', hwnd)),
        Tool("move_window_to_monitor", "Move HWND, unique title, or referenced window to a numbered monitor and verify.", obj(dict(hwnd_schema,title={'type':'string'},monitor_index={'type':'integer','minimum':1}), ['monitor_index']), PermissionLevel.ACTION, lambda monitor_index,title='',hwnd=None: move_window_to_monitor(title,monitor_index) if title and hwnd is None else move_window_handle_to_monitor(windows.target(hwnd)['hwnd'],monitor_index)),
        Tool("type_text", "Ввести обычный текст в текущее активное поле.", obj({"text": {"type": "string"}}, ["text"]), PermissionLevel.ACTION, lambda text:type_text(text,registry.check_cancel)),
        Tool("press_hotkey", "Нажать комбинацию клавиш; в full доступна клавиша Windows.", obj({"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"]), PermissionLevel.ACTION, lambda keys: press_hotkey(keys, allow_windows=registry.policy.autonomy==PermissionLevel.DANGEROUS)),
        Tool("click_mouse", "Нажать кнопку мыши в экранных координатах виртуального рабочего стола.", obj({"x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string", "enum": ["left", "right", "middle"]}}, ["x", "y"]), PermissionLevel.ACTION, click),
        Tool("click_screen_position", "Нажать экранную позицию. Использовать только когда UI Automation не нашёл элемент.", obj({"x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string", "enum": ["left", "right", "middle"]}}, ["x", "y"]), PermissionLevel.ACTION, click),
        Tool("scroll", "Прокрутить интерфейс; положительное значение вверх, отрицательное вниз.", obj({"amount": {"type": "integer"}, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["amount"]), PermissionLevel.ACTION, scroll),
        Tool("drag", "Перетащить мышью между экранными координатами.", obj({"start_x": {"type": "integer"}, "start_y": {"type": "integer"}, "end_x": {"type": "integer"}, "end_y": {"type": "integer"}, "duration_ms": {"type": "integer"}}, ["start_x", "start_y", "end_x", "end_y"]), PermissionLevel.ACTION, drag),
        Tool("set_volume", "Установить системную громкость от 0 до 100 процентов.", obj({"level": {"type": "integer", "minimum": 0, "maximum": 100}}, ["level"]), PermissionLevel.ACTION, set_volume),
        Tool("open_url", "Navigate directly in the named or preferred browser and verify its address through UIA.", obj({"url": {"type": "string"}, "browser": {'type':'string'}, 'monitor_index': {'type':'integer','minimum':1}}, ["url"]), PermissionLevel.ACTION,
             lambda url, browser='', monitor_index=None: open_url(url, resolver, browser, registry.check_cancel, uia,
                 decision.monitor(list_monitors(), getattr(registry,'desktop_context',None), foreground_window(), monitor_index))),
        Tool("list_directory", "Показать метаданные каталога с явным покрытием и ограничением 500 элементов.", obj({"path": {"type": "string"}}, ["path"]), PermissionLevel.READ, directory_observation),
        Tool('list_downloads', 'List actual Windows Downloads known folder, names, sizes and modification times. Does not read document contents; modification time is not download time.', obj(), PermissionLevel.READ, list_downloads),
        Tool("read_text_file", "Прочитать небольшой текстовый файл.", obj({"path": {"type": "string"}}, ["path"]), PermissionLevel.READ, read_text_file),
        Tool("read_clipboard", "Прочитать текст clipboard только по явному запросу пользователя.", obj(), PermissionLevel.READ, read_clipboard),
        Tool("write_clipboard", "Записать текст в clipboard.", obj({"text": {"type": "string"}}, ["text"]), PermissionLevel.ACTION, write_clipboard),
        Tool("get_ui_tree", "Получить доступные элементы активного окна через Windows UI Automation.", obj({"interactive_only": {"type": "boolean"}}), PermissionLevel.READ, lambda interactive_only=True: uia.tree(interactive_only)),
        Tool("find_ui_element", "Найти UI Automation элемент активного окна по имени и типу.", obj({"name": {"type": "string"}, "control_type": {"type": "string"}, "timeout": {"type": "number"}}, ["name"]), PermissionLevel.READ, lambda name, control_type="", timeout=0: uia.find(name, control_type, min(float(timeout), 30))),
        Tool("click_ui_element", "Нажать UI Automation элемент по accessible name; предпочтительнее координат.", obj({"name": {"type": "string"}, "control_type": {"type": "string"}, "timeout": {"type": "number"}}, ["name"]), PermissionLevel.ACTION, lambda name, control_type="", timeout=2: uia.click(name, control_type, min(float(timeout), 30))),
        Tool("wait_for_window", "Ждать появления окна до 30 секунд.", obj({"title": {"type": "string"}, "timeout_seconds": {"type": "number"}}, ["title"]), PermissionLevel.READ, wait_for_window),
        Tool("wait_for_ui_element", "Ждать появления UI Automation элемента до 30 секунд.", obj({"name": {"type": "string"}, "control_type": {"type": "string"}, "timeout": {"type": "number"}}, ["name"]), PermissionLevel.READ, lambda name, control_type="", timeout=10: uia.find(name, control_type, min(float(timeout), 30))),
        Tool("see_screen", "Восприятие UIA → OCR → Vision. Для чтения текста mode=auto; для фото mode=vision. Изображение только в RAM.", obj({"target": {"type": "string", "enum": ["all", "active_monitor", "active_window", "cursor", "diff"]}, "question": {"type": "string"}, 'region': {'type':'array','items':{'type':'integer'},'minItems':4,'maxItems':4}, 'mode': {'type':'string','enum':['auto','ocr','vision']}}), PermissionLevel.READ, screen.see_screen),
        Tool("see_monitor", "Прочитать один монитор по его номеру с явной передачей bounds в UIA/OCR/Vision.", obj({"monitor_index":{"type":"integer","minimum":1},"question":{"type":"string"},"mode":{"type":"string","enum":["auto","ocr","vision"]}}, ["monitor_index"]), PermissionLevel.READ, see_monitor),
    ]
    for tool in tools:
        registry.register(tool)
