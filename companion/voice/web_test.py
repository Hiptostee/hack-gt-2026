"""Web testing interface that simulates the Raspberry Pi voice companion.

Run:
    python3 -m companion.voice.web_test --image scene.jpeg

Opens http://localhost:8080. The page has a push-to-talk button that behaves
like the physical button on the Pi, plays audio through both the browser and the
host computer's speakers (Mac/Pi), and shows live debug diagnostics.
"""
import argparse
import base64
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from companion.errors import AppError
from companion.voice.gemini import Gemini, DEFAULT_MODEL

ELEVENLABS_API = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


def resolve_voice(key):
    """Pick a voice: env var > first on the account > stock default."""
    vid = os.environ.get("ELEVENLABS_VOICE_ID", "")
    if vid:
        return vid, "env"
    if not key:
        return "", "no key"
    try:
        req = Request(ELEVENLABS_API + "/voices", headers={"xi-api-key": key})
        with urlopen(req, timeout=10) as resp:
            voices = json.load(resp).get("voices", [])
            if voices:
                return voices[0]["voice_id"], "account"
    except HTTPError as err:
        detail = err.read()[:200].decode("utf-8", "replace")
        print(f"ElevenLabs /voices {err.code}: {detail}", file=sys.stderr)
    except Exception as err:
        print(f"ElevenLabs /voices: {err}", file=sys.stderr)
    return DEFAULT_VOICE, "stock fallback"


def play_host_audio(audio_bytes=None, text=""):
    """Plays audio directly through the host computer's speakers (macOS / Linux / Pi)."""
    def _run():
        if audio_bytes:
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    f.write(audio_bytes)
                    tmp = f.name
                if platform.system() == "Darwin" and shutil.which("afplay"):
                    subprocess.run(["afplay", tmp], check=False)
                elif shutil.which("mpv"):
                    subprocess.run(["mpv", "--no-terminal", tmp], check=False)
                elif shutil.which("ffplay"):
                    subprocess.run(["ffplay", "-nodisp", "-autoexit", tmp], check=False)
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        elif text:
            if platform.system() == "Darwin" and shutil.which("say"):
                subprocess.run(["say", text], check=False)
            elif shutil.which("espeak-ng"):
                subprocess.run(["espeak-ng", text], check=False)
    threading.Thread(target=_run, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(PAGE, "text/html; charset=utf-8")
        elif self.path == "/fallback-image":
            if self.server.fallback_image:
                self._send(self.server.fallback_image, "image/jpeg")
            else:
                self.send_error(404)
        elif self.path == "/status":
            self._json({
                "gemini_model": self.server.gemini.model,
                "gemini_key": bool(self.server.gemini.key),
                "el_voice": self.server.el_voice,
                "el_voice_source": self.server.el_voice_source,
                "el_key": bool(self.server.el_key),
                "el_model": self.server.el_model,
                "ros_camera": bool(self.server.ros_camera),
                "camera_status": (self.server.ros_camera.status()
                                  if self.server.ros_camera else "Not connected"),
                "fallback_image": bool(self.server.fallback_image),
                "fallback_image_bytes": (len(self.server.fallback_image)
                                         if self.server.fallback_image else 0),
            })
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/ask":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        play_host = body.get("play_host", True) and not self.server.ros_camera
        log = []
        t_start = time.monotonic()

        def note(msg, kind="info"):
            entry = {"t": round(time.monotonic() - t_start, 3),
                     "msg": msg, "kind": kind}
            log.append(entry)
            print(f"[{time.strftime('%H:%M:%S')}] [{kind.upper():4}] {msg}", flush=True)

        # ---- audio ----
        audio_wav = base64.b64decode(body["audio"]) if body.get("audio") else b""
        if audio_wav:
            note(f"Audio received: {len(audio_wav):,} bytes")
        else:
            note("No audio received", "warn")

        # ---- image ----
        if self.server.ros_camera:
            try:
                image_jpeg, _captured_at = self.server.ros_camera.capture()
                note(f"Pi ROS camera frame: {len(image_jpeg):,} bytes")
            except AppError as err:
                image_jpeg = None
                note(f"Pi camera unavailable: {err}", "warn")
        elif body.get("image"):
            image_jpeg = base64.b64decode(body["image"])
            note(f"Webcam frame: {len(image_jpeg):,} bytes")
        elif self.server.fallback_image:
            image_jpeg = self.server.fallback_image
            note(f"Using fallback image: {len(image_jpeg):,} bytes")
        else:
            image_jpeg = None
            note("No image available", "warn")

        # ---- Gemini ----
        note(f"Calling Gemini ({self.server.gemini.model})…")
        gemini_t0 = time.monotonic()
        try:
            result = self.server.gemini.ask(audio_wav, image_jpeg, [])
            gemini_s = round(time.monotonic() - gemini_t0, 2)
            note(f"Gemini OK — {gemini_s}s", "pass")
            note(f"  heard: {result['transcript']!r}")
            note(f"  answer: {result['answer']!r}")
            if result["landmark"]:
                note(f"  landmark: {result['landmark']!r}")
            note(f"  action: {result['device_action']}")
        except AppError as err:
            gemini_s = round(time.monotonic() - gemini_t0, 2)
            note(f"Gemini FAIL ({gemini_s}s): {err}", "fail")
            if play_host:
                play_host_audio(text="I'm sorry, I encountered an error. Please try again.")
            self._json({"error": str(err), "log": log})
            return

        resp = {
            "transcript": result["transcript"],
            "answer": result["answer"],
            "landmark": result["landmark"],
            "device_action": result["device_action"],
            "gemini_time": gemini_s,
        }
        if result["device_action"] == "navigate_backpack":
            if self.server.guidance:
                resp["answer"] = self.server.guidance.start()
            else:
                resp["answer"] = "Backpack guidance is unavailable here."
            note(resp["answer"])
        elif result["device_action"] == "stop_navigation":
            if self.server.guidance:
                self.server.guidance.stop()
            resp["answer"] = "Guidance stopped."
            note(resp["answer"])

        # ---- ElevenLabs ----
        audio = None
        if self.server.el_key and self.server.el_voice:
            note(f"Calling ElevenLabs (voice {self.server.el_voice[:8]}…, "
                 f"model {self.server.el_model})…")
            el_t0 = time.monotonic()
            audio, el_first, el_err = self._synthesize(resp["answer"])
            el_total = round(time.monotonic() - el_t0, 2)
            if audio:
                resp["audio"] = base64.b64encode(audio).decode()
                resp["el_first_byte"] = el_first
                resp["el_total"] = el_total
                note(f"ElevenLabs OK — first byte {el_first}s, "
                     f"total {el_total}s, {len(audio):,} bytes", "pass")
            else:
                resp["audio_error"] = el_err
                note(f"ElevenLabs FAIL ({el_total}s): {el_err}", "fail")
        else:
            note("ElevenLabs skipped — no key or voice", "warn")

        # ---- Host speaker playback (Mac / Pi) ----
        if play_host:
            if audio:
                play_host_audio(audio_bytes=audio)
                note("Playing audio through host speaker (afplay/mpv)", "pass")
            else:
                play_host_audio(text=resp["answer"])
                note("Speaking answer through host speaker (system TTS)", "pass")

        total = round(time.monotonic() - t_start, 2)
        note(f"Total round trip: {total}s", "pass" if total < 5 else "warn")
        resp["total_time"] = total
        resp["log"] = log
        self._json(resp)

    def _synthesize(self, text):
        """Returns (audio_bytes | None, first_byte_s, error_string)."""
        url = (ELEVENLABS_API + "/text-to-speech/" + self.server.el_voice + "?"
               + urlencode({"output_format": "mp3_22050_32"}))
        req = Request(url,
                      data=json.dumps({"text": text,
                                       "model_id": self.server.el_model}).encode(),
                      headers={"xi-api-key": self.server.el_key,
                               "Content-Type": "application/json"})
        try:
            t0 = time.monotonic()
            with urlopen(req, timeout=20) as r:
                first_chunk = r.read(4096)
                first_s = round(time.monotonic() - t0, 2)
                rest = r.read()
            return first_chunk + rest, first_s, None
        except HTTPError as err:
            detail = err.read()[:300].decode("utf-8", "replace")
            return None, 0, f"{err.code}: {detail}"
        except (URLError, TimeoutError) as err:
            return None, 0, str(err)

    def _send(self, data, ctype):
        body = data if isinstance(data, bytes) else data.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data):
        self._send(json.dumps(data), "application/json")

    def log_message(self, fmt, *args):
        pass


