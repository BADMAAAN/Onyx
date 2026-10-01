"""Acceptance must preserve complete source, every final and rejected silence."""
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from scripts.replay_diagnostics import ReplayProbe, assess_replay, sha256


def observation(text, status='COMPLETED'):
    return dict(events=[dict(event='final_audio_submitted', sequence=1),
                        dict(event='stt_final', transcription={'text':text})],
                command_results=[{'status':status}])


class RealSpeechReplayTests(TestCase):
    def test_complete_command_only_ignores_wake_case_and_punctuation(self):
        result=assess_replay('Открой Яндекс Музыку', observation('Оникс, открой Яндекс Музыку.'))
        self.assertEqual(result['acceptance_status'],'PASS')

    def test_completed_tail_never_passes_complete_request(self):
        cases=[('не закрывай VS Code, открой папку Onyx на рабочем столе','На рабочем столе'),
               ('открой Обсидиан и напиши: Onyx, мне нужен ifconfig','ifconfig')]
        for expected,tail in cases:
            with self.subTest(tail=tail):
                result=assess_replay(expected, observation(tail))
                self.assertEqual(result['task_status'],'COMPLETED')
                self.assertEqual(result['acceptance_status'],'UNVERIFIED')

    def test_multiple_final_events_never_use_last_as_whole(self):
        data=observation('На рабочем столе')
        data['events'].insert(0,dict(event='stt_final',transcription={'text':'Оникс, открой папку'}))
        result=assess_replay('открой папку Onyx на рабочем столе',data)
        self.assertEqual(result['acceptance_status'],'UNVERIFIED')
        self.assertIsNone(result['final_transcript'])
        self.assertEqual(result['final_event_count'],2)

    def test_literal_payload_case_is_exact(self):
        text='открой Обсидиан и напиши: Onyx, мне нужен ifconfig'
        result=assess_replay(text,observation('Оникс, '+text.replace('Onyx','onyx')))
        self.assertFalse(result['literal_payload_exact_match'])
        self.assertEqual(result['acceptance_status'],'UNVERIFIED')

    def test_rejected_no_wake_requires_no_nonexistent_speech_completion(self):
        decoder=SimpleNamespace(submit=Mock(),emit=Mock(),busy=Event(),finals=SimpleNamespace(empty=lambda:True),partial=None)
        loop=SimpleNamespace(decoder=decoder,capture=SimpleNamespace(emit=Mock(),turn=SimpleNamespace(active=False)),
            speech=SimpleNamespace(begin=Mock(),done=Event()),dispatch=Mock(),
            agent=SimpleNamespace(orchestrator=SimpleNamespace(current_task=None)))
        probe=ReplayProbe(loop)
        probe.begin_case('rejected')
        self.assertTrue(probe.settled())
        self.assertFalse(loop.speech.done.is_set())

    def test_prepare_script_has_no_ansi_sensitive_unicode_or_voice_switch(self):
        script=(Path(__file__).resolve().parents[1]/'scripts/prepare_real_speech.ps1').read_text(encoding='utf-8')
        script.encode('ascii')
        self.assertIn('-Encoding UTF8',script)
        self.assertIn('$case.source_text',script)
        self.assertNotIn('Zira',script)
        self.assertNotIn('SpeakSsml',script)
        self.assertNotEqual(sha256('Оникс'.encode('utf-8')),sha256('РћРЅРёРєСЃ'.encode('utf-8')))
