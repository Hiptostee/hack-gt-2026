"""Capture, and the one owner of the speaker. One code path for the Pi and dev machines.

Only the owner's persistent output stream writes to the device. Producers
submit speech as prioritized playbacks and never touch the device, so a
blocked network read can never hold the speaker or delay a warning.
"""
import io
import itertools
import sys
import threading
import wave
from collections import deque

try:
    import numpy as np
except ImportError:
    np = None

try:
    import sounddevice as sd
except (ImportError, OSError):  # Let state-machine tests run without PortAudio installed.
    sd = None

CAPTURE_RATE = 16000
OUTPUT_RATE = 22050
BLOCK = 512

# Lower number wins the speaker. Order from the hazard spec §7.
URGENT, FAULT, CAUTION, HELP, INFO, ANSWER = range(6)

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


def resample(samples, from_rate, to_rate):
    if from_rate == to_rate or len(samples) == 0:
        return samples
    count = int(round(len(samples) * to_rate / from_rate))
    positions = np.arange(count) * (from_rate / to_rate)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.int16)


class Playback:
    """One utterance on the speech lane. Producers feed it and close it; the
    owner plays it when it is the most important one waiting."""

    def __init__(self, owner, priority, sequence):
        self.owner = owner
        self.priority = priority
        self.sequence = sequence
        self.chunks = deque()
        self.offset = 0
        self.closed = False
        self.started = False
        self.cancelled = False
        self.done = threading.Event()

    def feed(self, samples, rate=OUTPUT_RATE):
        """Returns False once cancelled, so the producer can stop reading."""
        return self.owner._feed(self, samples, rate)

    def close(self):
        self.owner._close(self)

    def cancel(self):
        self.owner._cancel(self)

    def wait(self, timeout=None):
        """True if it played to the end, False if it was cancelled."""
        self.done.wait(timeout)
        return self.done.is_set() and not self.cancelled