# ---------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Voice Companion — Hardware Simulator & Debug Console</title>
<meta name="description" content="Push-to-talk debug interface simulating the Raspberry Pi voice companion">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#06060b;--bg2:#0d0d14;--card:rgba(255,255,255,0.03);
  --border:rgba(255,255,255,0.06);--border2:rgba(255,255,255,0.12);
  --text:#e0e0e6;--dim:#5a5a64;--dim2:#3a3a44;
  --accent:#4ecdc4;--accent2:#3bb8b0;--accent-g:rgba(78,205,196,0.25);
  --rec:#ff6b6b;--rec-g:rgba(255,107,107,0.35);
  --think:#ffd93d;--think-g:rgba(255,217,61,0.25);
  --pass:#4ecdc4;--fail:#ff6b6b;--warn:#ffd93d;--info:#8e8e93;
  --r:12px;--font:'Inter',system-ui,sans-serif;
  --mono:'JetBrains Mono','SF Mono',monospace;
}
html,body{height:100%}
body{
  font-family:var(--font);background:var(--bg);color:var(--text);
  display:grid;grid-template-columns:1fr 1fr;grid-template-rows:auto 1fr;
  gap:0;height:100vh;overflow:hidden;
}
@media(max-width:900px){body{grid-template-columns:1fr;grid-template-rows:auto auto 1fr;overflow:auto}}

