"""Local hazard warnings. Rendered once at startup, played offline, first on the speaker.

ROS snapshots are validated by HazardState before reaching this phrase bank.
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
        self.bank = {}
        for severity in ("urgent", "caution"):
            for band in ("head", "torso"):
                for direction in ("left", "center", "right"):
                    text = "Head-height obstacle ahead" if band == "head" else "Obstacle ahead"
                    if direction != "center":
                        text += ", " + direction
                    if severity == "urgent":
                        text = "Stop. " + text
                    clip = speech.render_local(text + ".")
                    self.phrase_ready = self.phrase_ready and clip is not None
                    # Short urgent onset; never wait for cloud synthesis.
                    prefix = audio.tone(1320, 0.06, 0.7) if severity == "urgent" else np.zeros(0, np.int16)
                    self.bank[f"{severity}:{band}:{direction}"] = np.concatenate(
                        [prefix, clip if clip is not None else self.clip])
        for key, text in {"unavailable": "Hazard sensing unavailable. Use your cane.",
                          "ready": "Hazard warnings ready. Floor hazards are not monitored.",
                          "restored": "Hazard sensing restored."}.items():
            clip = speech.render_local(text)
            self.phrase_ready = self.phrase_ready and clip is not None
            self.bank[key] = clip if clip is not None else self.clip
        self.bank["alive"] = audio.tone(440, 0.04, 0.08)

    def announce(self, alert, now):
        """At most one pending alert; never replace an ongoing higher priority warning."""
        if now >= alert.expires_at:
            return False
        if self.current is not None and not self.current.done.is_set():
            if self.current.priority <= alert.priority:
                return False
            self.current.cancel()
        self.audio.revoke(alert.priority)
        self.current = self.audio.submit(self.bank[alert.phrase], priority=alert.priority,
                                         expires_at=alert.expires_at)
        return not self.current.cancelled

    def due(self, at):
        return self.last_at is None or at - self.last_at >= REPEAT_AFTER

    def warn(self, at):
        """Non-blocking. Replaces any warning still waiting to be heard."""
        self.last_at = at
        if self.current is not None:
            self.current.cancel()
        self.current = self.audio.submit(self.clip, priority=URGENT)
