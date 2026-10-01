from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

from kk.voice.tts_silero import SileroTTS
from kk.voice.tts_sapi import english_sentence


class EnglishDeliveryTests(TestCase):
    def test_acknowledgement_keeps_conversation_open(self):
        from kk.voice.conversation import ConversationSession
        session=ConversationSession(); session.touch()
        for text in ('да','понял','поняла','понял тебя','yes','okay'):
            self.assertFalse(session.closing(text))
        self.assertTrue(session.closing('до связи'))

    def test_whole_english_sentence_is_not_transliterated(self):
        tts=SileroTTS(Path('unused'), '', 'baya')
        tts.english=Mock(actual_voice='Microsoft Zira Desktop')
        text='A Python function is a reusable block of code.'
        prepared=tts.prepare_text(text)
        self.assertEqual(prepared,text)
        tts.synthesize_prepared(prepared)
        tts.english.synthesize.assert_called_once_with(text)
        self.assertEqual(tts.actual_voice,'Microsoft Zira Desktop')
        self.assertIsNone(tts._model)

    def test_russian_sentence_with_names_stays_russian(self):
        for text in ('Открыла VS Code и Firefox.', 'RAM и VRAM хранят данные.', 'YouTube.'):
            self.assertFalse(english_sentence(text))

    def test_english_with_names_stays_english(self):
        for text in ('Firefox and VS Code are open.', 'Tea is made from leaves of the tea plant.', 'Opened Notepad.', 'Typed.'):
            self.assertTrue(english_sentence(text))

    def test_action_language_rendering_preserves_target_and_literal_data(self):
        from kk.core.command_language import render_action_result
        self.assertEqual(render_action_result('Открыла VS Code.', 'Open VS Code'), 'Opened VS Code.')
        self.assertEqual(render_action_result('Написала.', 'Type: не закрывай VS Code'), 'Typed.')
        self.assertEqual(render_action_result('Открыла мне Obsidian.', 'открой мне Obsidian'), 'Открыла мне Obsidian.')