/* ---- header ---- */
header{
  grid-column:1/-1;padding:.8rem 1.2rem;
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:1rem;
  background:var(--bg2);
}
header h1{
  font-size:1.05rem;font-weight:600;letter-spacing:-.01em;
  background:linear-gradient(135deg,var(--accent),#8b5cf6);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
header .tag{
  font-size:.65rem;padding:2px 8px;border-radius:6px;
  border:1px solid var(--border2);color:var(--dim);
  font-family:var(--mono);
}
.header-actions{margin-left:auto;display:flex;align-items:center;gap:.8rem}
.sound-test-btn{
  background:rgba(78,205,196,0.1);border:1px solid var(--accent);
  color:var(--accent);font-size:.7rem;padding:3px 10px;border-radius:6px;
  cursor:pointer;font-family:var(--mono);transition:all .15s;
}
.sound-test-btn:hover{background:var(--accent);color:#06060b}
.header-dots{display:flex;gap:.5rem;align-items:center}
.dot{width:8px;height:8px;border-radius:50%;background:var(--dim2);transition:background .2s}
.dot.ok{background:var(--pass);box-shadow:0 0 6px var(--pass)}
.dot.err{background:var(--fail)}
.dot-label{font-size:.65rem;color:var(--dim);font-family:var(--mono);margin-right:.25rem}

/* ---- left: interaction ---- */
.interact{
  display:flex;flex-direction:column;align-items:center;
  padding:1.4rem 1rem;overflow-y:auto;
  border-right:1px solid var(--border);
}
@media(max-width:900px){.interact{border-right:none;border-bottom:1px solid var(--border);padding:1rem}}

.cam{
  width:100%;max-width:420px;aspect-ratio:4/3;border-radius:var(--r);
  overflow:hidden;border:1px solid var(--border);background:#0a0a0f;
  position:relative;margin-bottom:1.1rem;
}
.cam video,.cam img{width:100%;height:100%;object-fit:cover;display:block}
.cam-tag{
  position:absolute;top:8px;left:8px;background:rgba(0,0,0,.65);
  backdrop-filter:blur(6px);padding:2px 8px;border-radius:6px;
  font-size:.62rem;color:#aaa;text-transform:uppercase;letter-spacing:.05em;
  font-family:var(--mono);
}
.meter-wrap{position:absolute;bottom:0;left:0;right:0;height:3px;background:rgba(0,0,0,.4)}
.meter-bar{height:100%;width:0%;background:var(--rec);transition:width 50ms}

/* audio destination options */
.audio-bar{
  width:100%;max-width:420px;display:flex;justify-content:space-between;align-items:center;
  background:rgba(255,255,255,0.02);border:1px solid var(--border);
  border-radius:8px;padding:6px 12px;margin-bottom:1.1rem;font-size:.72rem;color:var(--dim);
}
.audio-bar label{display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none}
.audio-bar input[type="checkbox"]{accent-color:var(--accent);cursor:pointer}

.btn-area{display:flex;flex-direction:column;align-items:center;gap:.6rem;margin-bottom:1.2rem}
.talk{
  width:82px;height:82px;border-radius:50%;border:2px solid var(--accent);
  background:var(--card);color:var(--accent);cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  transition:all .2s;-webkit-user-select:none;user-select:none;touch-action:none;outline:none;
}
.talk:hover{background:rgba(78,205,196,.1);box-shadow:0 0 25px var(--accent-g)}
.talk.rec{border-color:var(--rec);color:var(--rec);background:rgba(255,107,107,.08);box-shadow:0 0 35px var(--rec-g);animation:pulse-r 1.4s ease-in-out infinite}
.talk.think{border-color:var(--think);color:var(--think);background:rgba(255,217,61,.08);box-shadow:0 0 25px var(--think-g);animation:pulse-t 1.8s ease-in-out infinite;pointer-events:none}
@keyframes pulse-r{0%,100%{transform:scale(1);box-shadow:0 0 24px var(--rec-g)}50%{transform:scale(1.06);box-shadow:0 0 44px var(--rec-g)}}
@keyframes pulse-t{0%,100%{box-shadow:0 0 16px var(--think-g)}50%{box-shadow:0 0 32px var(--think-g)}}
.talk svg{width:30px;height:30px;fill:currentColor}
.talk .spin{display:none}
.talk.think svg.mic{display:none}
.talk.think .spin{display:block;width:26px;height:26px;border:2.5px solid var(--think);border-top-color:transparent;border-radius:50%;animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.status-text{font-size:.82rem;color:#8e8e93;text-align:center;font-weight:400}
.rec-timer{font-family:var(--mono);font-size:.72rem;color:var(--dim);font-variant-numeric:tabular-nums;min-height:1.1em}

/* ---- response card ---- */
.resp{
  width:100%;max-width:420px;background:var(--card);
  border:1px solid var(--border);border-radius:var(--r);padding:1.1rem;
  opacity:0;transform:translateY(6px);transition:all .3s ease;pointer-events:none;
}
.resp.vis{opacity:1;transform:translateY(0);pointer-events:auto}
.resp .f{margin-bottom:.8rem}
.resp .f:last-child{margin-bottom:0}
.resp label{display:block;font-size:.62rem;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin-bottom:.25rem;font-family:var(--mono)}
.resp p{font-size:.9rem;line-height:1.45}
.resp .lm{color:var(--accent);font-size:.84rem}
.resp .timings{display:flex;gap:.6rem;flex-wrap:wrap}
.resp .timings span{font-family:var(--mono);font-size:.7rem;color:var(--dim);padding:2px 7px;background:rgba(255,255,255,.03);border-radius:5px;border:1px solid var(--border)}
.resp .timings span b{color:var(--text);font-weight:500}

/* audio player in response */
.audio-player-row{
  display:flex;align-items:center;gap:.6rem;margin-top:.3rem;
}
.audio-player-row audio{
  flex:1;height:32px;filter:invert(0.9) hue-rotate(180deg);
}
.replay-btn{
  background:rgba(78,205,196,0.12);border:1px solid var(--accent);
  color:var(--accent);font-size:.72rem;padding:5px 12px;border-radius:6px;
  cursor:pointer;font-family:var(--mono);transition:all .15s;white-space:nowrap;
}
.replay-btn:hover{background:var(--accent);color:#06060b}
.audio-status{font-size:.72rem;color:var(--dim);margin-top:.4rem;font-family:var(--mono)}

/* ---- right: debug log ---- */
.debug{
  display:flex;flex-direction:column;background:var(--bg2);overflow:hidden;
}
.debug-header{
  padding:.7rem 1rem;border-bottom:1px solid var(--border);
  font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);
  font-family:var(--mono);display:flex;align-items:center;gap:.6rem;
}
.debug-header .clear-btn{
  margin-left:auto;background:none;border:1px solid var(--border);
  color:var(--dim);font-size:.62rem;padding:2px 8px;border-radius:5px;
  cursor:pointer;font-family:var(--mono);
}
.debug-header .clear-btn:hover{border-color:var(--border2);color:var(--text)}
.log-scroll{flex:1;overflow-y:auto;padding:.5rem 0}
.log-line{
  padding:2px 1rem;font-family:var(--mono);font-size:.72rem;line-height:1.7;
  display:flex;gap:.6rem;
}
.log-line:hover{background:rgba(255,255,255,.02)}
.log-t{color:var(--dim2);min-width:48px;text-align:right;flex-shrink:0}
.log-tag{min-width:36px;text-align:center;flex-shrink:0;font-weight:500;border-radius:3px;padding:0 4px}
.log-tag.pass{color:var(--pass)}
.log-tag.fail{color:var(--fail)}
.log-tag.warn{color:var(--warn)}
.log-tag.info{color:var(--info)}
.log-msg{color:var(--text);word-break:break-word}
.log-msg.fail{color:var(--fail)}
.log-msg.warn{color:var(--warn)}
.log-line.fail-line{background:rgba(255,107,107,.05)}
</style>
</head>
<body>

<header>
  <h1>Voice Companion</h1>
  <span class="tag" id="model-tag">…</span>
  <div class="header-actions">
    <button class="sound-test-btn" id="btn-sound-test" title="Play test sound & speech">🔊 Test Sound</button>
    <div class="header-dots">
      <span class="dot-label">Gemini</span><span class="dot" id="dot-gemini"></span>
      <span class="dot-label">11Labs</span><span class="dot" id="dot-el"></span>
      <span class="dot-label">Mic</span><span class="dot" id="dot-mic"></span>
      <span class="dot-label">Cam</span><span class="dot" id="dot-cam"></span>
    </div>
  </div>
</header>

<!-- left panel -->
<div class="interact">
  <div class="cam" id="cam-box">
    <span class="cam-tag" id="cam-tag">…</span>
    <video id="webcam" autoplay playsinline muted style="display:none"></video>
    <img id="fallback" src="/fallback-image" alt="" style="display:none">
    <canvas id="snap" style="display:none"></canvas>
    <div class="meter-wrap"><div class="meter-bar" id="meter"></div></div>
  </div>

  <div class="audio-bar">
    <label title="Play audio via Web Audio / HTML5 in this browser tab"><input type="checkbox" id="opt-browser-audio" checked> Browser Audio</label>
    <label title="Play audio directly on host computer speaker (Mac/Pi)"><input type="checkbox" id="opt-host-audio" checked> Host Speaker</label>
    <label title="Play Pi hardware earcons (sound effects)"><input type="checkbox" id="opt-earcons" checked> Earcons</label>
  </div>

  <div class="btn-area">
    <button class="talk" id="btn" disabled title="Hold to talk, release to send">
      <svg class="mic" viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
      <div class="spin"></div>
    </button>
    <span class="status-text" id="status">Initializing…</span>
    <span class="rec-timer" id="timer"></span>
  </div>

  <div class="resp" id="resp">
    <div class="f"><label>Heard</label><p id="r-heard"></p></div>
    <div class="f"><label>Answer</label><p id="r-answer"></p></div>
    <div class="f" id="r-lm-wrap" style="display:none"><label>Landmark</label><p id="r-lm" class="lm"></p></div>
    <div class="f" id="r-audio-wrap" style="display:none">
      <label>Voice Output</label>
      <div class="audio-player-row">
        <audio id="audio-player" controls preload="auto"></audio>
        <button class="replay-btn" id="replay-btn" title="Replay voice">🔊 Replay</button>
      </div>
      <div class="audio-status" id="audio-status"></div>
    </div>
    <div class="f"><label>Latency</label><div class="timings" id="r-timings"></div></div>
  </div>
</div>

<!-- right panel: debug log -->
<div class="debug">
  <div class="debug-header">
    <span>Debug Log</span>
    <button class="clear-btn" id="clear-log">Clear</button>
  </div>
  <div class="log-scroll" id="log-scroll"></div>
</div>

<script>
const $ = id => document.getElementById(id);
const btn=$('btn'), statusEl=$('status'), timerEl=$('timer'),
      webcamEl=$('webcam'), fallback=$('fallback'), camTag=$('cam-tag'),
      snap=$('snap'), meterBar=$('meter'), respCard=$('resp'),
      logScroll=$('log-scroll'), audioPlayer=$('audio-player'),
      replayBtn=$('replay-btn'), audioStatus=$('audio-status'),
      optBrowserAudio=$('opt-browser-audio'), optHostAudio=$('opt-host-audio'),
      optEarcons=$('opt-earcons'), btnSoundTest=$('btn-sound-test');

let mediaStream=null, audioCtx=null, hasWebcam=false;
let recording=false, pressed=false;
let recSource=null, recProc=null, recChunks=[], recStart=0, timerRaf=0;
let analyser=null, lastVoiceData={audio:null, answer:''};
let activeWebAudioSource=null;

function getAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
  return audioCtx;
}

/* ================ Earcons ================ */
function playEarcon(type) {
  if (!optEarcons.checked) return;
  try {
    const ctx = getAudioCtx();
    const t = ctx.currentTime;
    if (type === 'listening') {
      // 660 -> 880 Hz double chime
      const o1 = ctx.createOscillator(), g1 = ctx.createGain();
      o1.type = 'sine'; o1.frequency.setValueAtTime(660, t);
      o1.connect(g1); g1.connect(ctx.destination);
      g1.gain.setValueAtTime(0.12, t);
      g1.gain.exponentialRampToValueAtTime(0.001, t + 0.08);
      o1.start(t); o1.stop(t + 0.08);

      const o2 = ctx.createOscillator(), g2 = ctx.createGain();
      o2.type = 'sine'; o2.frequency.setValueAtTime(880, t + 0.09);
      o2.connect(g2); g2.connect(ctx.destination);
      g2.gain.setValueAtTime(0.12, t + 0.09);
      g2.gain.exponentialRampToValueAtTime(0.001, t + 0.22);
      o2.start(t + 0.09); o2.stop(t + 0.22);
    } else if (type === 'thinking') {
      // 520 Hz gentle tone
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine'; o.frequency.setValueAtTime(520, t);
      o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.09, t);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.18);
      o.start(t); o.stop(t + 0.18);
    } else if (type === 'error') {
      // 300 -> 220 Hz low buzz
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'triangle'; o.frequency.setValueAtTime(300, t);
      o.frequency.linearRampToValueAtTime(220, t + 0.25);
      o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.15, t);
      g.gain.exponentialRampToValueAtTime(0.001, t + 0.25);
      o.start(t); o.stop(t + 0.25);
    }
  } catch(e) {}
}

/* ================ debug log ================ */
function log(msg, kind='info') {
  const ts = new Date().toLocaleTimeString('en-US',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const line = document.createElement('div');
  line.className = 'log-line' + (kind==='fail'?' fail-line':'');
  const tagLabels = {pass:'PASS',fail:'FAIL',warn:'WARN',info:'····'};
  line.innerHTML = `<span class="log-t">${ts}</span>`
    + `<span class="log-tag ${kind}">${tagLabels[kind]||'····'}</span>`
    + `<span class="log-msg ${kind}">${esc(msg)}</span>`;
  logScroll.appendChild(line);
  logScroll.scrollTop = logScroll.scrollHeight;
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function addServerLogs(entries) {
  for (const e of entries) {
    const prefix = `[+${e.t.toFixed(2)}s] `;
    log(prefix + e.msg, e.kind);
  }
}

$('clear-log').onclick = () => { logScroll.innerHTML = ''; };

/* ================ status dots ================ */
function dot(id, ok) {
  const el = $('dot-'+id);
  if (el) el.className = 'dot ' + (ok ? 'ok' : 'err');
}

/* ================ sound test button ================ */
btnSoundTest.onclick = async () => {
  getAudioCtx();
  playEarcon('listening');
  log('Testing sound output…', 'info');
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance('Voice test. Audio output is working properly.');
    u.rate = 1.0;
    u.onstart = () => log('Sound test: SpeechSynthesis speaking', 'pass');
    u.onerror = (e) => log('Sound test: SpeechSynthesis error: ' + e.error, 'warn');
    window.speechSynthesis.speak(u);
  } else {
    log('Browser speech synthesis is not supported', 'warn');
  }
};

/* ================ init ================ */
async function init() {
  log('Starting up…');
  let serverStatus = null;

  // Fetch server status
  try {
    const r = await fetch('/status');
    const s = await r.json();
    serverStatus = s;
    $('model-tag').textContent = s.gemini_model;
    dot('gemini', s.gemini_key);
    dot('el', s.el_key);
    log(`Gemini model: ${s.gemini_model}, key: ${s.gemini_key?'set':'MISSING'}`,
        s.gemini_key?'pass':'fail');
    log(`ElevenLabs voice: ${s.el_voice||'none'} (${s.el_voice_source}), key: ${s.el_key?'set':'MISSING'}`,
        s.el_key?'pass':(s.el_key===false?'warn':'fail'));
    if (s.ros_camera)
      log(`Pi ROS camera: ${s.camera_status}`, s.camera_status==='Ready'?'pass':'warn');
    else if (s.fallback_image)
      log(`Fallback image: ${(s.fallback_image_bytes/1e6).toFixed(1)} MB`);
    else
      log('No fallback image — use --image or allow webcam', 'warn');
  } catch(e) {
    log('Failed to reach server: '+e.message, 'fail');
  }

  // Camera
  if (serverStatus && serverStatus.ros_camera) {
    fallback.style.display = 'block';
    camTag.textContent = 'Pi camera';
    dot('cam', serverStatus.camera_status === 'Ready');
  } else try {
    const stream = await navigator.mediaDevices.getUserMedia({video:{width:640,height:480}});
    webcamEl.srcObject = stream;
    webcamEl.style.display = 'block';
    fallback.style.display = 'none';
    camTag.textContent = 'live';
    hasWebcam = true;
    dot('cam', true);
    log('Webcam: active', 'pass');
  } catch(e) {
    fallback.style.display = 'block';
    camTag.textContent = 'image';
    dot('cam', true);
    log('Webcam unavailable — using fallback image', 'warn');
  }

  // Mic
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1}});
    dot('mic', true);
    btn.disabled = false;
    statusEl.textContent = 'Hold to talk (or hold Space)';
    log('Microphone: active', 'pass');
  } catch(e) {
    dot('mic', false);
    statusEl.textContent = 'Microphone blocked';
    log('Microphone access denied — enable in browser settings', 'fail');
  }

  log('Ready.', 'pass');
}

