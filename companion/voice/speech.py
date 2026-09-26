"""Speech output: ElevenLabs streaming, with a local engine as the safety net.

The local engine must work before ElevenLabs is trusted. It is what every
network failure falls back to, and a silent device is indistinguishable from a
dead one.
"""
import json
import platform
import shutil
import socket
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

API = "https://api.elevenlabs.io/v1"
PCM_RATE = 22050
OUTPUT_FORMAT = "pcm_22050"
# ElevenLabs keys carry granular permissions, and a key scoped to synthesis
# alone cannot list voices. Falling back to a long-standing stock voice keeps
# the device talking instead of dropping to the robotic local engine.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


class Speech:
    def __init__(self, audio, key="", voice_id="", model_id="eleven_flash_v2_5"):
        self.audio = audio
        self.key = key
        self.voice_id = voice_id
        self.model_id = model_id
        self.process = None
        self.cloud_ok = bool(key)
        self.on_first_audio = None  # Called when the first PCM chunk arrives.
        if self.key and not self.voice_id:
            self.voice_id = self._first_voice()

    def _first_voice(self):
        try:
            request = Request(API + "/voices", headers={"xi-api-key": self.key})
            with urlopen(request, timeout=10) as response:
                payload = json.load(response)
            voices = payload.get("voices", [])
            if voices:
                return voices[0]["voice_id"]
            print(f"ElevenLabs: the account lists no voices (keys: {list(payload)}).",
                  file=sys.stderr)
        except HTTPError as error:
            detail = error.read()[:300].decode("utf-8", "replace")
            print(f"ElevenLabs /voices {error.code}: {detail}", file=sys.stderr)
        except (URLError, TimeoutError, socket.timeout) as error:
            print(f"ElevenLabs /voices unreachable: {error}", file=sys.stderr)
        except (ValueError, KeyError, IndexError) as error:
            print(f"ElevenLabs /voices unreadable: {error!r}", file=sys.stderr)
        print(f"ElevenLabs: could not list voices; using stock voice {DEFAULT_VOICE}. "
              "Set ELEVENLABS_VOICE_ID to choose another.", file=sys.stderr)
        return DEFAULT_VOICE

    def say(self, text, local=False):
        """local=True forces the offline engine, for anything that must work
        without a network — status reports and help, above all."""
        if not text.strip():
            return
        if not local and self.key and self.voice_id and self._say_cloud(text):
            return
        self._say_local(text)

    def _say_cloud(self, text):
        url = (API + "/text-to-speech/" + self.voice_id + "/stream?"
               + urlencode({"output_format": OUTPUT_FORMAT}))
        payload = {"text": text, "model_id": self.model_id}
        request = Request(url, data=json.dumps(payload).encode(),
                          headers={"xi-api-key": self.key, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                self.cloud_ok = True
                return self.audio.play_stream(self._chunks(response), PCM_RATE)
        except HTTPError as error:
            detail = error.read()[:300].decode("utf-8", "replace")
            print(f"ElevenLabs {error.code}: {detail}", file=sys.stderr)
        except (URLError, TimeoutError, socket.timeout) as error:
            print(f"ElevenLabs unreachable: {error}", file=sys.stderr)
        self.cloud_ok = False
        return False

    def _chunks(self, response):
        remainder = b""
        first = True
        while True:
            block = response.read(4096)
            if not block:
                break
            if first and self.on_first_audio:
                self.on_first_audio()
                first = False
            block = remainder + block
            # A 16-bit sample must not be split across two yielded chunks.
            usable = len(block) - (len(block) % 2)
            remainder = block[usable:]
            yield np.frombuffer(block[:usable], dtype="<i2")

    def _say_local(self, text):
        self.stop()
        if platform.system() == "Darwin":
            command = ["say", text]
        elif shutil.which("espeak-ng"):
            command = ["espeak-ng", "-s", "170", text]
        elif shutil.which("espeak"):
            command = ["espeak", "-s", "170", text]
        else:
            print("No local speech engine. Install espeak-ng.", file=sys.stderr)
            self.audio.earcon("error")
            return
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            self.process.wait()
        except OSError as error:
            print(f"Local speech failed: {error}", file=sys.stderr)
            self.audio.earcon("error")

    def stop(self):
        self.audio.stop()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.process = None
