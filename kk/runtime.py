from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path

from kk.config import Config
from kk.core.agent import Agent
from kk.core.orchestrator import Orchestrator
from kk.core.state import StateMachine
from kk.memory.database import MemoryDatabase
from kk.permissions.confirmation import terminal_confirmation
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.performance import PerformanceRecorder
from kk.providers import LMStudioProvider, LLMProvider, OllamaProvider, ProviderError
from kk.tools.memory import register_memory_tools
from kk.tools.registry import ToolRegistry
from kk.tools.windows import register_windows_tools
from kk.vision.screen_capture import ScreenCapture
from kk.vision.ollama_vision import OllamaVisionProvider
from kk.vision.screen_understanding import ScreenUnderstanding
from kk.vision.uia import UIAutomationService
from kk.ambient import AmbientContextManager
from kk.tools.registry import Tool
from kk.tools.windows.factory import obj


@dataclass(slots=True)
class Runtime:
    agent: Agent
    provider: LLMProvider
    model: str
    tools: ToolRegistry
    memory: MemoryDatabase
    policy: PermissionPolicy
    capture: ScreenCapture
    screen: ScreenUnderstanding
    state: StateMachine
    performance: PerformanceRecorder
    context: AmbientContextManager
    build: dict = field(default_factory=dict)


def _providers(config: Config) -> dict[str, LLMProvider]:
    return {
        "ollama": OllamaProvider(
            config.get("providers.ollama.base_url", "http://127.0.0.1:11434"),
            config.get("providers.ollama.timeout_seconds", 180),
            config.get("brain.context_window", 16384),
        ),
        "lmstudio": LMStudioProvider(
            config.get("providers.lmstudio.base_url", "http://127.0.0.1:1234/v1"),
            config.get("providers.lmstudio.timeout_seconds", 180),
        ),
    }


