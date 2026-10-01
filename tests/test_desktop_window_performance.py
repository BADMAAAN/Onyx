from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch

from onyx_desktop.client import Client
from onyx_desktop.window_geometry import reachable_geometry
from scripts.summarize_desktop_performance import summarize


class WindowGeometryTests(TestCase):
    def test_disconnected_monitor_restores_to_primary_with_titlebar_reachable(self):
        index, box = reachable_geometry([2200, 250, 1100, 800], [(0, 0, 1920, 1040)], margins=(8, 32, 8, 8))
        self.assertEqual(index, 0)
        self.assertEqual(box, (812, 232, 1100, 800))

    def test_maximum_overlap_wins_when_center_falls_in_monitor_gap(self):
        index, box = reachable_geometry([1600, 0, 760, 600], [(0, 0, 1920, 1040), (2400, 0, 1920, 1040)])
        self.assertEqual(index, 0)
        self.assertEqual(box, (1160, 0, 760, 600))

    def test_negative_monitor_coordinates_are_preserved(self):
        saved = [-1800, 100, 1100, 800]
        self.assertEqual(reachable_geometry(saved, [(-1920, 0, 1920, 1080), (0, 0, 1920, 1040)]), (0, tuple(saved)))

    def test_small_high_dpi_workarea_overrides_normal_minimum(self):
        index, box = reachable_geometry([100, 100, 1500, 1000], [(0, 0, 640, 440)], margins=(8, 30, 8, 8))
        self.assertEqual(box, (8, 30, 624, 402))

    def test_corrupt_or_missing_preferences_do_not_produce_window_coordinates(self):
        for value in (None, [], [True, 0, 900, 700], [0, 0, -3, 700], [2**40, 0, 900, 700]):
            self.assertIsNone(reachable_geometry(value, [(0, 0, 1920, 1080)]))


