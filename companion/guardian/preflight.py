"""Guardian bring-up check: python3 -m companion.guardian.preflight

Run on the Pi before a demo. Each stage checks one thing Guardian needs, so a
failure names the part to fix instead of "Guardian isn't available right now".

    --pin 17        also test the button: triple tap when asked
    --no-connect    skip opening a real agent session (costs a few seconds of credits)
    --no-mic        skip the microphone recording
"""
import argparse
import importlib
import os
import shutil
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.voice.selftest import Report

SPEAKER_RATE = 22050
MIC_RATE = 16000
HOLD = 0.6
SHORT_PRESS = 0.15
TAP_WINDOW = 0.5


def check_environment(report):
    report.stage("Configuration")
    for name in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "GEMINI_API_KEY"):
        if os.environ.get(name):
            report.ok(f"{name} is set.")
        else:
            report.fail(f"{name} is not set. Guardian stays off without the first two.")
    sms = os.environ.get("GUARDIAN_SMS", "")
    if not sms:
        report.skip("GUARDIAN_SMS unset: Guardian will say texting is unavailable.")
    elif sms == "fake":
        report.ok("GUARDIAN_SMS=fake: texts are printed, not sent. Say so in the demo.")
    elif sms == "twilio":
        missing = [n for n in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
                               "GUARDIAN_CONTACT_NUMBER") if not os.environ.get(n)]
        if missing:
            report.fail(f"GUARDIAN_SMS=twilio but {', '.join(missing)} unset.")
        else:
            report.ok("GUARDIAN_SMS=twilio with account, sender and contact number set.")
    else:
        report.fail(f"GUARDIAN_SMS={sms!r} is not a known mode (fake, twilio).")
    if os.environ.get("GUARDIAN_CONTACT_NAME"):
        report.ok(f"Contact name: {os.environ['GUARDIAN_CONTACT_NAME']}.")
    else:
        report.skip("GUARDIAN_CONTACT_NAME unset: the agent will say \"your contact\".")


def check_packages(report, need_gpio):
    report.stage("Python packages")
    wanted = ["numpy", "sounddevice", "elevenlabs"] + (["gpiozero"] if need_gpio else [])
    for name in wanted:
        try:
            module = importlib.import_module(name)
            report.ok(f"{name} {getattr(module, '__version__', '')}".strip() + ".")
        except Exception as error:
            report.fail(f"{name} does not import ({error!r}).")
    engine = shutil.which("espeak-ng") or shutil.which("espeak") or shutil.which("say")
    if engine:
        report.ok(f"Local speech engine: {engine}.")
    else:
        report.fail("No local speech engine. Install espeak-ng: warnings and help depend on it.")


def check_devices(report):
    report.stage("Audio devices")
    try:
        import sounddevice as sd
    except Exception as error:
        report.fail(f"sounddevice unavailable ({error!r}).")
        return False
    ok = True
    try:
        output = sd.query_devices(kind="output")
        sd.check_output_settings(samplerate=SPEAKER_RATE, channels=1, dtype="int16")
        report.ok(f"Speaker '{output['name']}' accepts {SPEAKER_RATE} Hz mono.")
    except Exception as error:
        ok = False
        report.fail(f"Default speaker rejects {SPEAKER_RATE} Hz mono ({error}). "
                    "Use an ALSA 'plug' default device or change OUTPUT_RATE.")
    try:
        mic = sd.query_devices(kind="input")
        sd.check_input_settings(samplerate=MIC_RATE, channels=1, dtype="int16")
        report.ok(f"Microphone '{mic['name']}' accepts {MIC_RATE} Hz mono.")
    except Exception as error:
        ok = False
        report.fail(f"Default microphone rejects {MIC_RATE} Hz mono ({error}).")
    return ok