def build_runtime(config: Config, logger: logging.Logger, allow_offline: bool = False) -> Runtime:
    providers = _providers(config)
    providers['ollama'].keep_alive=config.get('brain.keep_alive','30m')
    requested = config.get("brain.provider", "ollama")
    provider = providers.get(requested)
    if provider is None or not provider.is_available():
        provider = next((candidate for candidate in providers.values() if candidate.is_available()), None)
    if provider is None:
        if allow_offline and requested in providers:
            provider=providers[requested]
            logger.warning('[BRAIN] Backend offline; background will reconnect on the next request')
        else:
            raise ProviderError("Не найден доступный локальный LLM backend (Ollama/LM Studio)")
    if not isinstance(provider, OllamaProvider):
        raise ProviderError('Resource safety requires Ollama residency telemetry; unguarded backend fallback is disabled')
    models = provider.list_models() if provider.is_available() else []
    names = {item.get("name") or item.get("model") or item.get("id") for item in models}
    model = config.get("brain.fast", config.get("brain.model", ""))
    if model not in names and not (allow_offline and not names):
        if not names:
            raise ProviderError(f"В {provider.name} нет доступных моделей")
        logger.warning("[BRAIN] Настроенная модель %s отсутствует; выберите установленную модель в Onyx", model)

    data_dir = config.root / "data"
    memory = MemoryDatabase(data_dir / "kk.db")
    autonomy_profile = config.get("permissions.autonomy", config.get("permissions.autonomy_level", "normal")).casefold()
    autonomy = {
        "safe": PermissionLevel.READ,
        "normal": PermissionLevel.ACTION,
        "full": PermissionLevel.DANGEROUS,
        'read': PermissionLevel.READ, 'action': PermissionLevel.ACTION, 'dangerous': PermissionLevel.DANGEROUS,
    }[autonomy_profile]
    policy = PermissionPolicy(autonomy, data_dir / "STOP.KK", terminal_confirmation)
    tools = ToolRegistry(policy, memory, logger)
    from kk.core.decision_policy import DecisionPolicy, OperationalContext
    tools.decision_policy = DecisionPolicy(config.get('preferences', {}),
        config.get('decision.high_confidence', .84), config.get('decision.context_confidence', .65), data_dir/'preferences.json', memory=memory)
    tools.desktop_context = OperationalContext()
    capture = ScreenCapture(config.get("vision.keep_screenshots", False))
    performance = PerformanceRecorder(config.root / "logs" / "performance.jsonl")
    state = StateMachine(memory)
    tools.visual_state = state
    tools.register(Tool('set_orb_visibility', 'Показать или скрыть краевое свечение Onyx до следующей явной команды показать; голос продолжает работать.',
        obj({'visible': {'type': 'boolean'}}, ['visible']), PermissionLevel.ACTION, state.set_orb_visible))
    uia = UIAutomationService()

    vision_backend = providers.get(config.get("vision.provider", "ollama"))
    vision_model = config.get("vision.model", "")
    vision_provider = None
    if (
        vision_model
        and isinstance(vision_backend, OllamaProvider)
        and (allow_offline or (vision_backend.is_available()
        and vision_model in {item.get("name") or item.get("model") for item in vision_backend.list_models()}))
    ):
        vision_provider = OllamaVisionProvider(
            vision_backend,
            vision_model,
            performance,
            model,
            config.get("vision.unload_between_models", True),
        )
    screen = ScreenUnderstanding(
        capture,
        uia,
        vision_provider,
        config.get("vision.max_dimension", 1600),
        performance,
    )
    register_windows_tools(tools, config.get("apps.aliases", {}), screen, uia)
    tools.app_resolver.powershell = config.get('apps.powershell', 'auto')
    register_memory_tools(tools, memory)
    context = AmbientContextManager(data_dir / 'ambient.db', capture, uia,
        config.get('context.retention_seconds', 900), config.get('context.interval_seconds', 2.0),
        config.get('context.screen_interval_seconds', 8.0),
        config.get('context.idle_threshold_seconds', 180), config.get('context.session_gap_seconds', 600),
        excluded_apps=config.get('context.excluded_apps', []),
        persistent_retention_seconds=config.get('context.persistent_retention_seconds', 7*86400))
    screen.context_manager = context
    tools.ambient = context
    if config.get('context.paused',False):context.pause(True)
    tools.register(Tool('get_desktop_context', 'Быстрый snapshot окон обоих мониторов и текущей активности без Vision.',
        obj(), PermissionLevel.READ, context.snapshot))
    tools.register(Tool('get_recent_context', 'Компактная история активности за duration секунд; можно фильтровать по приложению, заголовку и монитору.',
        obj({'duration': {'type':'integer','minimum':1,'maximum':86400}, 'app': {'type':'string'},
             'title': {'type':'string'}, 'monitor_id': {'type':'string'}, 'keywords': {'type':'string'}}), PermissionLevel.READ, context.recent))
    tools.register(Tool('return_to_previous_window', 'Вернуться к предыдущему активному окну до указанного приложения и проверить HWND.',
        obj({'before_app': {'type':'string'}}), PermissionLevel.ACTION, context.return_to_previous))
    tools.register(Tool('find_app_in_context', 'Найти последнее расположение приложения или окна на мониторах по краткой памяти.',
        obj({'query': {'type':'string'}}, ['query']), PermissionLevel.READ, context.find_app))
    tools.register(Tool('pause_ambient_monitoring', 'Полностью поставить Ambient Monitoring на паузу или возобновить.',
        obj({'paused': {'type':'boolean'}}), PermissionLevel.READ, context.pause))

    orchestrator = Orchestrator(
        provider, model, tools, memory, logger, state,
        config.get("agent.max_tool_rounds", 6), config.get("brain.temperature", 0.2), performance,
    )
    orchestrator.max_steps = int(config.get('agent.max_steps',24))
    from kk.core.brain import BrainRouter, ContextBroker
    deep = config.get('brain.deep', model)
    orchestrator.brain_router = BrainRouter(model, deep if deep in names else model)
    orchestrator.context_broker = ContextBroker()
    if isinstance(provider, OllamaProvider):
        from kk.providers.resources import ModelResourceManager
        provider.resource_manager = ModelResourceManager(provider,
            config.get('resources.ram_reserve_gb', 8), config.get('resources.stt_vram_gb', 2),
            config.get('resources.vram_reserve_gb', 1))
        provider.resource_manager.model_profiles = config.get('resources.model_profiles', {})
        provider.resource_manager.on_pressure = lambda value: setattr(state, 'graphics_simplified', bool(value))
        provider.resource_manager.start_idle_watchdog()
        if isinstance(vision_backend, OllamaProvider):
            vision_backend.resource_manager = provider.resource_manager
    orchestrator.verify_desktop_completion = bool(config.get('agent.verify_desktop_completion',True))
    from kk.core.persona import Persona
    orchestrator.persona = Persona(config.get('persona.gender','female'),config.get('persona.style','Естественно, коротко, тепло; лёгкий юмор только к месту.'))
    orchestrator.persist_voice_transcripts = bool(config.get('voice.persist_transcripts',False))
    orchestrator.timeout_seconds = float(config.get('agent.task_timeout_seconds',120))
    return Runtime(Agent(orchestrator), provider, model, tools, memory, policy, capture, screen, state, performance, context)
