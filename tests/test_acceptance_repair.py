"""Acceptance must validate the audio lifecycle separately from generated text."""
import unittest
from scripts.validate_repair import validate_report


class AcceptanceRepairTests(unittest.TestCase):
    def report(self):
        return {'mode': 'replay_interrupt', 'rows': [{
            'status': 'COMPLETED', 'answer': 'Four.', 'spoken_text': 'Four.',
            'speech_trace': [
                {'event': 'tts_synthesis_end', 'chunk_id': 1, 'generated_frames': 100},
                {'event': 'audio_device_start', 'chunk_id': 1},
                {'event': 'audio_submitted', 'chunk_id': 1, 'submitted_frames': 100},
                {'event': 'audio_playback_complete', 'chunk_id': 1, 'completed_frames': 100},
                {'event': 'speech_complete'},
            ],
        }], 'interruption': {
            'old_generation_cancelled': False, 'old_generation_completed': True,
            'old_speech_cancelled': True, 'old_speech_cancel_reason': 'confirmed_barge_in',
            'stale_audio_after_cancel': False, 'stale_audio_after_new_turn': False,
        }}

    def test_finished_text_still_requires_real_speech_cancellation(self):
        report = self.report()
        self.assertTrue(validate_report(report)['passed'])
        report['interruption']['old_speech_cancelled'] = False
        self.assertFalse(validate_report(report)['passed'])

    def test_running_old_computation_and_stale_audio_are_failures(self):
        for change in [
            {'old_generation_completed': False},
            {'stale_audio_after_cancel': True},
            {'stale_audio_after_new_turn': True},
            {'old_speech_cancel_reason': 'shutdown'},
        ]:
            with self.subTest(change=change):
                report = self.report()
                report['interruption'].update(change)
                self.assertFalse(validate_report(report)['passed'])

    def test_new_answer_must_drain_all_generated_audio(self):
        report = self.report()
        report['rows'][0]['speech_trace'][3]['completed_frames'] = 99
        self.assertFalse(validate_report(report)['passed'])

    def test_empty_run_is_not_success(self):
        report = self.report()
        report['rows'] = []
        self.assertFalse(validate_report(report)['passed'])