class Audio:
    def __init__(self, capture_rate=CAPTURE_RATE, gain=1.0, output_rate=OUTPUT_RATE,
                 start_output=True):
        self.capture_rate = capture_rate
        self.output_rate = output_rate
        self.gain = gain
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self._out_lock = threading.Lock()
        self._playbacks = []
        self._cues = deque()
        self._sequence = itertools.count()
        self._output = None
        self._output_failed = False
        self._start_output = start_output

    # ---- capture ---------------------------------------------------------

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

    # ---- speech lane -----------------------------------------------------

    def stream(self, priority=ANSWER):
        """Open a playback to feed incrementally. Always close() it."""
        playback = Playback(self, priority, next(self._sequence))
        if not self._ensure_output():
            playback.cancelled = True
            playback.done.set()
            return playback
        with self._out_lock:
            self._playbacks.append(playback)
        return playback

    def submit(self, samples, rate=None, priority=ANSWER):
        """Queue a whole clip without waiting for it."""
        playback = self.stream(priority)
        playback.feed(samples, rate or self.output_rate)
        playback.close()
        return playback

    def play(self, samples, rate=None, priority=ANSWER):
        """Blocking. Returns False if the clip was cancelled."""
        playback = self.submit(samples, rate, priority)
        return playback.wait(len(samples) / (rate or self.output_rate) + 5.0)

    def revoke(self, from_priority):
        """Cancel every playback at from_priority or less important."""
        with self._out_lock:
            for playback in list(self._playbacks):
                if playback.priority >= from_priority:
                    self._cancel_locked(playback)

    def stop(self):
        """Barge-in. Never mutes a warning or fault."""
        self.revoke(HELP)

    def _feed(self, playback, samples, rate):
        if len(samples) and rate != self.output_rate:
            samples = resample(samples, rate, self.output_rate)
        with self._out_lock:
            if playback.cancelled:
                return False
            if len(samples):
                playback.chunks.append(np.asarray(samples, dtype=np.int16))
            return True

    def _close(self, playback):
        with self._out_lock:
            playback.closed = True

    def _cancel(self, playback):
        with self._out_lock:
            self._cancel_locked(playback)

    def _cancel_locked(self, playback):
        playback.cancelled = True
        playback.chunks.clear()
        if playback in self._playbacks:
            self._playbacks.remove(playback)
        playback.done.set()

    def _finish_locked(self, playback):
        self._playbacks.remove(playback)
        playback.done.set()

    # ---- cue lane --------------------------------------------------------

    def cue(self, samples):
        """Mix a short clip over whatever is playing. Cues play in order."""
        done = threading.Event()
        if not self._ensure_output():
            done.set()
            return done
        with self._out_lock:
            self._cues.append([np.asarray(samples, dtype=np.int16), 0, done])
        return done

    def earcon(self, name, wait=True):
        # Earcons ignore barge-in: they are the signal that something happened,
        # including the interruption itself.
        frequencies, duration, volume = EARCONS[name]
        samples = np.concatenate([self.tone(f, duration, volume) for f in frequencies])
        done = self.cue(samples)
        if wait:
            done.wait(len(samples) / self.output_rate + 1.0)

    def tone(self, frequency, duration, volume):
        count = int(self.output_rate * duration)
        t = np.arange(count) / self.output_rate
        wave_form = np.sin(2 * np.pi * frequency * t)
        fade = min(64, count // 4)
        if fade:
            envelope = np.ones(count)
            envelope[:fade] = np.linspace(0, 1, fade)
            envelope[-fade:] = np.linspace(1, 0, fade)
            wave_form *= envelope
        return (wave_form * volume * 32767).astype(np.int16)

    # ---- output ----------------------------------------------------------

    def _ensure_output(self):
        if self._output is not None:
            return True
        if not self._start_output:
            return True  # Tests drive _render() directly.
        if self._output_failed or sd is None:
            return False
        with self._out_lock:
            if self._output is not None:
                return True
            try:
                self._output = sd.OutputStream(
                    samplerate=self.output_rate, channels=1, dtype="int16",
                    blocksize=BLOCK, latency="low", callback=self._callback)
                self._output.start()
            except Exception as error:
                self._output = None
                self._output_failed = True
                print(f"Audio output failed to open: {error!r}", file=sys.stderr)
                return False
        return True

    def _callback(self, outdata, frames, _time, _status):
        outdata[:, 0] = self._render(frames)

    def _current_locked(self):
        if not self._playbacks:
            return None
        return min(self._playbacks, key=lambda p: (p.priority, p.sequence))

    def _render(self, frames):
        mixed = np.zeros(frames, dtype=np.float32)
        with self._out_lock:
            filled = 0
            while filled < frames:
                playback = self._current_locked()
                if playback is None:
                    break
                if not playback.started:
                    # Whatever it displaces is gone for good: no resuming a
                    # half-spoken answer after a warning.
                    for other in list(self._playbacks):
                        if other.started and other.priority > playback.priority:
                            self._cancel_locked(other)
                    playback.started = True
                if not playback.chunks:
                    if playback.closed:
                        self._finish_locked(playback)
                        continue
                    break  # The most important speech is still arriving; hold the lane.
                chunk = playback.chunks[0]
                take = min(frames - filled, len(chunk) - playback.offset)
                mixed[filled:filled + take] = chunk[playback.offset:playback.offset + take]
                filled += take
                playback.offset += take
                if playback.offset >= len(chunk):
                    playback.chunks.popleft()
                    playback.offset = 0
            position = 0
            while position < frames and self._cues:
                cue = self._cues[0]
                samples, offset, done = cue
                take = min(frames - position, len(samples) - offset)
                mixed[position:position + take] += samples[offset:offset + take]
                position += take
                cue[1] = offset + take
                if cue[1] >= len(samples):
                    self._cues.popleft()
                    done.set()
        if self.gain != 1.0:
            mixed *= self.gain
        return np.clip(mixed, -32768, 32767).astype(np.int16)

    def close(self):
        self.revoke(URGENT)
        output, self._output = self._output, None
        if output is not None:
            output.stop()
            output.close()
        with self._out_lock:
            for cue in self._cues:
                cue[2].set()
            self._cues.clear()