/* ================ camera capture ================ */
function captureFrame() {
  if (!hasWebcam) return '';
  const c=snap, ctx=c.getContext('2d');
  c.width=webcamEl.videoWidth||640; c.height=webcamEl.videoHeight||480;
  ctx.drawImage(webcamEl,0,0);
  return c.toDataURL('image/jpeg',0.8).split(',')[1];
}

/* ================ recording ================ */
function startRec() {
  const ctx = getAudioCtx();
  recChunks = [];
  recSource = ctx.createMediaStreamSource(mediaStream);
  recProc = ctx.createScriptProcessor(4096,1,1);
  analyser = ctx.createAnalyser(); analyser.fftSize=256;
  recSource.connect(analyser);
  recProc.onaudioprocess = e => {
    if(recording) recChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };
  recSource.connect(recProc);
  recProc.connect(ctx.destination);
  recording = true;
  recStart = performance.now();
  tickTimer(); tickMeter();
}

function stopRec() {
  recording = false;
  cancelAnimationFrame(timerRaf);
  if(recProc) recProc.disconnect();
  if(recSource) recSource.disconnect();
  meterBar.style.width='0%';

  const total = recChunks.reduce((a,c)=>a+c.length,0);
  const pcm = new Float32Array(total);
  let off=0;
  for(const c of recChunks){pcm.set(c,off);off+=c.length}

  let peak=0;
  for(let i=0;i<pcm.length;i++) peak=Math.max(peak,Math.abs(pcm[i]));
  const peakI16 = Math.round(peak * 32767);
  const durS = (total / (audioCtx.sampleRate || 16000)).toFixed(1);
  log(`Recorded ${durS}s, peak ${peakI16}, ${total} samples @ ${audioCtx.sampleRate} Hz`);

  if (peakI16 < 50) {
    log('Recording is near-silent — check microphone', 'warn');
  }

  const i16 = new Int16Array(pcm.length);
  for(let i=0;i<pcm.length;i++){const s=Math.max(-1,Math.min(1,pcm[i]));i16[i]=s<0?s*0x8000:s*0x7FFF}
  return wavEncode(i16, audioCtx.sampleRate);
}

