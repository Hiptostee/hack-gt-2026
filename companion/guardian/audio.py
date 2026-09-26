"""Guardian's microphone and speaker path (spec §2, §4).

Push-to-talk: the agent hears real audio only while the button is held past the
hold threshold, and digital silence otherwise, including whenever the device is
playing a local sound. Agent speech goes to the companion's audio owner at the
answer tier, so warnings and help always cut it off.
"""
import sys
import threading
import time

import numpy as np

from companion.voice.audio import ANSWER

try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None

RATE = 16000
CHUNK = 4000  # 250 ms: the SDK's recommended input chunk.


class GuardianAudio:
    """Implements the ElevenLabs AudioInterface methods: start, stop, output, interrupt."""

    def __init__(self, audio, on_started=None, open_mic=True):
        self.audio = audio
        self.on_started = on_started
        self.open_mic = open_mic
        self.lock = threading.Lock()
        self.send = None
        self.stream = None
        self.enabled = False      # False until the cancel window ends.
        self.pressing = False     # Button down: agent speech is muted.
        self.talking = False      # Held past the threshold: mic is live.
        self.turn = False         # This press became a user turn.
        self.pending = []         # Captured before the threshold; sent if it becomes talk.
        self.drop_output = False  # After a local stop, until the user's next turn.
        self.held_output = []     # Agent audio that arrived before the session was enabled.
        self.playback = None
        self.last_output_at = None
        self.sent_seconds = 0.0   # Real (non-silent) audio sent, for tests and logs.

    # ---- AudioInterface --------------------------------------------------

    def start(self, input_callback):
        self.send = input_callback
        if self.open_mic:
            if sd is None:
                print("Guardian: sounddevice unavailable; no microphone.", file=sys.stderr)
                return  # Never reports started, so the session gives up as unavailable.
            try:
                self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                                blocksize=CHUNK, callback=self._capture)
                self.stream.start()
            except Exception as error:
                print(f"Guardian: microphone failed to open ({error!r})", file=sys.stderr)
                self.stream = None
                return
        if self.on_started:
            self.on_started()

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.send = None
        self._cancel_playback()

    def output(self, audio_bytes):
        samples = np.frombuffer(audio_bytes, dtype="<i2")
        with self.lock:
            if not self.enabled:
                self.held_output.append(samples)
                return
            if self.pressing or self.drop_output:
                return
            playback = self.playback
            if playback is None or playback.cancelled:
                playback = self.playback = self.audio.stream(ANSWER)
            self.last_output_at = time.monotonic()
        playback.feed(samples, RATE)

    def interrupt(self):
        """The agent was interrupted server-side: drop what it was saying."""
        self._cancel_playback()

    # ---- controller hooks ------------------------------------------------

    def enable(self):
        """End of the cancel window: play the held greeting, allow the mic."""
        with self.lock:
            self.enabled = True
            held, self.held_output = self.held_output, []
        for samples in held:
            self.output(samples.tobytes())

    def press(self):
        """Any press stops the agent at once; a tap is a stop."""
        with self.lock:
            self.pressing = True
            self.talking = False
            self.turn = False
            self.pending = []
        self._cancel_playback()

    def talk(self):
        """Held past the threshold: this press is a user turn."""
        with self.lock:
            if self.pressing:
                self.talking = True
                self.turn = True

    def end_talk(self):
        """Talk cap reached while still held: stop sending, keep it a turn."""
        with self.lock:
            self.talking = False

    def release(self):
        """Returns True if the press was a user turn."""
        with self.lock:
            turn = self.turn
            self.pressing = False
            self.talking = False
            self.turn = False
            self.pending = []
            # A turn invites a reply; a tap keeps the agent quiet until the next turn.
            self.drop_output = not turn
        return turn

    def mute_until_turn(self):
        with self.lock:
            self.drop_output = True
        self._cancel_playback()

    def _cancel_playback(self):
        with self.lock:
            playback, self.playback = self.playback, None
        if playback is not None:
            playback.cancel()

    def _capture(self, indata, _frames, _time, _status):
        self.capture(bytes(indata))

    def capture(self, data):
        """One mic chunk in; what the agent receives out."""
        silence = bytes(len(data))
        with self.lock:
            send = self.send
            live = self.enabled and not self.audio.local_sound_playing()
            if self.pressing and not self.turn:
                self.pending.append(data)
                chunks = [silence]
            elif self.talking and live:
                chunks, self.pending = self.pending + [data], []
                self.sent_seconds += sum(len(c) for c in chunks) / (2 * RATE)
            else:
                chunks = [silence]
        if send is not None:
            for chunk in chunks:
                send(chunk)
