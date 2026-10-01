"""Perception regressions. All desktop pixels and inference are mocked."""
import base64
from contextlib import contextmanager
import io
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from kk.core.task import CancellationToken
from kk.providers.base import LLMResponse, ProviderError
from kk.providers.ollama_provider import OllamaProvider
from kk.vision.base import ScreenObservation
from kk.vision.errors import PerceptionError
from kk.vision.ollama_vision import OllamaVisionProvider
from kk.vision.screen_capture import ScreenCapture
from kk.vision.screen_context import ScreenContextManager
from kk.vision.screen_understanding import ScreenUnderstanding


MONITORS = [
    dict(index=1, left=0, top=0, right=1920, bottom=1080, primary=True),
    dict(index=2, left=-1280, top=-100, right=0, bottom=924, primary=False),
]
FOREGROUND = dict(hwnd=42, process_id=71, executable='test.exe', title='Fixture')


def png(color='white'):
    image = Image.new('RGB', (120, 80), color)
    ImageDraw.Draw(image).rectangle((10, 10, 30, 30), fill='red')
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    image.close()
    return buffer.getvalue()


def observation(pixels=None, **kwargs):
    return ScreenObservation(Path(), 'test.exe', 'Fixture', (120, 80), MONITORS[1],
                             (0, 0), image_png=png() if pixels is None else pixels, **kwargs)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        for name, value in [('list_monitors', MONITORS), ('foreground_window', FOREGROUND),
                            ('window_rect', (10, 20, 1010, 720))]:
            p = patch('kk.vision.screen_capture.' + name, return_value=value)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        p = patch('kk.ui.capture_guard.is_own_window', return_value=False)
        p.start(); self.addCleanup(p.stop)
        p = patch('kk.vision.screen_capture.ImageGrab.grab',
                  side_effect=lambda **kw: Image.new('RGB',
                      (kw['bbox'][2]-kw['bbox'][0], kw['bbox'][3]-kw['bbox'][1]), 'white'))
        self.grab = p.start(); self.addCleanup(p.stop)

    def test_second_monitor_negative_coordinates_and_scale_are_preserved(self):
        image, metadata = ScreenCapture().capture_image('all', [-1280, -100, 0, 924], 640)
        self.addCleanup(image.close)
        self.assertEqual(self.grab.call_args.kwargs, {'bbox': (-1280, -100, 0, 924), 'all_screens': True})
        self.assertEqual(metadata['scale'], (.5, .5))
        self.assertEqual(metadata['monitor']['index'], 2)
        self.assertEqual(metadata['coordinate_space'], 'physical_virtual_desktop')
        self.assertEqual(image.size, (640, 512))

    def test_all_contains_two_monitors_without_pretending_it_is_primary(self):
        image, metadata = ScreenCapture().capture_image('all')
        image.close()
        self.assertIsNone(metadata['monitor'])
        self.assertEqual([m['index'] for m in metadata['monitors']], [1, 2])
        self.assertEqual(metadata['bounds'], (-1280, -100, 1920, 1080))

    def test_absent_foreground_uses_primary(self):
        self.foreground_window.return_value = {}
        image, metadata = ScreenCapture().capture_image()
        image.close()
        self.assertEqual(metadata['bounds'], (0, 0, 1920, 1080))

    def test_empty_invalid_outside_regions_never_capture_another_screen(self):
        for region in ([], [0, 0, 0, 3], [0, 0, 3000, 1080], ['0', 0, 10, 10]):
            with self.subTest(region=region), self.assertRaises(PerceptionError):
                ScreenCapture().capture_image(region=region)
        self.grab.assert_not_called()

    def test_black_frame_is_unavailable_not_inferred_content(self):
        self.grab.side_effect = lambda **kw: Image.new('RGB', (40, 30), 'black')
        with self.assertRaises(PerceptionError) as error:
            ScreenCapture().capture_image()
        self.assertEqual((error.exception.stage, error.exception.reason), ('capture', 'blank_or_protected'))

    def test_rgb_conversion_and_window_change_detection(self):
        self.grab.side_effect = lambda **kw: Image.new('RGBA', (40, 30), (80, 90, 100, 255))
        image, _ = ScreenCapture().capture_image()
        self.assertEqual(image.mode, 'RGB')
        image.close()
        self.foreground_window.side_effect = [FOREGROUND, {**FOREGROUND, 'process_id': 900}]
        with self.assertRaisesRegex(PerceptionError, 'target_changed'):
            ScreenCapture().capture_image()

    def test_clean_capture_is_restored_when_backend_raises(self):
        events = []
        @contextmanager
        def guard():
            events.append('hide')
            try:
                yield
            finally:
                events.append('restore')
        self.grab.side_effect = OSError('capture failed')
        with patch('kk.ui.capture_guard.clean_capture', guard), self.assertRaises(PerceptionError):
            ScreenCapture().capture_image()
        self.assertEqual(events, ['hide', 'restore'])

    def test_exclusion_ack_failure_never_reads_contaminated_pixels(self):
        @contextmanager
        def failed_guard():
            raise RuntimeError('capture/exclusion_failed: no acknowledgement')
            yield
        with patch('kk.ui.capture_guard.clean_capture', failed_guard), self.assertRaisesRegex(PerceptionError, 'capture/exclusion_failed'):
            ScreenCapture().capture_image()
        self.grab.assert_not_called()

    def test_each_capture_has_new_id_and_pixels(self):
        self.grab.side_effect = [Image.new('RGB', (10, 10), 'red'), Image.new('RGB', (10, 10), 'blue')]
        first, a = ScreenCapture().capture_image()
        second, b = ScreenCapture().capture_image()
        self.addCleanup(first.close); self.addCleanup(second.close)
        self.assertNotEqual(a['id'], b['id'])
        self.assertGreaterEqual(b['captured_monotonic'], a['captured_monotonic'])
        self.assertNotEqual(first.tobytes(), second.tobytes())


