"""Neutral acoustic retry must preserve command scope and literal payload."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

import numpy as np

from kk.voice.recognition import recognize_final
from kk.voice.stt_faster_whisper import FasterWhisperSTT, Transcription


def heard(text, confidence=1):
    return Transcription(text, 'ru', 1, 2, .1, text, text,
                         confidence=confidence, intent_confidence=confidence)


class SpeechRetryTests(TestCase):
    def test_observed_mixed_spellings_resolve_only_as_application_targets(self):
        from kk.core.desktop_commands import parse_plan
        from kk.core.literal_intents import literal_intent
        from kk.tools.windows.app_resolver import canonical
        for name in ('VS код', 'VS-код'):
            with self.subTest(name=name):
                self.assertEqual(canonical(name), 'vscode')
                self.assertEqual(parse_plan(name + ' закрой'),
                                 [{'kind': 'window', 'operation': 'close', 'app': 'VS Code'}])
                self.assertEqual(literal_intent('В Блокноте напиши ' + name)['text'], name)
                self.assertEqual(parse_plan('открой папку «' + name + '»')[0]['name'], name)
                stt = SimpleNamespace(decode_full=Mock(side_effect=[
                    heard('Оникс, VS Code, закрой', .2), heard('Оникс, ' + name + ' закрой.', .8)]))
                self.assertTrue(recognize_final(stt, object()).accepted)

    def test_retry_removes_dictionary_bias_without_changing_first_decode(self):
        stt = FasterWhisperSTT()
        stt.hotwords = ['VS Code', 'Obsidian']
        stt.transcribe = Mock(side_effect=[heard('Оникс, VS Code, закрой', .3),
                                          heard('Onyx VS Code закрой.', .8)])
        audio = np.zeros(16000, dtype=np.float32)
        result = stt.transcribe_final(audio)
        self.assertTrue(result.accepted and result.committed)
        self.assertEqual(result.text, 'Onyx VS Code закрой.')
        self.assertEqual(stt.transcribe.call_count, 2)
        first, retry = stt.transcribe.call_args_list
        self.assertNotIn('hotwords', first.kwargs)
        self.assertIn('hotwords', retry.kwargs)
        self.assertIsNone(retry.kwargs['hotwords'])
        self.assertIs(first.args[0], audio)
        self.assertIs(retry.args[0], audio)
        self.assertEqual(stt.hotwords, ['VS Code', 'Obsidian'])

    def test_unparsed_retry_cannot_drop_negation_or_change_its_scope(self):
        pairs = [
            ('не закрывай VS Code, открой папку', 'закрывай VS Code, открой папку'),
            ('не закрывай VS Code', 'закрой VS Code'),
            ('не закрывай VS Code, открой Obsidian', 'закрой VS Code, не открывай Obsidian'),
            ('расскажи про YouTube', 'открой YouTube'),
        ]
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                stt = SimpleNamespace(decode_full=Mock(side_effect=[heard(first, .3), heard(second)]))
                result = recognize_final(stt, object())
                self.assertFalse(result.accepted)
                self.assertIn('passes_disagree', result.rejection_reason)

    def test_same_unparsed_request_can_gain_confidence(self):
        text = 'Не закрывай VS Code, открой папку'
        stt = SimpleNamespace(decode_full=Mock(side_effect=[heard(text, .3), heard(text + '.')]))
        self.assertTrue(recognize_final(stt, object()).accepted)

    def test_literal_retry_does_not_replace_case_or_punctuation(self):
        for second in ('В Блокноте напиши youtube', 'В Блокноте напиши YouTube!'):
            with self.subTest(second=second):
                stt = SimpleNamespace(decode_full=Mock(side_effect=[
                    heard('В Блокноте напиши YouTube', .3), heard(second)]))
                result = recognize_final(stt, object())
                self.assertFalse(result.accepted)
                self.assertIn('passes_disagree', result.rejection_reason)

    def test_retry_still_rejects_low_confidence(self):
        stt = SimpleNamespace(decode_full=Mock(side_effect=[
            heard('VS Code закрой', .2), heard('VS Code закрой', .3)]))
        self.assertFalse(recognize_final(stt, object()).accepted)
