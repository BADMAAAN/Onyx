import unittest

from kk.voice.stt_faster_whisper import Transcription
from kk.voice.wakeword_temporary import TemporaryWakeWord


class _FakeSTT:
    def __init__(self):
        self.cancelled = False

    def listen(self, **_kwargs):
        return Transcription("Кей Кей", "ru", 1.0, 0.25, 0.01)

    def cancel(self):
        self.cancelled = True


class WakeWordTests(unittest.TestCase):
    def test_temporary_detector_fires(self):
        stt = _FakeSTT()
        wakeword = TemporaryWakeWord(stt)
        heard = []

        def on_wake(text):
            heard.append(text)
            wakeword.stop()

        wakeword.run(on_wake)
        self.assertEqual(heard, ["Кей Кей"])
        self.assertTrue(stt.cancelled)


if __name__ == "__main__":
    unittest.main()
