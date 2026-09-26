"""Application-enforced SMS confirmation (Guardian spec §7).

The agent can only prepare a draft. The device reads the preview itself, and
only a clear yes from a push-to-talk turn that started after the preview ended
sends it, once. Nothing the model says can authorize a send.
"""
import re
import sys
import threading
import time
from dataclasses import dataclass, field

DRAFT_TTL = 60.0
MAX_SENDS = 3
NOTE_LIMIT = 160
DELIVERY_WAIT = 15.0

AFFIRM = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|send( it)?|go ahead|please (do|send)|do it)\b",
                    re.IGNORECASE)
DENY = re.compile(r"\b(no|nope|don't|do not|cancel|stop|wait|never ?mind)\b", re.IGNORECASE)

OUTCOME_TEXT = {
    "submitted": "Your message was submitted.",
    "delivered": "Delivery was confirmed.",
    "failed": "The message could not be sent.",
    "unknown": "I couldn't confirm whether it was sent.",
}


@dataclass
class Draft:
    text: str
    created_at: float
    preview_ended_at: float = None
    number: int = field(default=0)


class FakeSender:
    """Development stand-in: prints instead of sending. Say so in any demo."""
    can_check = False

    def send(self, to_number, text):
        print(f"[fake sms] to {to_number or '(no number)'}: {text}", flush=True)
        return {"status": "submitted", "id": "fake"}


class SmsGate:
    def __init__(self, sender, contact_name, contact_number, user_name, speak, update,
                 clock=time.monotonic, spawn=None):
        self.sender = sender
        self.contact_name = contact_name or "your contact"
        self.contact_number = contact_number
        self.user_name = user_name or "The user"
        self.speak = speak      # speak(text) -> bool; plays locally and blocks.
        self.update = update    # update(text): tell the agent what happened.
        self.clock = clock
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.lock = threading.Lock()
        self.draft = None
        self.sends = 0
        self.drafts = 0

    @property
    def available(self):
        return self.sender is not None

    def compose(self, note, observation):
        # Short on purpose: the device reads all of it aloud inside the agent's
        # 25 s tool timeout, and the contact needs the facts, not device health.
        note = " ".join((note or "").split())[:NOTE_LIMIT].rstrip(".")
        text = f"{self.user_name} asked for help from their wearable. {observation}"
        if note:
            text += f' They said: "{note}."'
        return text + " This is not an emergency service."

    def prepare(self, note, observation):
        """Called from the agent's tool. Blocks while the preview is read."""
        if not self.available:
            return "Texting isn't available on this device right now. Tell the user."
        with self.lock:
            self._expire_locked()
            if self.draft is not None:
                return "A text is already waiting for the user's answer. Do not prepare another."
            if self.sends >= MAX_SENDS:
                return "The text limit for this session has been reached. Tell the user."
            self.drafts += 1
            draft = self.draft = Draft(self.compose(note, observation), self.clock(),
                                       number=self.drafts)
        self.speak(f"I'll text {self.contact_name}: {draft.text} "
                   "Hold the button and say yes to send, or no to cancel.")
        with self.lock:
            if self.draft is not draft:
                return "The text was cancelled before the user could answer. It was not sent."
            draft.preview_ended_at = self.clock()
        return ("The device read the text aloud and is waiting for the user's answer. "
                "Do not say it was sent.")

    def on_transcript(self, transcript, turn_started_at):
        """A finished user turn. Only a turn begun after the preview can answer it."""
        with self.lock:
            self._expire_locked()
            draft = self.draft
            if (draft is None or draft.preview_ended_at is None or turn_started_at is None
                    or turn_started_at < draft.preview_ended_at):
                return
            self.draft = None
            confirmed = bool(AFFIRM.search(transcript or "")) and not DENY.search(transcript or "")
            if confirmed:
                self.sends += 1
        if confirmed:
            self.spawn(lambda: self._send(draft))
        else:
            self._report("The user did not confirm, so the text was not sent.")

    def cancel(self, reason):
        with self.lock:
            draft, self.draft = self.draft, None
        if draft is not None:
            self._report(f"The pending text was cancelled ({reason}). It was not sent.",
                         aloud=False)

    def _expire_locked(self):
        if self.draft is not None and self.clock() - self.draft.created_at > DRAFT_TTL:
            self.draft = None

    def _send(self, draft):
        try:
            result = self.sender.send(self.contact_number, draft.text)
            status = result.get("status", "unknown")
        except Exception as error:  # An exception after sending may mean it went out.
            print(f"SMS send raised: {error!r}", file=sys.stderr)
            result, status = {}, "unknown"
        if status not in OUTCOME_TEXT:
            status = "unknown"
        self._report(OUTCOME_TEXT[status])
        if status == "submitted" and getattr(self.sender, "can_check", False) and result.get("id"):
            deadline = self.clock() + DELIVERY_WAIT
            while self.clock() < deadline:
                time.sleep(2.0)
                state = self.sender.check(result["id"])
                if state in ("delivered", "failed"):
                    self._report(OUTCOME_TEXT[state])
                    return

    def _report(self, text, aloud=True):
        if aloud:
            self.speak(text)
        self.update(f"Device update about the text message: {text}")