class ScopedSignalsTests(TestCase):
    def setUp(self):
        self.client = Client('.', 'unused', preview=True)
        self.sidebar, self.selection, self.language = [], [], []
        self.client.sidebarChanged.connect(lambda: self.sidebar.append(1))
        self.client.selectionChanged.connect(lambda: self.selection.append(1))
        self.client.languageChanged.connect(lambda: self.language.append(1))

    def test_streaming_updates_do_not_rebuild_sidebar_or_translate_all_labels(self):
        c = self.client
        c.receive('chats', [dict(conversation_id=c.selected, title='Example')])
        self.sidebar.clear()
        for content in ('A', 'An', 'Answer'):
            c.receive('history', dict(conversation_id=c.selected, has_more=False, requests=[], messages=[
                dict(message_id='response', role='assistant', status='running', content=content, request_id='request')]))
        self.assertEqual(self.sidebar, [])
        self.assertEqual(self.selection, [])
        self.assertEqual(self.language, [])
        self.assertEqual(c.messages.rows[0]['content'], 'Answer')
        meta = c.metaObject()
        for name, expected in [('chats', 'sidebarChanged'), ('projects', 'sidebarChanged'),
                               ('sidebarCollapsed', 'sidebarChanged'), ('archived', 'sidebarChanged'),
                               ('selected', 'selectionChanged'), ('language', 'languageChanged')]:
            self.assertEqual(bytes(meta.property(meta.indexOfProperty(name)).notifySignal().name()).decode(), expected)

    def test_sidebar_changes_and_selection_remain_observable(self):
        c = self.client
        c.receive('projects', [dict(project_id='p', title='Project')])
        c.receive('projects', [dict(project_id='p', title='Project')])
        c.toggleSection('projects')
        c.showArchive(True)
        c.select('another')
        c.preference('language', 'ru')
        self.assertEqual(len(self.sidebar), 3)
        self.assertEqual(len(self.selection), 1)
        self.assertEqual(len(self.language), 1)

    def test_system_font_uses_installed_variable_default_and_preserves_other_custom_font(self):
        with patch('onyx_desktop.preferences.installed_font_families', return_value={'Segoe UI Variable', 'Segoe UI', 'Arial'}):
            c = Client('.', 'unused', preview=True)
        self.assertEqual(c.systemFont, 'Segoe UI Variable')
        c._prefs['fontFamily'] = 'Segoe UI'
        self.assertEqual(c.appearance['fontFamily'], 'Segoe UI Variable')
        c._prefs['fontFamily'] = 'Arial'
        self.assertEqual(c.appearance['fontFamily'], 'Arial')

    def test_voice_notice_changes_without_losing_existing_state(self):
        c = self.client
        state = dict(text_ready=True, voice_state='listening', voice_reason='', voice_notice='Не расслышала. Попробуй ещё раз.')
        c.receive('hello', state)
        self.assertEqual(c.voiceNotice, state['voice_notice'])
        c.receive('hello', dict(state, voice_notice=''))
        self.assertEqual(c.voiceNotice, '')

    def test_settings_and_picker_models_do_not_reset_during_streaming(self):
        c = self.client
        updates = []
        for name in ('modelsChanged', 'modelStateChanged', 'appsChanged', 'memoriesChanged', 'backendSettingsChanged'):
            getattr(c, name).connect(lambda name=name: updates.append(name))
        state = dict(conversation_id=c.selected, model_id='a', rows=[dict(id='a', chat=True)], jobs=[])
        c.receive('models', state)
        c.receive('featureResult', dict(op='apps.list', result=[dict(id='terminal')]))
        c.receive('memories', [dict(key='preference', value='original')])
        c.receive('backendSettings', dict(inputs=['device']))
        updates.clear()
        c.receive('models', deepcopy(state))
        c.receive('featureResult', dict(op='apps.list', result=[dict(id='terminal')]))
        c.receive('memories', [dict(key='preference', value='original')])
        c.receive('backendSettings', dict(inputs=['device']))
        c.receive('history', dict(conversation_id=c.selected, has_more=False, requests=[], messages=[
            dict(message_id='response', role='assistant', status='running', content='part', request_id='request')]))
        self.assertEqual(updates, [])
        c.receive('models', dict(state, jobs=[dict(id='download', state='running')]))
        self.assertEqual(updates, ['modelStateChanged'])
        c.receive('memories', [dict(key='preference', value='saved edit')])
        self.assertEqual(updates[-1], 'memoriesChanged')

    def test_current_model_notification_follows_selected_conversation_only(self):
        c = self.client
        models = []
        c.currentModelChanged.connect(lambda: models.append(c.currentModel))
        c.receive('models', dict(conversation_id='other', model_id='b', rows=[]))
        self.assertEqual(models, [])
        c.receive('models', dict(conversation_id=c.selected, model_id='a', rows=[]))
        c.select('other')
        self.assertEqual(models, ['a', 'b'])

    def test_live_start_is_not_duplicated_and_stale_ack_cannot_reopen_closed_panel(self):
        c = self.client
        c._preview=False;c.command=Mock();c._capabilities=['voice_live']
        starts=[];c.liveStarted.connect(lambda: starts.append(1))
        c.startLive();c.startLive()
        self.assertEqual(c.command.call_count, 1)
        c.setDraft('Text stays a draft while Live starts');c.command.reset_mock();c.send()
        c.command.assert_not_called()
        c.stopLive()
        c.receive('live', dict(started=True,conversation_id=c.selected))
        self.assertEqual(starts, [])

    def test_background_waits_for_scoped_backend_ack_and_failure_does_not_hide_window(self):
        c = self.client
        c._preview=False;c.command=Mock();c._capabilities=['voice_live_background'];c._liveActive=True
        backgrounds=[];c.liveBackgroundStarted.connect(lambda: backgrounds.append(1))
        c.backgroundLive();c.backgroundLive()
        self.assertEqual(c.command.call_count, 1)
        self.assertEqual(backgrounds, [])
        c.receive('liveError', 'connection unavailable')
        self.assertEqual(backgrounds, [])
        c.backgroundLive()
        c.receive('liveBackground', dict(background=True,conversation_id=c.selected))
        self.assertEqual(backgrounds, [1])
        c.setDraft('Text stays editable during Live');c.command.reset_mock();c.send()
        c.command.assert_not_called()


class PerformanceSummaryTests(TestCase):
    def test_visible_idle_is_not_reported_as_streaming_or_geometry_evidence(self):
        context = dict(visible=True, exposed=True, visibility='Windowed', section='Chat', response_active=False)
        result = summarize(dict(contexts=[(0, context)], events=[], frames=[(.016, 16)], gui_intervals=[(.016, 16)]))
        self.assertEqual(result['groups']['visible']['frames']['samples'], 1)
        self.assertEqual(result['groups']['geometry_interaction']['frames']['samples'], 0)
        self.assertEqual(result['groups']['streaming']['frames']['samples'], 0)

    def test_response_geometry_and_settings_have_specific_counts(self):
        context = dict(visible=True, exposed=True, visibility='Windowed', section='Settings', response_active=True)
        result = summarize(dict(contexts=[(0, context)], events=[(.3, 'Resize'), (.31, 'MessageContentChanged')],
                                frames=[(.32, 16)], gui_intervals=[(.32, 80)]))
        for group in ('response_during_geometry', 'settings_during_response', 'streaming'):
            self.assertEqual(result['groups'][group]['frames']['samples'], 1)
        self.assertEqual(result['groups']['response_during_geometry']['gui_intervals']['max_ms'], 80)

    def test_hidden_interval_is_not_classified_as_visible_when_reopened(self):
        context = dict(visible=True, exposed=True, visibility='Windowed')
        hidden = dict(context, visible=False, exposed=False)
        result = summarize(dict(contexts=[(0, context), (.2, hidden), (1.1, context)], events=[],
                                frames=[(1.2, 1100)], gui_intervals=[]))
        self.assertEqual(result['groups']['visible']['frames']['samples'], 0)
        self.assertEqual(result['groups']['all_samples']['frames']['max_ms'], 1100)
