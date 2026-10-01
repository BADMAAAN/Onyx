from __future__ import annotations

import ctypes
from ctypes import wintypes
from time import perf_counter
from time import monotonic
from datetime import datetime, timezone
from uuid import uuid4
import logging
from typing import Any

from PIL import Image
import io
from pathlib import Path

from kk.performance import PerformanceRecorder
from kk.tools.windows.native import list_monitors

from .base import ScreenObservation, VisionProvider
from .screen_capture import ScreenCapture
from .uia import UIAutomationService
from .errors import PerceptionError


class ScreenUnderstanding:
    def __init__(self, capture: ScreenCapture, uia: UIAutomationService, provider: VisionProvider | None, max_dimension: int = 1600, performance: PerformanceRecorder | None = None):
        self.capture = capture
        self.uia = uia
        self.provider = provider
        self.max_dimension = max_dimension
        self.performance = performance
        from .ocr import WindowsOCR
        self.ocr = WindowsOCR()
        self.context_manager = None
        self.cancellation_provider = lambda: None

    def fast_context(self):
        from .screen_context import current_screen_context
        started = perf_counter()
        result = current_screen_context()
        result['cursor'] = self._mouse()
        try:
            result['ui_elements'] = self.uia.tree(interactive_only=False)
            after = current_screen_context().get('foreground', {})
            before = result.get('foreground', {})
            if (before.get('hwnd'), before.get('process_id')) != (after.get('hwnd'), after.get('process_id')):
                result['ui_elements'] = []
                result['uia_error'] = 'Foreground changed during UIA observation'
                result['foreground'] = after
        except Exception as exc:
            result['ui_elements'] = []
            result['uia_error'] = f'{type(exc).__name__}: {exc}'
        result['source'] = 'uia'
        result.update(id=uuid4().hex, timestamp=datetime.now(timezone.utc).isoformat(),
                      captured_monotonic=monotonic(), freshness='observed_for_request')
        result['seconds'] = perf_counter() - started
        if self.performance:
            self.performance.record('uia_observation', result['seconds'])
        return result

    @staticmethod
    def _mouse() -> tuple[int, int]:
        point = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def _resize(self, path) -> tuple[int, int]:
        with Image.open(path) as image:
            if max(image.size) > self.max_dimension:
                image.thumbnail((self.max_dimension, self.max_dimension), Image.Resampling.LANCZOS)
                image.save(path, "PNG", optimize=True)
            return image.size

    def observe(self, target: str = "active_window") -> tuple[Any, ScreenObservation]:
        captured = self.capture.capture(target)
        try:
            image_size = self._resize(captured.path)
        except BaseException:
            captured.cleanup()
            raise
        try:
            ui_elements = self.uia.tree(interactive_only=True)
        except Exception as exc:
            ui_elements = [{"uia_error": f"{type(exc).__name__}: {exc}"}]
        foreground = captured.foreground
        metadata = captured.metadata or {}
        monitor = metadata.get('monitor')
        if target != 'active_window':
            ui_elements = []
        mouse = self._mouse()
        observation = ScreenObservation(
            captured.path,
            foreground.get("executable", ""),
            foreground.get("title", ""),
            image_size,
            monitor,
            mouse,
            ui_elements,
            crop_bounds=metadata.get('bounds'),
            scale=(image_size[0]/captured.width, image_size[1]/captured.height),
            hwnd=foreground.get('hwnd', 0), process_id=foreground.get('process_id', 0),
            timestamp=metadata.get('timestamp', datetime.now(timezone.utc).isoformat()),
            captured_monotonic=metadata.get('captured_monotonic', monotonic()),
        )
        return captured, observation

    def see_monitor(self, monitor_index: int, question='Что находится на этом мониторе?', mode='auto'):
        monitor = next((item for item in list_monitors() if item['index'] == int(monitor_index)), None)
        if monitor is None:
            raise PerceptionError('capture', 'monitor_missing', f'Монитор {monitor_index} не найден')
        region = [monitor[key] for key in ('left', 'top', 'right', 'bottom')]
        result = self.see_screen('all', question, region, mode)
        result['monitor'] = monitor
        return result

    def see_screen(self, target: str = "active_window", question: str = "Что сейчас на экране?", region=None, mode='auto') -> dict[str, Any]:
        started = perf_counter()
        if mode not in {'auto', 'ocr', 'vision'}:
            raise PerceptionError('payload', 'invalid_mode', 'Режим зрения должен быть auto, ocr или vision')
        if target not in {'active_window', 'active_monitor', 'all', 'cursor', 'diff'}:
            raise PerceptionError('capture', 'invalid_target', f'Неизвестная цель захвата: {target}')
        if region is not None and (not isinstance(region, (list, tuple)) or len(region) != 4):
            raise PerceptionError('capture', 'invalid_region', 'region должен содержать четыре координаты; для полного кадра не передавайте region')
        if target == 'diff':
            region = region if region is not None else (self.context_manager.recent_diff_region() if self.context_manager else None)
            if region is None:
                raise PerceptionError('capture', 'diff_unavailable', 'Свежая область изменения ещё не обнаружена')
            target = 'active_window'
        token = self.cancellation_provider()
        if token:
            token.check()
        q = question.casefold()
        semantic = any(word in q for word in ('изображен', 'изображён', 'фотограф', 'картин', 'видео', 'на экране', 'визуальн', 'монитор'))
        if mode == 'vision' or semantic:
            from .screen_context import current_screen_context
            context = {**current_screen_context(), 'cursor': self._mouse(), 'ui_elements': []}
        else:
            context = self.fast_context()
        # Foreground UIA belongs to its window, never to an unrelated monitor crop.
        if region is not None or target != 'active_window':
            context['ui_elements'] = []
        if mode == 'auto' and not semantic:
            elements = context['ui_elements']
            text = '\n'.join(dict.fromkeys(str(e.get('text') or e.get('value') or e.get('name') or '')
                for e in elements if not e.get('password') and not e.get('offscreen'))).strip()
            # Reading an editor requires its document/value, not just menu labels.
            reading = any(w in q for w in ('написан', 'прочитай', 'текст', 'содержим'))
            document_text = any(e.get('text') or e.get('value') for e in elements if not e.get('password'))
            if text and (not reading or document_text):
                if reading:
                    text = '\n'.join(dict.fromkeys(str(e.get('text') or e.get('value')) for e in elements
                        if not e.get('password') and not e.get('offscreen') and (e.get('text') or e.get('value'))))
                return self._result(context, {}, 'uia', text[:12000], token)
        image = None
        try:
            if token:
                token.check()
            try:
                capture_started = perf_counter()
                image, metadata = self.capture.capture_image(target, region, self.max_dimension)
                self._trace(token, 'capture_complete', seconds=perf_counter()-capture_started)
            except PerceptionError:
                raise
            except Exception as exc:
                raise PerceptionError('capture', 'backend_failed', 'Не удалось получить кадр экрана') from exc
            captured_fg = metadata.get('foreground', {})
            previous_fg = context.get('foreground', {})
            if captured_fg:
                if (captured_fg.get('hwnd'), captured_fg.get('process_id')) != (previous_fg.get('hwnd'), previous_fg.get('process_id')):
                    context['ui_elements'] = []
                context['foreground'] = captured_fg
            if token:
                token.check()
            if mode == 'ocr' or (mode == 'auto' and not semantic):
                try:
                    result = self.ocr.read(image)
                    if self.performance:
                        self.performance.record('ocr_latency', result['seconds'])
                    if any(r['text'].strip() for r in result['results']):
                        return self._result({**context, **result}, metadata, 'ocr',
                                            '\n'.join(r['text'] for r in result['results']), token)
                except Exception as exc:
                    context['ocr_error'] = f'{type(exc).__name__}: {exc}'
                if mode == 'ocr':
                    raise PerceptionError('inference', 'ocr_empty', 'OCR не смог прочитать текст')
            if not self.provider:
                raise PerceptionError('model', 'unavailable', 'Модель зрения не настроена')
            buffer = io.BytesIO()
            image.save(buffer, 'PNG')
            metadata['image_format'] = 'PNG'
            metadata['image_bytes'] = buffer.tell()
            fg = metadata.get('foreground', {})
            observation = ScreenObservation(Path(), fg.get('executable',''), fg.get('title',''), image.size,
                metadata.get('monitor'), context.get('cursor', (0, 0)), context['ui_elements'], buffer.getvalue(),
                metadata.get('bounds'), metadata.get('scale', (1, 1)),
                id=metadata.get('id', uuid4().hex), timestamp=metadata.get('timestamp', datetime.now(timezone.utc).isoformat()),
                captured_monotonic=metadata.get('captured_monotonic', monotonic()),
                hwnd=fg.get('hwnd', 0), process_id=fg.get('process_id', 0), cancellation=token)
            description = self.provider.answer_about_screen(observation, question)
            if token:
                token.check()
            return {**self._result(context, metadata, 'vision', description, token),
                    'observation': observation.metadata(), 'resolution': image.size, 'screenshot_temporary': True}
        except PerceptionError as exc:
            self._trace(token, 'perception_failed', stage=exc.stage, reason=exc.reason, target=target)
            logging.getLogger('kk').exception('[PERCEPTION] stage=%s reason=%s', exc.stage, exc.reason)
            raise
        finally:
            if image is not None:
                image.close()
            if self.performance:
                self.performance.record("screen_understanding_total", perf_counter() - started, target=target)

    @staticmethod
    def _trace(token, event, **fields):
        ids = {key: getattr(token, key, None) for key in ('session_id', 'turn_id', 'response_id')}
        ids['task_id'] = getattr(token, 'trace_id', getattr(token, 'generation_id', None))
        logging.getLogger('kk').info('[PERCEPTION] %s %s', event, {**ids, **fields})
        if token:
            token.mark(event, **fields)

    def _result(self, context, metadata, source, description, token):
        if token:
            token.check()
        observed_at = metadata.get('captured_monotonic', context.get('captured_monotonic', monotonic()))
        age = max(0.0, monotonic() - observed_at)
        observation_id = metadata.get('id', context.get('id', uuid4().hex))
        self._trace(token, 'perception_complete', observation_id=observation_id, source=source,
                    age_seconds=round(age, 3), bounds=metadata.get('bounds'),
                    monitor_indices=[m.get('index') for m in metadata.get('monitors', [])])
        foreground = context.get('foreground', {})
        timestamp = metadata.get('timestamp', context.get('timestamp', datetime.now(timezone.utc).isoformat()))
        fresh = 'captured_for_request' if metadata else 'observed_for_request'
        observation = {'id': observation_id, 'timestamp': timestamp, 'source': source,
            'application': foreground.get('executable', ''), 'process_id': foreground.get('process_id', 0),
            'hwnd': foreground.get('hwnd', 0), 'monitor': metadata.get('monitor'),
            'bounds': metadata.get('bounds', foreground.get('bounds')),
            'ui_elements': context.get('ui_elements', []), 'freshness': fresh, 'age_seconds': age}
        return {**context, 'id': observation_id, 'observation_id': observation_id, 'observation': observation,
            'timestamp': timestamp,
            'source': source, 'description': description, 'vision_used': source == 'vision',
            'crop': metadata, 'monitor': metadata.get('monitor'), 'age_seconds': age,
            'freshness': fresh}
