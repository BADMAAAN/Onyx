"""WebRTC AEC using KK's rendered PCM as the far-end reference.

No microphone muting. Other applications are handled by audio ducking; this
reference cancels KK playback only. Room quality must be calibrated live.
"""
import numpy as np


class EchoCancellation:
    def __init__(self, reference, delay_ms=50):
        from aec_audio_processing import AudioProcessor
        self.ap = AudioProcessor(enable_aec=True, enable_ns=True, enable_agc=False)
        self.ap.set_stream_format(16000, 1)
        self.ap.set_reverse_stream_format(16000, 1)
        self.ap.set_stream_delay(int(delay_ms))
        self.reference = reference

    def process(self, pcm):
        ref = self.reference(len(pcm)//2)
        ref = np.clip(ref*32768, -32768, 32767).astype(np.int16).tobytes()
        output = []
        for offset in range(0, len(pcm), 320):
            self.ap.process_reverse_stream(ref[offset:offset+320])
            output.append(self.ap.process_stream(pcm[offset:offset+320]))
        return b''.join(output)

