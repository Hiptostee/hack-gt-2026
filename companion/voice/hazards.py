"""Local hazard warnings. Rendered once at startup, played offline, first on the speaker.

The current /hazard_warning message carries no payload the companion parses,
so every warning uses the generic phrase. The JSON contract in
ros_ws/src/hazard_warnings/specs.md §6 replaces this when the detector exists.
"""
import sys

import numpy as np

from companion.voice.audio import URGENT

PHRASE = "Obstacle ahead."
# Same event at most every 2 s for urgent warnings (hazard spec §6).
REPEAT_AFTER = 2.0
# Two short high beeps: nothing else the device plays sits this high.
TONE = [(1320, 0.08), (0, 0.04), (1320, 0.08), (0, 0.06)]


class HazardVoice:
    def __init__(self, audio, speech):
        self.audio = audio
        parts = [audio.tone(f, d, 0.7) if f else np.zeros(int(audio.output_rate * d), np.int16)
                 for f, d in TONE]
        phrase = speech.render_local(PHRASE)
        self.phrase_ready = phrase is not None
        if not self.phrase_ready:
            print("Hazard phrase unavailable: warnings are tone only.", file=sys.stderr)
        self.clip = np.concatenate(parts + ([phrase] if self.phrase_ready else []))
        self.last_at = None
        self.current = None

    def due(self, at):
        return self.last_at is None or at - self.last_at >= REPEAT_AFTER

    def warn(self, at):
        """Non-blocking. Replaces any warning still waiting to be heard."""
        self.last_at = at
        if self.current is not None:
            self.current.cancel()
        self.current = self.audio.submit(self.clip, priority=URGENT)
