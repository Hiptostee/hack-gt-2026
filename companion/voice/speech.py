"""Speech output: ElevenLabs streaming, with a local engine as the safety net.

The local engine must work before ElevenLabs is trusted. It is what every
network failure falls back to, and a silent device is indistinguishable from a
dead one.

Both engines play through the audio owner. Local speech is rendered to PCM
first, so nothing else ever opens the output device.
"""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from companion.voice.audio import ANSWER, resample

try:
    import numpy as np
except ImportError:
    np = None

API = "https://api.elevenlabs.io/v1"
PCM_RATE = 22050
OUTPUT_FORMAT = "pcm_22050"
# ElevenLabs keys carry granular permissions, and a key scoped to synthesis
# alone cannot list voices. Falling back to a long-standing stock voice keeps
# the device talking instead of dropping to the robotic local engine.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_SPEED = 1.15
RENDER_TIMEOUT = 15


class Speech:
    def __init__(self, audio, key="", voice_id="", model_id="eleven_flash_v2_5", speed=None):
        self.audio = audio
        self.key = key
        self.voice_id = voice_id
        self.model_id = model_id
        self.speed = speed if speed is not None else float(
            os.environ.get("COMPANION_SPEECH_SPEED", str(DEFAULT_SPEED)))
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

    def say(self, text, local=False, priority=ANSWER):
        """local=True forces the offline engine, for anything that must work
        without a network — status reports and help, above all. Returns False
        if the speech was cut off."""
        if not text.strip():
            return True
        generation = self.audio.generation(priority)
        if not local and self.key and self.voice_id:
            played = self._say_cloud(text, priority, generation)
            if played is not None:
                return played
        return self._say_local(text, priority, generation)

    def _say_cloud(self, text, priority, generation=None):
        """None means no audio was produced, so the caller falls back to local."""
        url = (API + "/text-to-speech/" + self.voice_id + "/stream?"
               + urlencode({"output_format": OUTPUT_FORMAT}))
        payload = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": self.speed,
            },
        }
        request = Request(url, data=json.dumps(payload).encode(),
                          headers={"xi-api-key": self.key, "Content-Type": "application/json"})
        # Register before opening the network request: a hazard must revoke
        # this answer even if HTTP has not returned its first byte yet.
        playback = self.audio.stream(priority, generation=generation)
        fed = False
        try:
            with urlopen(request, timeout=20) as response:
                self.cloud_ok = True
                if playback.cancelled:
                    return False
                for chunk in self._chunks(response):
                    if not playback.feed(chunk, PCM_RATE):
                        break  # Cancelled: stop reading, drop the rest.
                    fed = True
        except HTTPError as error:
            detail = error.read()[:300].decode("utf-8", "replace")
            print(f"ElevenLabs {error.code}: {detail}", file=sys.stderr)
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            print(f"ElevenLabs unreachable: {error}", file=sys.stderr)
        finally:
            if playback is not None:
                playback.close()
        if playback is not None and (fed or playback.cancelled):
            return playback.wait()
        self.cloud_ok = False
        return None

    def _chunks(self, response):
        remainder = b""
        first = True
        chunk_size = 1024
        while True:
            block = response.read(chunk_size)
            if not block:
                break
            if first:
                if self.on_first_audio:
                    self.on_first_audio()
                first = False
                chunk_size = 4096
            block = remainder + block
            # A 16-bit sample must not be split across two yielded chunks.
            usable = len(block) - (len(block) % 2)
            remainder = block[usable:]
            if np is not None:
                yield np.frombuffer(block[:usable], dtype="<i2")

    def _say_local(self, text, priority, generation=None):
        # Sentence by sentence: the first plays while the rest render, so a long
        # status report does not wait for the whole thing to synthesize.
        playback = self.audio.stream(priority, generation=generation)
        spoke = False
        try:
            for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
                samples = self.render_local(sentence)
                if samples is None:
                    break
                if not playback.feed(samples):
                    break
                spoke = True
        finally:
            playback.close()
        if not spoke and not playback.cancelled:
            self.audio.earcon("error")
            return False
        return playback.wait()

    def render_local(self, text, lang=None):
        """Offline engine to int16 PCM at the owner's rate, or None.

        *lang* selects the TTS voice (e.g. ``"ko"`` for Korean).  When
        ``None`` the engine's default (English) voice is used.
        """
        from companion.i18n import ESPEAK_VOICES, SAY_VOICES

        if platform.system() == "Darwin":
            wpm = str(int(175 * self.speed))
            command = ["say", "-r", wpm, "--data-format=LEI16@22050"]
            voice = SAY_VOICES.get(lang) if lang else None
            if voice:
                command += ["-v", voice]
            command.append("-o")
        elif shutil.which("espeak-ng"):
            voice = ESPEAK_VOICES.get(lang, "en") if lang else "en"
            command = ["espeak-ng", "-v", voice, "-s", str(int(170 * self.speed)), "--stdin", "-w"]
        elif shutil.which("espeak"):
            voice = ESPEAK_VOICES.get(lang, "en") if lang else "en"
            command = ["espeak", "-v", voice, "-s", str(int(170 * self.speed)), "--stdin", "-w"]
        else:
            print("No local speech engine. Install espeak-ng.", file=sys.stderr)
            return None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "speech.wav"
            try:
                # Text goes on stdin so a leading "-" is never read as an option.
                subprocess.run(command + [str(path)], input=text.encode(), check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=RENDER_TIMEOUT)
                with wave.open(str(path), "rb") as handle:
                    rate = handle.getframerate()
                    channels = handle.getnchannels()
                    if handle.getsampwidth() != 2:
                        raise ValueError("expected 16-bit audio")
                    frames = handle.readframes(handle.getnframes())
            except (OSError, subprocess.SubprocessError, ValueError, wave.Error) as error:
                print(f"Local speech failed: {error!r}", file=sys.stderr)
                return None
        samples = np.frombuffer(frames, dtype="<i2")
        if channels > 1:
            samples = samples[::channels]
        return resample(samples, rate, self.audio.output_rate)

    def stop(self):
        self.audio.stop()