def check_speech(report, audio, speech):
    report.stage("Local speech and speaker")
    started = time.monotonic()
    clip = speech.render_local("Obstacle ahead.")
    render = time.monotonic() - started
    if clip is None:
        report.fail("Local speech could not render the hazard phrase.")
        return
    report.ok(f"Hazard phrase rendered in {render:.2f}s.")
    if render > 0.5:
        print("        NOTE: slow render delays local help and status speech, not warnings "
              "(the warning is rendered once at startup).", flush=True)
    playback = audio.submit(clip)
    queued = time.monotonic()
    while not playback.started and not playback.done.is_set() and time.monotonic() - queued < 2:
        time.sleep(0.002)
    if playback.started:
        report.ok(f"Audio owner started it {1000 * (time.monotonic() - queued):.0f} ms after "
                  "queueing. You should hear \"Obstacle ahead.\" The speaker's own delay "
                  "is not included.")
    else:
        report.fail("The audio owner never started playing. Check the speaker.")
    playback.wait(5)


def check_microphone(report, seconds):
    report.stage("Microphone")
    try:
        import numpy as np
        import sounddevice as sd
        print(f"  Recording {seconds:.0f}s — say something at normal volume.", flush=True)
        samples = sd.rec(int(MIC_RATE * seconds), samplerate=MIC_RATE, channels=1,
                         dtype="int16")
        sd.wait()
    except Exception as error:
        report.fail(f"Could not record ({error!r}).")
        return
    peak = int(np.abs(samples).max()) if len(samples) else 0
    if peak == 0:
        report.fail("Digital silence (peak 0): the mic is muted or not permitted.")
    elif peak < 500:
        report.fail(f"Peak {peak} is very quiet; raise the capture gain (alsamixer).")
    else:
        report.ok(f"Peak {peak}.")


def check_button(report, pin):
    report.stage(f"Button (GPIO {pin})")
    try:
        from gpiozero import Button
        button = Button(pin, pull_up=True, bounce_time=0.02)
    except Exception as error:
        report.fail(f"Could not open GPIO {pin} ({error!r}).")
        return
    events = []
    button.when_pressed = lambda: events.append(("press", time.monotonic()))
    button.when_released = lambda: events.append(("release", time.monotonic()))
    print("  Triple tap the button now (you have 8 seconds).", flush=True)
    time.sleep(8)
    button.close()
    presses = [t for kind, t in events if kind == "press"]
    releases = [t for kind, t in events if kind == "release"]
    taps = list(zip(presses, releases))
    if not taps:
        report.fail("No presses seen. Check wiring: button between the pin and GND.")
        return
    durations = [r - p for p, r in taps]
    gaps = [taps[i + 1][0] - taps[i][1] for i in range(len(taps) - 1)]
    print("  Press lengths: " + ", ".join(f"{d * 1000:.0f} ms" for d in durations), flush=True)
    if gaps:
        print("  Gaps between taps: " + ", ".join(f"{g * 1000:.0f} ms" for g in gaps), flush=True)
    counted = [d for d in durations if SHORT_PRESS <= d < HOLD]
    too_short = [d for d in durations if d < SHORT_PRESS]
    if too_short:
        report.fail(f"{len(too_short)} press(es) under {SHORT_PRESS * 1000:.0f} ms would be "
                    "ignored as too brief, breaking the triple tap.")
    elif len(counted) >= 3 and all(g < TAP_WINDOW for g in gaps):
        report.ok("That would count as a triple tap.")
    elif any(g >= TAP_WINDOW for g in gaps):
        report.fail(f"A gap reached {TAP_WINDOW * 1000:.0f} ms, which ends the tap chain early.")
    else:
        report.fail(f"Only {len(counted)} press(es) counted as taps.")


def check_network(report):
    report.stage("Network")
    from companion.voice.__main__ import network_up

    if network_up():
        report.ok("8.8.8.8:53 reachable (the check Guardian uses before opening).")
    else:
        report.fail("8.8.8.8:53 unreachable: Guardian will refuse to open. If the network "
                    "is up, this venue may block outside DNS.")
    started = time.monotonic()
    try:
        urlopen(Request("https://api.elevenlabs.io/", method="HEAD"), timeout=5).close()
    except HTTPError:
        pass  # Any HTTP answer means the host is reachable.
    except (URLError, OSError) as error:
        report.fail(f"api.elevenlabs.io unreachable ({error}).")
        return False
    report.ok(f"api.elevenlabs.io answered in {1000 * (time.monotonic() - started):.0f} ms.")
    return True


