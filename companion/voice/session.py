"""In-memory state. Nothing here is written to disk."""
import time

IMAGE_TTL = 60


class Session:
    def __init__(self, ttl=IMAGE_TTL):
        self.ttl = ttl
        self.image = None
        self.image_at = 0.0
        self.captured_at = None
        self.history = []
        self.last_answer = ""
        self.landmark = None

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
        """captured_at is when the camera saw it, not when Gemini answered."""
        if label:
            self.landmark = {"label": label, "at": captured_at or time.time()}

    def _landmark_time(self):
        return time.strftime("%I:%M %p", time.localtime(self.landmark["at"])).lstrip("0")

    def describe_landmark(self):
        if not self.landmark:
            return "No landmark has been observed yet."
        return (f"Last landmark: {self.landmark['label']}, seen at {self._landmark_time()}. "
                "That is a past observation, not your current location.")

    def observation_text(self):
        """Guardian wording: a timed camera observation, never a location."""
        if not self.landmark:
            return "No landmark has been observed."
        return f"The camera last saw {self.landmark['label']} at {self._landmark_time()}."

    def clear(self):
        self.image = None
        self.history = []
        self.last_answer = ""
