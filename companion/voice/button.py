"""Press/release event sources. The state machine never learns which one it has."""
import queue
import sys
import threading
import time


class GpioButton:
    """Momentary button on a GPIO pin, wired to ground with an internal pull-up."""

    def __init__(self, pin, events):
        try:
            from gpiozero import Button  # Imported here so dev machines never need it.
        except ImportError:
            raise RuntimeError("gpiozero is required for GPIO button. Install with: sudo apt install python3-gpiozero python3-lgpio")
        self.events = events
        try:
            self.button = Button(pin, pull_up=True, bounce_time=0.02)
        except Exception as error:
            raise RuntimeError(f"Could not open GPIO pin {pin} ({error}). On Pi 5, ensure python3-lgpio is installed.") from error
        self.button.when_pressed = lambda: events.put(("press", time.monotonic()))
        self.button.when_released = lambda: events.put(("release", time.monotonic()))

    def close(self):
        self.button.close()


class KeyboardButton:
    """Dev stand-in. Terminals report no key-up, so Enter toggles press/release."""

    HELP = """
Dev controls (no GPIO):
  Enter   start talking, Enter again to send
  r       short press (repeat last answer)
  h       double press (help mode)
  q       quit
"""

    def __init__(self, events):
        self.events = events
        self.held = False
        self.running = True
        self.thread = threading.Thread(target=self._read, daemon=True)
        print(self.HELP, flush=True)
        self.thread.start()

    def _read(self):
        for line in sys.stdin:
            if not self.running:
                return
            key = line.strip().lower()
            now = time.monotonic()
            if key == "q":
                self.events.put(("quit", now))
                return
            if key == "r":
                self.events.put(("press", now))
                self.events.put(("release", now + 0.3))
            elif key == "h":
                for _ in range(2):
                    self.events.put(("press", now))
                    self.events.put(("release", now + 0.3))
            elif key == "":
                self.held = not self.held
                if self.held:
                    self.events.put(("press", now))
                else:
                    # Report a duration past the hold threshold so a toggled
                    # dev press is always treated as a real recording.
                    self.events.put(("release", now + 1.0))
        self.events.put(("quit", time.monotonic()))

    def close(self):
        self.running = False


def open_button(pin, events=None):
    events = events if events is not None else queue.Queue()
    if pin is None:
        return KeyboardButton(events), events
    try:
        return GpioButton(pin, events), events
    except Exception as error:
        print(f"GPIO button initialization failed: {error}\nFalling back to keyboard controls.", file=sys.stderr)
        return KeyboardButton(events), events
