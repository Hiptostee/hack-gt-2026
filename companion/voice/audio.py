"""Capture and interruptible playback. One code path for the Pi and dev machines."""
import io
import threading
import wave

import numpy as np
try:
    import sounddevice as sd
except ImportError:  # Let state-machine tests run without PortAudio installed.
    sd = None

CAPTURE_RATE = 16000

# Earcons are generated, not loaded, so they are always available and never wait
# on a disk read or a network call.
EARCONS = {
    "listening": ([660, 880], 0.07, 0.25),
    "thinking": ([520], 0.06, 0.18),
    "working": ([440], 0.05, 0.12),
    "error": ([300, 220], 0.13, 0.28),
    "too_brief": ([200], 0.05, 0.18),
    "stopped": ([380], 0.05, 0.15),
}


class Audio:
    def __init__(self, capture_rate=CAPTURE_RATE, gain=1.0):
        self.capture_rate = capture_rate
        self.gain = gain
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self._interrupt = threading.Event()

    def record_start(self):
        with self._lock:
            self._chunks = []

        def callback(indata, _frames, _time, _status):
            with self._lock:
                self._chunks.append(indata.copy())

        self._stream = sd.InputStream(samplerate=self.capture_rate, channels=1,
                                      dtype="int16", callback=callback)
        self._stream.start()

    def recorded_seconds(self):
        with self._lock:
            frames = sum(len(chunk) for chunk in self._chunks)
        return frames / self.capture_rate

    def record_stop(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return b"", 0.0, 0
        samples = np.concatenate(chunks, axis=0)
        peak = int(np.abs(samples).max()) if len(samples) else 0
        return self._wav(samples), len(samples) / self.capture_rate, peak

    def _wav(self, samples):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.capture_rate)
            handle.writeframes(samples.tobytes())
        return buffer.getvalue()

    def _apply_gain(self, samples):
        if self.gain == 1.0:
            return samples
        scaled = samples.astype(np.float32) * self.gain
        return np.clip(scaled, -32768, 32767).astype(np.int16)

    def play(self, samples, rate):
        self._interrupt.clear()
        block = self._apply_gain(samples).reshape(-1, 1)
        with sd.OutputStream(samplerate=rate, channels=1, dtype="int16") as out:
            for start in range(0, len(block), 1024):
                if self._interrupt.is_set():
                    return False
                out.write(block[start:start + 1024])
        return True

    def play_stream(self, chunks, rate):
        """Play int16 chunks as they arrive. Returns False if interrupted."""
        self._interrupt.clear()
        started = False
        try:
            with sd.OutputStream(samplerate=rate, channels=1, dtype="int16") as out:
                for chunk in chunks:
                    if self._interrupt.is_set():
                        return False
                    if len(chunk) == 0:
                        continue
                    started = True
                    out.write(self._apply_gain(chunk).reshape(-1, 1))
        except sd.PortAudioError:
            return started
        return True

    def stop(self):
        self._interrupt.set()

    def earcon(self, name):
        frequencies, duration, volume = EARCONS[name]
        samples = np.concatenate([self._tone(f, duration, volume) for f in frequencies])
        # Earcons ignore the interrupt flag: they are the signal that something
        # happened, including the interruption itself.
        was_interrupted = self._interrupt.is_set()
        self.play(samples, self.capture_rate)
        if was_interrupted:
            self._interrupt.set()

    def pulsed(self, frequency=880, seconds=15, period=1.0, on=0.3, volume=0.9):
        pulse = self._tone(frequency, on, volume)
        gap = np.zeros(int(self.capture_rate * (period - on)), dtype=np.int16)
        return np.tile(np.concatenate([pulse, gap]), int(seconds / period))

    def _tone(self, frequency, duration, volume):
        count = int(self.capture_rate * duration)
        t = np.arange(count) / self.capture_rate
        wave_form = np.sin(2 * np.pi * frequency * t)
        fade = min(64, count // 4)
        if fade:
            envelope = np.ones(count)
            envelope[:fade] = np.linspace(0, 1, fade)
            envelope[-fade:] = np.linspace(1, 0, fade)
            wave_form *= envelope
        return (wave_form * volume * 32767).astype(np.int16)