class VisionAdapterTests(unittest.TestCase):
    def test_fixture_maps_friendly_qt_names_using_physical_geometry_and_dpr(self):
        from scripts.vision_pixel_probe import match_screen
        screen = SimpleNamespace(name=lambda: 'Friendly monitor', devicePixelRatio=lambda: 1.25,
            geometry=lambda: SimpleNamespace(x=lambda: -1280, y=lambda: -100,
                width=lambda: 1024, height=lambda: 819))
        monitor = {**MONITORS[1], 'monitor_id': r'\\.\DISPLAY2', 'width': 1280, 'height': 1024}
        self.assertIs(match_screen(monitor, [screen]), screen)

    def test_fixture_truth_is_only_pixels_and_score_requires_all_fields(self):
        from scripts.vision_pixel_probe import create_fixture, score
        pixels, expected = create_fixture()
        with Image.open(io.BytesIO(pixels)) as image:
            self.assertEqual(image.size, (1100, 680))
            self.assertEqual(image.info, {})
        full = expected['code'] + ' ' + ' '.join(expected['colors'] + expected['shapes'])
        self.assertTrue(score(expected, full)['passed'])
        self.assertFalse(score(expected, ' '.join(expected['colors'] + expected['shapes']))['passed'])

    def test_rest_gets_base64_pixels_not_filename_or_data_url(self):
        provider = OllamaProvider('http://unused')
        pixels = png()
        with patch('kk.providers.ollama_provider.request_json', return_value={
                'done': True, 'done_reason': 'stop', 'message': {'content': 'Описание'}}) as request:
            answer = provider.describe_image_bytes('qwen3-vl:8b', 'describe', pixels)
        body = request.call_args.kwargs['body']
        encoded = body['messages'][0]['images'][0]
        self.assertEqual(base64.b64decode(encoded, validate=True), pixels)
        self.assertFalse(encoded.startswith('data:'))
        self.assertEqual(answer, 'Описание')

    def test_invalid_path_string_cannot_be_encoded_as_image_bytes(self):
        with self.assertRaises(ProviderError):
            OllamaProvider('http://unused').describe_image_bytes('x', 'x', 'image.png')

    def test_cancellation_reaches_production_stream_and_cannot_return_stale_answer(self):
        provider = OllamaProvider('http://unused')
        token = CancellationToken()
        provider.chat_stream = Mock(return_value=(LLMResponse('ok'), {}))
        provider.describe_image_bytes('qwen3-vl:8b', 'describe', png(), cancellation=token)
        self.assertIs(provider.chat_stream.call_args.kwargs['cancellation'], token)
        token.cancel('superseded', 'test')
        with self.assertRaises(InterruptedError):
            provider.describe_image_bytes('qwen3-vl:8b', 'describe', png(), cancellation=token)
        self.assertEqual(provider.chat_stream.call_count, 1)

    def test_model_capability_checked_before_inference(self):
        provider = OllamaProvider('http://unused')
        provider.model_capabilities = Mock(return_value=['completion'])
        provider.describe_image_bytes = Mock()
        with self.assertRaisesRegex(PerceptionError, 'model/no_vision_capability'):
            OllamaVisionProvider(provider, 'text-only').describe_screen(observation())
        provider.describe_image_bytes.assert_not_called()

    def test_validated_pixels_and_capabilities_are_reused_without_extra_metadata_requests(self):
        provider = OllamaProvider('http://unused')
        provider.model_capabilities = Mock(return_value=['vision'])
        provider.describe_image_bytes = Mock(return_value='Свежий кадр')
        adapter = OllamaVisionProvider(provider, 'qwen3-vl:8b')
        for _ in range(2):
            adapter.describe_screen(observation())
        self.assertEqual(provider.model_capabilities.call_count, 1)
        self.assertEqual(provider.describe_image_bytes.call_count, 2)
        self.assertIsInstance(provider.describe_image_bytes.call_args.args[2], bytes)

    def test_payload_failure_precedes_model_load(self):
        provider = Mock()
        with self.assertRaisesRegex(PerceptionError, 'payload/invalid_image'):
            OllamaVisionProvider(provider, 'x').describe_screen(observation(b'invalid'))
        provider.describe_image_bytes.assert_not_called()

    def test_inference_and_resource_failures_are_distinguishable(self):
        from kk.providers.resources import ResourcePressure
        for exception, stage in [(OSError('HTTP failure'), 'inference'), (ResourcePressure('low VRAM'), 'resource')]:
            provider = SimpleNamespace(resource_manager=object(), describe_image_bytes=Mock(side_effect=exception))
            with self.subTest(stage=stage), self.assertRaises(PerceptionError) as error:
                OllamaVisionProvider(provider, 'x').describe_screen(observation())
            self.assertEqual(error.exception.stage, stage)

    def test_crop_coordinates_convert_to_physical_negative_desktop(self):
        provider = SimpleNamespace(resource_manager=object(), describe_image_bytes=Mock(
            return_value='{"found":true,"x":60,"y":40}'))
        result = OllamaVisionProvider(provider, 'x').locate_element(
            observation(crop_bounds=(-1280, -100, -1040, 60), scale=(.5, .5)), 'fixture')
        self.assertEqual((result['x'], result['y']), (-1160, -20))


