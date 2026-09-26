"""Laptop smoke test for the Guardian ElevenLabs agent. Not the device code.

Run from the repo root, with headphones (this has no echo control):

    set -a; source .env; set +a
    python3 -m companion.guardian.smoke --image scene.jpeg

The mic is always open here; push-to-talk and the shared audio owner belong to
the real GuardianController. prepare_sms is fake and sends nothing.
"""
import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    AudioInterface, ClientTools, Conversation, ConversationInitiationData)

from companion.errors import AppError
from companion.voice.__main__ import battery_text, network_up
from companion.voice.gemini import Gemini

RATE = 16000
CHUNK = 4000
# int16 peak treated as the user speaking; tune if the room is noisy.
VOICE_PEAK = 1500
# Agent audio after this much quiet counts as the start of a new reply.
REPLY_GAP = 1.0


class SoundDeviceAudio(AudioInterface):
    """Also prints turn latency: last loud mic chunk to first reply audio.
    Headphones are required for this to mean anything."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = bytearray()
        self.streams = []
        self.last_voice = None
        self.last_output = 0.0

    def start(self, input_callback):
        def capture(indata, _frames, _time, _status):
            data = bytes(indata)
            if np.abs(np.frombuffer(data, dtype="<i2")).max(initial=0) > VOICE_PEAK:
                self.last_voice = time.monotonic()
            input_callback(data)

        def playback(outdata, _frames, _time, _status):
            size = len(outdata)
            with self.lock:
                chunk = bytes(self.pending[:size])
                del self.pending[:size]
            outdata[:len(chunk)] = chunk
            outdata[len(chunk):] = b"\x00" * (size - len(chunk))

        self.streams = [
            sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                              blocksize=CHUNK, callback=capture),
            sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16",
                               callback=playback),
        ]
        for stream in self.streams:
            stream.start()

    def stop(self):
        for stream in self.streams:
            stream.stop()
            stream.close()
        self.streams = []

    def output(self, audio):
        now = time.monotonic()
        if now - self.last_output > REPLY_GAP and self.last_voice is not None:
            print(f"[turn] speech end -> first reply audio: {now - self.last_voice:.2f}s",
                  flush=True)
            self.last_voice = None
        self.last_output = now
        with self.lock:
            self.pending.extend(audio)

    def interrupt(self):
        with self.lock:
            self.pending.clear()


def timed(name, handler):
    def run(parameters):
        started = time.monotonic()
        print(f"[tool] {name}({parameters})", flush=True)
        result = handler(parameters)
        print(f"[tool] {name} -> {time.monotonic() - started:.2f}s: {result}", flush=True)
        return result
    return run


def status_text():
    return " ".join(["Network reachable." if network_up() else "No network.",
                     "Camera: static test image.", battery_text()])


def make_describe(image_path):
    if not image_path:
        return lambda _p: "As of now: a hallway with a door on the right. The sign on the door is unreadable."
    image = Path(image_path).read_bytes()
    gemini = Gemini(os.environ.get("GEMINI_API_KEY", ""),
                    os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"))

    def describe(_parameters):
        try:
            answer = gemini.ask(None, image, [])["answer"]
        except AppError as error:
            return f"The image service didn't respond: {error}"
        return f"As of {time.strftime('%I:%M %p')}: {answer}"
    return describe


def prepare_sms(parameters):
    note = (parameters.get("note") or "")[:160]
    return ("Preview read aloud; waiting for the user's answer. "
            f"(Smoke test: nothing was sent. Note: {note or 'none'})")


def main():
    parser = argparse.ArgumentParser(prog="python3 -m companion.guardian.smoke")
    parser.add_argument("--image", help="JPEG for a real Gemini describe_scene call")
    parser.add_argument("--contact", default="Sarah")
    args = parser.parse_args()

    key = os.environ.get("ELEVENLABS_API_KEY")
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID")
    if not key or not agent_id:
        print("Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID (source .env).", file=sys.stderr)
        return 1

    tools = ClientTools()
    tools.register("get_status", timed("get_status", lambda _p: status_text()))
    tools.register("describe_scene", timed("describe_scene", make_describe(args.image)))
    tools.register("prepare_sms", timed("prepare_sms", prepare_sms))

    conversation = Conversation(
        ElevenLabs(api_key=key),
        agent_id,
        requires_auth=True,
        audio_interface=SoundDeviceAudio(),
        client_tools=tools,
        config=ConversationInitiationData(dynamic_variables={
            "last_observation": "The camera saw a Room 204 sign at 3:52 PM.",
            "status": status_text(),
            "contact_name": args.contact,
            "sms_available": "yes",
            "time": time.strftime("%I:%M %p"),
        }),
        callback_agent_response=lambda text: print(f"Agent: {text}", flush=True),
        callback_user_transcript=lambda text: print(f"User:  {text}", flush=True),
        callback_end_session=lambda: print("[session ended]", flush=True),
    )
    print("Connecting... speak after the greeting. Ctrl+C to end.", flush=True)
    conversation.start_session()
    signal.signal(signal.SIGINT, lambda *_: conversation.end_session())
    print(f"Conversation ID: {conversation.wait_for_session_end()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
