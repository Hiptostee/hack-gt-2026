"""Bring-up harness: python3 -m companion.voice.selftest

Checks each stage of the voice pipeline on its own, so a failure points at one
component instead of "it didn't talk". The spoken question is synthesized
locally rather than recorded, so the cloud path can be tested before the
microphone works.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from companion.errors import AppError
from companion.voice.audio import Audio
from companion.voice.gemini import Gemini
from companion.voice.speech import Speech

QUESTION = "How many doors are there in this room"


class Report:
    def __init__(self):
        self.failures = 0

    def stage(self, name):
        print(f"\n--- {name} ---", flush=True)

    def ok(self, message):
        print(f"  PASS  {message}", flush=True)

    def skip(self, message):
        print(f"  SKIP  {message}", flush=True)

    def fail(self, message):
        self.failures += 1
        print(f"  FAIL  {message}", flush=True)


def synthesize(text, path):
    if platform.system() == "Darwin":
        command = ["say", "-o", str(path), "--data-format=LEI16@16000", text]
    elif shutil.which("espeak-ng"):
        command = ["espeak-ng", "-w", str(path), text]
    elif shutil.which("espeak"):
        command = ["espeak", "-w", str(path), text]
    else:
        return None
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    return path.read_bytes() if path.exists() else None


def check_output(report, audio):
    report.stage("Audio output")
    try:
        audio.earcon("listening")
        report.ok("Played an earcon. You should have heard two short rising tones.")
        return True
    except Exception as error:
        report.fail(f"Could not play audio: {error!r}")
        return False


def check_microphone(report, audio, seconds):
    """Returns the captured WAV so the rest of the test can send what was
    actually said, rather than checking the mic and then ignoring it."""
    report.stage("Microphone")
    try:
        audio.record_start()
        print(f"  Recording {seconds}s — ask a question about what the camera sees.",
              flush=True)
        time.sleep(seconds)
        wav, captured, peak = audio.record_stop()
    except Exception as error:
        report.fail(f"Could not open the microphone: {error!r}")
        return None
    if peak == 0:
        report.fail(f"Captured {captured:.1f}s of digital silence (peak 0). "
                    "The device is almost certainly not permitted to use the "
                    "microphone rather than genuinely quiet.")
        if platform.system() == "Darwin":
            print("        macOS: System Settings > Privacy & Security > "
                  "Microphone, enable your terminal or editor, then restart it.",
                  flush=True)
        else:
            print("        Check `arecord -l` and that the user is in the audio group.",
                  flush=True)
        return None
    if peak < 200:
        report.fail(f"Captured {captured:.1f}s but peak was only {peak}. "
                    "Below the MIN_PEAK threshold, so this would be treated as "
                    "silence. Check input gain or mic placement.")
        return None
    report.ok(f"Captured {captured:.1f}s, peak {peak}.")
    return wav


def check_gemini(report, gemini, audio_wav, image):
    report.stage("Gemini")
    if not gemini.key:
        report.skip("GEMINI_API_KEY is not set.")
        return None
    if not audio_wav:
        report.fail("No synthesized audio to send.")
        return None
    if image and len(image) > 1_500_000:
        print(f"  NOTE: the image is {len(image) / 1e6:.1f} MB, which base64 "
              "inflates by a third. A live camera frame is far smaller.", flush=True)
    started = time.monotonic()
    try:
        result = gemini.ask(audio_wav, image, [])
    except AppError as error:
        report.fail(f"{error}")
        if error.status in (503, 504):
            print("        The model was overloaded or unreachable, which says "
                  "nothing about the request. Retried already; try again shortly.",
                  flush=True)
        elif error.status == 404:
            print("        The model does not exist or is no longer available. "
                  "Check GEMINI_MODEL.", flush=True)
        else:
            isolate(report, gemini, audio_wav, image)
        return None
    elapsed = time.monotonic() - started
    report.ok(f"Round trip {elapsed:.2f}s using model {gemini.model}.")
    print(f"        asked    : {QUESTION!r}", flush=True)
    print(f"        heard    : {result['transcript']!r}", flush=True)
    print(f"        answer   : {result['answer']!r}", flush=True)
    print(f"        landmark : {result['landmark']!r}", flush=True)
    print(f"        action   : {result['device_action']}", flush=True)
    if not result["transcript"].strip():
        report.fail("Gemini returned an empty transcript — it did not hear the audio.")
    if image is None and "see" not in result["answer"].lower():
        print("        NOTE: no image was sent; the answer should say it cannot see.",
              flush=True)
    return result


def isolate(report, gemini, audio_wav, image):
    """Narrow a rejection to one ingredient instead of guessing across three."""
    print("  Isolating which part of the request was rejected:", flush=True)
    probes = [("audio only, no image", audio_wav, None),
              ("image only, no audio", b"", image),
              ("neither, text only", b"", None)]
    for label, audio, frame in probes:
        try:
            gemini.ask(audio, frame, [])
            print(f"        {label}: ACCEPTED", flush=True)
        except AppError as error:
            print(f"        {label}: rejected ({error})", flush=True)
    print("        Whichever probe is accepted names the ingredient at fault. "
          "If text-only is rejected too, it is the model, key, or schema — the "
          "Gemini error printed above says which.", flush=True)


def check_elevenlabs(report, speech, text):
    report.stage("ElevenLabs")
    if not speech.key:
        report.skip("ELEVENLABS_API_KEY is not set; the local engine will be used.")
        speech.say(text, local=True)
        report.ok("Local speech engine spoke the answer.")
        return
    if not speech.voice_id:
        report.fail("No voice id available. Set ELEVENLABS_VOICE_ID.")
        return
    timing = {}
    speech.on_first_audio = lambda: timing.setdefault("first", time.monotonic())
    started = time.monotonic()
    speech.say(text)
    speech.on_first_audio = None
    if not speech.cloud_ok:
        report.fail("Fell back to the local engine — see the error above.")
        return
    first = timing.get("first", started) - started
    report.ok(f"First audio byte in {first:.2f}s, "
              f"finished in {time.monotonic() - started:.2f}s, voice {speech.voice_id}.")
    if first > 1.5:
        print("        NOTE: slow first byte. Check ELEVENLABS_MODEL and the network.",
              flush=True)


def main():
    parser = argparse.ArgumentParser(prog="python3 -m companion.voice.selftest")
    parser.add_argument("--image", help="JPEG to send as the camera frame")
    parser.add_argument("--text", default=QUESTION, help="Question to synthesize and send")
    parser.add_argument("--skip-mic", action="store_true")
    parser.add_argument("--mic-seconds", type=float, default=3.0)
    args = parser.parse_args()

    report = Report()
    audio = Audio()
    speech = Speech(audio,
                    key=os.environ.get("ELEVENLABS_API_KEY", ""),
                    voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
                    model_id=os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5"))
    gemini = Gemini(os.environ.get("GEMINI_API_KEY", ""),
                    os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"))

    print(f"Voice companion self-test on {platform.system()}.", flush=True)
    check_output(report, audio)

    mic_wav = None
    if not args.skip_mic:
        mic_wav = check_microphone(report, audio, args.mic_seconds)

    # Use the mic recording for Gemini when available. Fall back to a locally
    # synthesized question only when the mic is skipped or captured silence.
    if mic_wav:
        audio_for_gemini = mic_wav
        print(f"\n  Using microphone recording for Gemini.", flush=True)
    else:
        with tempfile.TemporaryDirectory() as directory:
            audio_for_gemini = synthesize(args.text, Path(directory) / "question.wav")
        if audio_for_gemini:
            report.stage("Speech synthesis (fallback for microphone)")
            report.ok(f"Synthesized {len(audio_for_gemini)} bytes for {args.text!r}.")
        else:
            report.stage("Speech synthesis (fallback for microphone)")
            report.fail("No local speech engine to synthesize with. Install espeak-ng.")

    image = None
    if args.image:
        path = Path(args.image)
        if path.is_file():
            image = path.read_bytes()
            print(f"  Using camera frame {path} ({len(image)} bytes).", flush=True)
        else:
            report.fail(f"Image not found: {path}")

    result = check_gemini(report, gemini, audio_for_gemini, image)

    answer = result["answer"] if result else "This is a speech output test."
    check_elevenlabs(report, speech, answer)

    print(f"\n{'All stages passed.' if not report.failures else f'{report.failures} stage(s) failed.'}",
          flush=True)
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