/* ================ WAV ================ */
function wavEncode(samples,rate){
  const buf=new ArrayBuffer(44+samples.length*2);const v=new DataView(buf);
  const ws=(o,s)=>{for(let i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i))};
  ws(0,'RIFF');v.setUint32(4,36+samples.length*2,true);ws(8,'WAVE');
  ws(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);
  v.setUint16(22,1,true);v.setUint32(24,rate,true);
  v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);
  ws(36,'data');v.setUint32(40,samples.length*2,true);
  for(let i=0;i<samples.length;i++)v.setInt16(44+i*2,samples[i],true);
  return buf;
}
function toB64(buf){const u=new Uint8Array(buf);let b='';for(let i=0;i<u.length;i++)b+=String.fromCharCode(u[i]);return btoa(b)}

/* ================ UI helpers ================ */
function tickTimer(){
  if(!recording) return;
  timerEl.textContent=((performance.now()-recStart)/1000).toFixed(1)+'s';
  timerRaf=requestAnimationFrame(tickTimer);
}
function tickMeter(){
  if(!recording){meterBar.style.width='0%';return}
  if(analyser){
    const d=new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteTimeDomainData(d);
    let pk=0;for(const v of d)pk=Math.max(pk,Math.abs(v-128));
    meterBar.style.width=Math.min(100,pk/128*100*1.5)+'%';
  }
  requestAnimationFrame(tickMeter);
}

