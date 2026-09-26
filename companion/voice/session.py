"""In-memory state. Nothing here is written to disk."""
import time

IMAGE_TTL = 60
# Guardian's trail (plan §12c): distinct landmarks, newest first. Provisional limits.
TRAIL_SIZE = 3
TRAIL_TTL = 30 * 60


class Session:
    def __init__(self, ttl=IMAGE_TTL):
        self.ttl = ttl
        self.image = None
        self.image_at = 0.0
        self.captured_at = None
        self.history = []
        self.last_answer = ""
        self.observations = []  # [{"label", "at"}], newest first.

    @property
    def landmark(self):
        trail = self.trail()
        return trail[0] if trail else None

    def set_image(self, image, captured_at):
        self.image = image
        self.image_at = time.monotonic()
        self.captured_at = captured_at

    def fresh_image(self):
        """A wearable camera's scene goes stale far faster than a phone photo."""
        if self.image is None or time.monotonic() - self.image_at > self.ttl:
            return None
        return self.image

    def add_exchange(self, asked, answered):
        self.history.append((asked or "(spoken request)", answered))
        self.history = self.history[-4:]
        self.last_answer = answered

    def save_landmark(self, label, captured_at=None):
        """captured_at is when the camera saw it, not when Gemini answered.
        Seeing the same label again moves it to the front with the new time."""
        label = (label or "").strip()
        if not label:
            return
        at = captured_at or time.time()
        kept = [o for o in self.observations if o["label"].lower() != label.lower()]
        self.observations = ([{"label": label, "at": at}] + kept)[:TRAIL_SIZE]

    def trail(self, now=None):
        now = now or time.time()
        return [o for o in self.observations if now - o["at"] <= TRAIL_TTL]

    @staticmethod
    def _clock(at):
        return time.strftime("%I:%M %p", time.localtime(at)).lstrip("0")

    def describe_landmark(self):
        landmark = self.landmark
        if not landmark:
            return "No landmark has been observed yet."
        return (f"Last landmark: {landmark['label']}, seen at {self._clock(landmark['at'])}. "
                "That is a past observation, not your current location.")

    def observation_text(self):
        """Guardian wording: a timed camera observation, never a location."""
        landmark = self.landmark
        if not landmark:
            return "No landmark has been observed."
        return f"The camera last saw {landmark['label']} at {self._clock(landmark['at'])}."

    def trail_text(self):
        """Newest first, as what the camera saw — never a route or a location."""
        trail = self.trail()
        if not trail:
            return "No landmark has been observed."
        if len(trail) == 1:
            return self.observation_text()
        items = "; ".join(f"{o['label']} at {self._clock(o['at'])}" for o in trail)
        return f"Recent camera observations: {items}."

    def clear(self):
        self.image = None
        self.history = []
        self.last_answer = ""
