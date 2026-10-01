"""Callback capture at a Focusrite endpoint's native sample rate.

The Focusrite Windows drivers are much more reliable at the device's native
sample rate.  PortAudio's DirectSound resampler produced a low-SNR 16 kHz
stream on the real interface, so capture native 30 ms packets and resample
each packet before handing it to WebRTC VAD/Whisper.
"""
from queue import Queue,Empty,Full
from time import monotonic
import math
import numpy as np
from scipy.signal import resample_poly
import sounddevice as sd


class AudioInput:
    def __init__(self,device,frames,sample_rate=16000):
        self.frames=frames
        self.packets=Queue(maxsize=64)
        self.last_packet=monotonic()
        device_info=sd.query_devices(device,'input')
        native_rate=int(round(device_info['default_samplerate']))
        self.device_name = device_info['name']
        self.native_rate = native_rate
        self.channel_rms = []
        self.selected_channel = 0
        self.peak = 0.0
        native_frames=max(1,int(round(frames*native_rate/sample_rate)))
        # "Analogue 1 + 2" is a multi-channel endpoint.  Reading only channel 1
        # silently loses a microphone plugged into input 2.  Capture both and
        # select the channel with the strongest useful signal per packet.
        channels=min(2,int(device_info['max_input_channels']))
        def callback(data,count,timing,status):
            source=np.frombuffer(bytes(data),dtype=np.int16)
            if channels > 1:
                source=source.reshape(-1,channels)
                energy=np.mean(source.astype(np.float32)**2,axis=0)
                self.channel_rms=(np.sqrt(energy)/32768).tolist()
                self.selected_channel=int(np.argmax(energy))
                source=source[:,self.selected_channel]
            else:
                self.channel_rms=[float(np.sqrt(np.mean(source.astype(np.float32)**2))/32768)]
            self.peak=float(np.max(np.abs(source.astype(np.float32)))/32768)
            if native_rate != sample_rate:
                divisor=math.gcd(native_rate,sample_rate)
                converted=resample_poly(source,sample_rate//divisor,native_rate//divisor)
                converted=np.clip(np.rint(converted),-32768,32767).astype(np.int16)
                if converted.size < frames:
                    converted=np.pad(converted,(0,frames-converted.size))
                packet=converted[:frames].tobytes()
            else:
                packet=source[:frames].tobytes()
            try:
                self.packets.put_nowait((packet,bool(status.input_overflow)))
            except Full:
                # Preserve newest audio, bounded memory. Overflow is explicit.
                try:self.packets.get_nowait()
                except Empty:pass
                try:self.packets.put_nowait((packet,True))
                except Full:pass
        self.stream=sd.RawInputStream(device=device,samplerate=native_rate,
            channels=channels,dtype='int16',blocksize=native_frames,callback=callback)

    def __enter__(self):
        self.stream.start()
        return self

    def read(self,frames):
        try:
            packet=self.packets.get(timeout=.2)
            self.last_packet=monotonic()
            return packet
        except Empty:
            if monotonic()-self.last_packet>3:
                raise RuntimeError('Microphone stopped delivering audio; reconnect required')
            return b'',True

    def __exit__(self,*exc):
        self.stream.stop()
        self.stream.close()