/* ================ Voice Output Pipeline ================ */
async function playVoiceResponse(b64Audio, answerText) {
  lastVoiceData = {audio: b64Audio, answer: answerText};
  const audioWrap = $('r-audio-wrap');
  audioWrap.style.display = '';

  if (!optBrowserAudio.checked) {
    audioStatus.textContent = 'Browser audio disabled (playing on host speaker only)';
    log('Browser audio skipped (disabled in options)', 'info');
    return;
  }

  if (b64Audio) {
    try {
      const raw = atob(b64Audio);
      const arr = new Uint8Array(raw.length);
      for(let i=0; i<raw.length; i++) arr[i] = raw.charCodeAt(i);
      const blob = new Blob([arr], {type: 'audio/mpeg'});
      const blobUrl = URL.createObjectURL(blob);
      audioPlayer.src = blobUrl;

      // Play through unlocked Web Audio API (immune to autoplay restrictions)
      const ctx = getAudioCtx();
      let playedWebAudio = false;
      if (ctx) {
        try {
          if (ctx.state === 'suspended') await ctx.resume();
          const copy = arr.slice(0).buffer;
          const decoded = await ctx.decodeAudioData(copy);
          if (activeWebAudioSource) {
            try { activeWebAudioSource.stop(); } catch(e){}
          }
          activeWebAudioSource = ctx.createBufferSource();
          activeWebAudioSource.buffer = decoded;
          activeWebAudioSource.connect(ctx.destination);
          activeWebAudioSource.onended = () => {
            audioStatus.textContent = 'Voice playback complete ⏹️';
          };
          activeWebAudioSource.start(0);
          playedWebAudio = true;
          audioStatus.textContent = 'Playing voice output (Web Audio 🔊)…';
          log('Playing voice via Web Audio API 🔊', 'pass');
        } catch(decodeErr) {
          log('Web Audio decode notice: ' + decodeErr.message, 'info');
        }
      }

      // If Web Audio didn't handle it, try HTML5 Audio
      if (!playedWebAudio) {
        audioPlayer.play().then(() => {
          audioStatus.textContent = 'Playing voice output (HTML5 🔊)…';
          log('Playing voice via HTML5 Audio 🔊', 'pass');
        }).catch(err => {
          log('Browser autoplay blocked: ' + err.message, 'warn');
          audioStatus.innerHTML = '<span style="color:var(--warn)">Autoplay was blocked by browser. <b>Click ▶ Play button above!</b></span>';
        });
      }
      dot('el', true);
    } catch(err) {
      log('Audio playback error: ' + err.message, 'fail');
      speakFallback(answerText);
    }
  } else if (answerText) {
    speakFallback(answerText);
  }
}