class ProductionPerceptionTests(unittest.TestCase):
    def setUp(self):
        self.capture, self.uia, self.provider = Mock(), Mock(), Mock()
        self.screen = ScreenUnderstanding(self.capture, self.uia, self.provider)
        self.provider.answer_about_screen.return_value = 'На изображении фигура.'
        self.screen.fast_context = lambda: {'foreground': FOREGROUND, 'ui_elements': [{'text': 'Wrong UIA'}]}
        self.screen.ocr = Mock()
        self.screen.ocr.read.return_value = {'seconds': 0, 'results': []}
        def capture(*args):
            return Image.open(io.BytesIO(png())), {
                'foreground': FOREGROUND, 'bounds': (-1280, -100, 0, 924), 'scale': (.5, .5),
                'monitor': MONITORS[1], 'monitors': [MONITORS[1]], 'id': 'pixels',
                'captured_monotonic': monotonic(), 'timestamp': '2026-09-28T00:00:00+00:00'}
        self.capture.capture_image.side_effect = capture
        for name, value in [('current_screen_context', {'foreground': FOREGROUND})]:
            p = patch('kk.vision.screen_context.' + name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(self.screen, '_mouse', return_value=(0, 0))
        p.start(); self.addCleanup(p.stop)

    def test_visual_question_sends_pixels_without_uia_or_ocr_substitution(self):
        result = self.screen.see_screen(question='Что сейчас на экране?', mode='vision')
        seen = self.provider.answer_about_screen.call_args.args[0]
        self.assertEqual(seen.image_png, png())
        self.assertEqual(seen.ui_elements, [])
        self.assertEqual(result['observation_id'], result['observation']['id'])
        self.assertEqual(result['observation']['hwnd'], 42)
        self.assertEqual(result['observation']['process_id'], 71)
        self.assertEqual(result['source'], 'vision')
        self.screen.ocr.read.assert_not_called()

    def test_auto_visual_question_skips_unneeded_ocr_and_explicit_ocr_reads_text(self):
        self.screen.see_screen(question='Что сейчас на экране?')
        self.screen.ocr.read.assert_not_called()
        self.screen.ocr.read.return_value = {'seconds': 0, 'results': [{'text': '40217'}]}
        result = self.screen.see_screen(question='Что сейчас на экране?', mode='ocr')
        self.assertEqual(result['source'], 'ocr')
        self.assertEqual(result['description'], '40217')

    def test_missing_explicit_monitor_is_not_substituted(self):
        with patch('kk.vision.screen_understanding.list_monitors', return_value=MONITORS):
            with self.assertRaisesRegex(PerceptionError, 'monitor_missing'):
                self.screen.see_monitor(9)
            result = self.screen.see_monitor(2, mode='vision')
        self.assertEqual(self.capture.capture_image.call_args.args[1], [-1280, -100, 0, 924])
        self.assertEqual(result['monitor']['index'], 2)

    def test_absent_vision_is_a_failure_not_success_description(self):
        self.screen.provider = None
        with self.assertRaisesRegex(PerceptionError, 'model/unavailable'):
            self.screen.see_screen(mode='vision')

    def test_empty_region_and_expired_diff_do_not_capture_full_screen(self):
        with self.assertRaisesRegex(PerceptionError, 'invalid_region'):
            self.screen.see_screen(region=[])
        self.screen.context_manager = ScreenContextManager(Mock(), Mock())
        self.screen.context_manager.diff_region = (10, 20, 30, 40)
        self.screen.context_manager.diff_observed_at = monotonic() - 60
        with self.assertRaisesRegex(PerceptionError, 'diff_unavailable'):
            self.screen.see_screen(target='diff')
        self.capture.capture_image.assert_not_called()

    def test_generation_cancel_during_vision_discards_returned_description(self):
        token = CancellationToken()
        self.screen.cancellation_provider = lambda: token
        def answer(observation, question):
            self.assertIs(observation.cancellation, token)
            token.cancel('superseded', 'test')
            return 'Must never be returned'
        self.provider.answer_about_screen.side_effect = answer
        with self.assertRaises(InterruptedError):
            self.screen.see_screen(mode='vision')

    def test_uia_from_changed_foreground_is_discarded(self):
        screen = ScreenUnderstanding(self.capture, self.uia, self.provider)
        self.uia.tree.return_value = [{'text': 'Old document'}]
        with patch('kk.vision.screen_context.current_screen_context', side_effect=[
                {'foreground': FOREGROUND}, {'foreground': {**FOREGROUND, 'hwnd': 99}}]), \
                patch.object(screen, '_mouse', return_value=(0, 0)):
            result = screen.fast_context()
        self.assertEqual(result['ui_elements'], [])
        self.assertEqual(result['foreground']['hwnd'], 99)

    def test_registered_handlers_propagate_observation_and_cancellation(self):
        from kk.memory.database import MemoryDatabase
        from kk.permissions.policy import PermissionPolicy, PermissionLevel
        from kk.tools.registry import ToolRegistry
        from kk.tools.windows.factory import register_windows_tools
        with TemporaryDirectory() as folder:
            root = Path(folder)
            memory = MemoryDatabase(root / 'test.db')
            try:
                registry = ToolRegistry(PermissionPolicy(PermissionLevel.READ, root/'STOP'), memory, logging.getLogger('test'))
                register_windows_tools(registry, {}, self.screen, self.uia)
                token = CancellationToken()
                registry.cancellation.set(token)
                result = registry.execute('see_screen', {'mode': 'vision'})
                self.assertTrue(result.ok, result.error)
                self.assertIs(self.provider.answer_about_screen.call_args.args[0].cancellation, token)
                self.assertEqual(result.data['observation_id'], 'pixels')
                with patch('kk.vision.screen_understanding.list_monitors', return_value=MONITORS):
                    monitor = registry.execute('see_monitor', {'monitor_index': 2, 'mode': 'vision'})
                self.assertTrue(monitor.ok, monitor.error)
                self.assertEqual(monitor.data['monitor']['index'], 2)
            finally:
                memory.close()
