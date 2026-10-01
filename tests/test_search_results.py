"""Fresh UIA search-result evidence, with every desktop action mocked."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from kk.tools.windows.search import AppSearch, search_result_evidence


def row(name, y, kind='TextControl', **fields):
    return {'name': name, 'control_type': kind,
            'rect': dict(left=300, top=y, right=850, bottom=y+35), **fields}


def field(query):
    return row('Поиск', 50, 'EditControl', value=query)


class SearchEvidenceTests(unittest.TestCase):
    def test_exact_query_field_and_result_card_below_sections_verify(self):
        for query, observed in [('Daft Punk', 'Daft Punk'), ('Sølv 42 — Небо?', 'SØLV 42: Небо')]:
            with self.subTest(query=query):
                data = search_result_evidence([field(query), row('Альбомы', 130),
                    row('Исполнители', 200), row(observed, 270, 'HyperlinkControl')], query)
                self.assertEqual(data['verification'], 'query_field_and_result_section')
                self.assertEqual(data['search_outcome'], 'results')
                self.assertEqual(data['result_evidence']['source'], 'uia')
                self.assertEqual(data['result_label'], observed)

    def test_query_only_in_input_or_suggestion_above_sections_is_not_results(self):
        rows = [field('Daft Punk'), row('Daft Punk', 100, 'ListItemControl'), row('Исполнители', 240)]
        self.assertIsNone(search_result_evidence(rows, 'Daft Punk'))
        self.assertIsNone(search_result_evidence([field('Daft Punk'), row('Альбомы', 100)], 'Daft Punk'))

    def test_transliterated_or_unrelated_results_remain_unverified(self):
        self.assertIsNone(search_result_evidence([field('OG Будо'), row('Треки', 130),
            row('Ojobudo', 220, 'HyperlinkControl')], 'OG Будо'))
        self.assertIsNone(search_result_evidence([field('Daft Punk'), row('Альбомы', 130),
            row('Daft Punker', 220)], 'Daft Punk'))

    def test_empty_state_has_its_own_outcome_and_needs_fresh_exact_field(self):
        rows = [field('Несуществующая композиция'), row('Ничего не найдено', 250)]
        evidence = search_result_evidence(rows, 'Несуществующая композиция')
        self.assertEqual(evidence['search_outcome'], 'empty')
        self.assertEqual(evidence['verification'], 'query_field_and_empty_state')
        self.assertIsNone(search_result_evidence(rows, 'Different query'))

    def test_offscreen_password_no_geometry_and_container_text_are_not_evidence(self):
        for match in (row('Daft Punk', 250, offscreen=True), row('Daft Punk', 250, password=True),
                      {'name': 'Daft Punk', 'control_type': 'TextControl'},
                      row('Daft Punk', 250, 'DocumentControl')):
            with self.subTest(match=match):
                self.assertIsNone(search_result_evidence([field('Daft Punk'), row('Треки', 130), match], 'Daft Punk'))

    def test_run_uses_at_most_three_fresh_trees_and_keeps_input_success(self):
        target = field('OG Будо')
        control = Mock()
        launcher = SimpleNamespace(open=Mock(return_value={'hwnd': 7}))
        uia = SimpleNamespace(tree=Mock(return_value=[target, row('Треки', 150), row('Ojobudo', 200)]))
        search = AppSearch(launcher, uia, Mock())
        search.guard = Mock()
        search.uia_focus = Mock(return_value=(control, target))
        search.focused_edit = Mock(return_value=(control, target))
        with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey') as keys, \
                patch('kk.tools.windows.search.sleep'):
            result = search.run('Яндекс Музыка', 'OG Будо', submit=True)
        self.assertEqual(uia.tree.call_count, 3)
        self.assertEqual(result['verification_attempts'], 3)
        self.assertTrue(result['input_verified'])
        self.assertFalse(result['search_verified'])
        self.assertFalse(result['verified'])
        keys.assert_called_once_with(['enter'])

    def test_run_accepts_results_appearing_on_second_observation_without_retyping(self):
        target = field('Daft Punk')
        control = Mock()
        uia = SimpleNamespace(tree=Mock(side_effect=[[target],
            [target, row('Исполнители', 180), row('Daft Punk', 300, 'HyperlinkControl')]]))
        search = AppSearch(SimpleNamespace(open=Mock(return_value={'hwnd': 7})), uia, Mock())
        search.guard = Mock()
        search.uia_focus = Mock(return_value=(control, target))
        search.focused_edit = Mock(return_value=(control, target))
        with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey'), \
                patch('kk.tools.windows.search.sleep'):
            result = search.run('Player', 'Daft Punk', submit=True)
        self.assertTrue(result['search_verified'])
        self.assertEqual(result['verification_attempts'], 2)
        control.GetValuePattern().SetValue.assert_called_once_with('Daft Punk')

    def test_uia_verification_failure_preserves_verified_input_without_retrying_action(self):
        target = field('Other query')
        control = Mock()
        uia = SimpleNamespace(tree=Mock(side_effect=OSError('stale provider')))
        search = AppSearch(SimpleNamespace(open=Mock(return_value={'hwnd': 7})), uia, Mock())
        search.guard = Mock()
        search.uia_focus = Mock(return_value=(control, target))
        search.focused_edit = Mock(return_value=(control, target))
        with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey') as keys, \
                patch('kk.tools.windows.search.sleep'):
            result = search.run('Player', 'Other query', submit=True)
        self.assertTrue(result['input_verified'])
        self.assertFalse(result['search_verified'])
        self.assertEqual(result['verification_errors'], ['OSError']*3)
        keys.assert_called_once_with(['enter'])