def check_agent(report, connect):
    report.stage("ElevenLabs agent")
    key, agent_id = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_AGENT_ID")
    if not (key and agent_id):
        report.skip("Key or agent ID missing.")
        return
    url = ("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id="
           + agent_id)
    started = time.monotonic()
    try:
        with urlopen(Request(url, headers={"xi-api-key": key}), timeout=10):
            pass
    except HTTPError as error:
        report.fail(f"Signed URL refused: HTTP {error.code}. The key may lack Agents "
                    "permission, or the agent ID is wrong.")
        return
    except (URLError, OSError) as error:
        report.fail(f"Signed URL request failed ({error}).")
        return
    report.ok(f"Signed URL issued in {1000 * (time.monotonic() - started):.0f} ms.")
    if not connect:
        report.skip("Session test skipped (--no-connect).")
        return
    check_session(report, key, agent_id)


def check_session(report, key, agent_id):
    """A real session with silence in and audio counted, not played."""
    from companion.guardian.session import connect_elevenlabs

    class Silent:
        def __init__(self):
            self.first_audio = None
            self.bytes = 0
            self.running = False
            self.on_started = None

        def start(self, send):
            self.running = True

            def feed():
                while self.running:
                    send(bytes(8000))
                    time.sleep(0.25)
            threading.Thread(target=feed, daemon=True).start()

        def stop(self):
            self.running = False

        def output(self, audio):
            if self.first_audio is None:
                self.first_audio = time.monotonic()
            self.bytes += len(audio)

        def interrupt(self):
            pass

    voice = Silent()
    beats = []
    started = time.monotonic()
    try:
        conversation = connect_elevenlabs(
            key, agent_id, voice, {}, {"last_observation": "No landmark has been observed.",
                                       "status": "Preflight check.", "contact_name": "your contact",
                                       "sms_available": "no", "time": time.strftime("%I:%M %p")},
            on_transcript=lambda _t: None, on_heartbeat=lambda: beats.append(1),
            on_end=lambda: None)
    except Exception as error:
        report.fail(f"Session did not start ({error!r}).")
        return
    time.sleep(8)
    conversation.end_session()
    if voice.first_audio is None:
        report.fail("Connected but no greeting audio within 8 s.")
    else:
        report.ok(f"Greeting audio after {voice.first_audio - started:.2f}s "
                  f"({voice.bytes / 32000:.1f}s of speech).")
    if beats:
        report.ok(f"{len(beats)} heartbeats in 8 s (Guardian closes after 15 s without one).")
    else:
        report.fail("No heartbeats: Guardian's dead-connection check has nothing to watch.")


def check_load(report):
    report.stage("System load")
    try:
        one, five, _ = os.getloadavg()
        cores = os.cpu_count() or 1
    except OSError:
        report.skip("Load average unavailable.")
        return
    message = f"Load {one:.1f} (1 min), {five:.1f} (5 min) on {cores} cores."
    if one > cores:
        report.fail(message + " The CPU is saturated; audio may stutter.")
    else:
        report.ok(message + " Run this again with the ROS stack up.")


def main():
    parser = argparse.ArgumentParser(prog="python3 -m companion.guardian.preflight")
    parser.add_argument("--pin", type=int, help="GPIO pin to test with a triple tap")
    parser.add_argument("--no-connect", action="store_true")
    parser.add_argument("--no-mic", action="store_true")
    parser.add_argument("--mic-seconds", type=float, default=3.0)
    args = parser.parse_args()

    report = Report()
    print("Guardian preflight.", flush=True)
    check_environment(report)
    check_packages(report, need_gpio=args.pin is not None)
    if check_devices(report):
        from companion.voice.audio import Audio
        from companion.voice.speech import Speech

        audio = Audio()
        check_speech(report, audio, Speech(audio))
        audio.close()
        if not args.no_mic:
            check_microphone(report, args.mic_seconds)
    if args.pin is not None:
        check_button(report, args.pin)
    if check_network(report):
        check_agent(report, connect=not args.no_connect)
    check_load(report)
    print(f"\n{'All stages passed.' if not report.failures else f'{report.failures} stage(s) failed.'}",
          flush=True)
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