function speakFallback(text) {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0;
    u.onstart = () => { audioStatus.textContent = 'Speaking via browser speech synthesis 🔊…'; };
    u.onend = () => { audioStatus.textContent = 'Voice playback complete ⏹️'; };
    window.speechSynthesis.speak(u);
    log('Spoken aloud via browser SpeechSynthesis fallback', 'pass');
  } else {
    audioStatus.textContent = 'No voice audio returned and speech synthesis unavailable';
  }
}

replayBtn.onclick = () => {
  if (lastVoiceData.audio || lastVoiceData.answer) {
    log('Replaying voice output…');
    playVoiceResponse(lastVoiceData.audio, lastVoiceData.answer);
  }
};

/* ================ button handlers ================ */
async function onDown(e){
  e.preventDefault();
  if(pressed||btn.classList.contains('think')) return;

  // Stop any ongoing speech
  if(activeWebAudioSource){
    try{activeWebAudioSource.stop()}catch(e){}
    activeWebAudioSource=null;
    log('Barge-in — stopped active audio');
  }
  if(audioPlayer){audioPlayer.pause()}
  if('speechSynthesis' in window){window.speechSynthesis.cancel()}

  getAudioCtx();
  playEarcon('listening');

  pressed=true;
  btn.classList.add('rec');
  statusEl.textContent='Listening…';
  log('Button pressed — recording started');
  startRec();
}

async function onUp(e){
  e.preventDefault();
  if(!pressed||!recording) return;
  pressed=false;
  btn.classList.remove('rec');
  btn.classList.add('think');
  statusEl.textContent='Thinking…';
  timerEl.textContent='';

  playEarcon('thinking');

  const wav=stopRec();
  const img=captureFrame();
  log('Sending to server…');

  try{
    const t0=performance.now();
    const r=await fetch('/ask',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        audio:toB64(wav),
        image:img,
        play_host:optHostAudio.checked
      })
    });
    const d=await r.json();
    btn.classList.remove('think');

    // replay server logs
    if(d.log) addServerLogs(d.log);

    if(d.error){
      statusEl.textContent=d.error;
      dot('gemini',false);
      playEarcon('error');
      log('Request failed: '+d.error,'fail');
      return;
    }
    dot('gemini',true);
    statusEl.textContent='Hold to talk (or hold Space)';

    // response card
    $('r-heard').textContent=d.transcript||'(empty)';
    $('r-answer').textContent=d.answer;
    const lmW=$('r-lm-wrap');
    if(d.landmark){$('r-lm').textContent=d.landmark;lmW.style.display=''}
    else lmW.style.display='none';

    // timings
    const tDiv=$('r-timings');
    tDiv.innerHTML='';
    const add=(l,v)=>{const s=document.createElement('span');s.innerHTML=`${l} <b>${v}</b>`;tDiv.appendChild(s)};
    if(d.gemini_time) add('Gemini',d.gemini_time+'s');
    if(d.el_first_byte!=null) add('11Labs 1st byte',d.el_first_byte+'s');
    if(d.el_total) add('11Labs total',d.el_total+'s');
    if(d.total_time) add('Total',d.total_time+'s');
    respCard.classList.add('vis');

    // play voice output
    playVoiceResponse(d.audio, d.answer);

  }catch(err){
    btn.classList.remove('think');
    statusEl.textContent='Network error';
    playEarcon('error');
    log('Fetch error: '+err.message,'fail');
  }
}

function onCancel(e){
  if(!pressed)return;
  pressed=false;recording=false;
  cancelAnimationFrame(timerRaf);
  if(recProc)recProc.disconnect();
  if(recSource)recSource.disconnect();
  btn.classList.remove('rec');
  statusEl.textContent='Cancelled — hold to talk';
  timerEl.textContent='';meterBar.style.width='0%';
  log('Recording cancelled');
}

btn.addEventListener('pointerdown',onDown);
btn.addEventListener('pointerup',onUp);
btn.addEventListener('pointerleave',onCancel);
btn.addEventListener('pointercancel',onCancel);
btn.addEventListener('contextmenu',e=>e.preventDefault());

/* keyboard: hold Space */
document.addEventListener('keydown',e=>{
  if(e.code==='Space'&&!e.repeat&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){
    e.preventDefault();onDown(e);
  }
});
document.addEventListener('keyup',e=>{
  if(e.code==='Space'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){
    e.preventDefault();onUp(e);
  }
});

init();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(
        prog="python3 -m companion.voice.web_test",
        description="Web debug console for the voice companion.")
    parser.add_argument("--image", help="Fallback JPEG when no webcam is available")
    parser.add_argument("--ros", action="store_true",
                        help="Use the Pi ROS camera and backpack guidance with a laptop browser")
    parser.add_argument("--topic", default="/camera/color/image_raw")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", default=None,
                        help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open the browser")
    args = parser.parse_args()

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    el_key = os.environ.get("ELEVENLABS_API_KEY", "")

    if not gemini_key:
        print("⚠  GEMINI_API_KEY is not set.", file=sys.stderr)
    if not el_key:
        print("⚠  ELEVENLABS_API_KEY is not set.", file=sys.stderr)

    el_voice, el_source = resolve_voice(el_key)

    chosen_model = args.model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    server = HTTPServer((args.host, args.port), Handler)
    server.gemini = Gemini(gemini_key, chosen_model)
    server.el_key = el_key
    server.el_voice = el_voice
    server.el_voice_source = el_source
    server.el_model = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")
    server.fallback_image = None
    server.ros_camera = None
    server.guidance = None
    if args.ros:
        from companion.ros_camera import RosCamera
        from companion.voice.guidance import RosGuidance
        server.ros_camera = RosCamera(args.topic)
        server.guidance = RosGuidance()

    if args.image:
        path = Path(args.image)
        if path.is_file():
            server.fallback_image = path.read_bytes()
            print(f"Fallback image: {path} ({len(server.fallback_image):,} bytes)")
        else:
            print(f"Warning: {path} not found", file=sys.stderr)

    url = f"http://localhost:{args.port}"
    print(f"Listening on {url}")
    print(f"Gemini model: {server.gemini.model}")
    print(f"ElevenLabs voice: {el_voice} ({el_source})")
    print("Voice output: browser, plus host speaker in local image mode")

    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        if server.guidance:
            server.guidance.close()
        if server.ros_camera:
            server.ros_camera.close()


if __name__ == "__main__":
    main()
