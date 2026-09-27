"""Web testing interface that simulates the Raspberry Pi voice companion.

Run:
    python3 -m companion.voice.web_test --image scene.jpeg

Opens http://localhost:8080/ for the unified dashboard, object finder and Guardian.
Scene answers play in the browser; Guardian uses the laptop audio owner.
/demo is an alias for the same dashboard.
"""
import argparse
import base64
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from companion.errors import AppError
from companion.voice.gemini import Gemini, DEFAULT_MODEL, optimize_image

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


def play_host_audio(audio_bytes=None, text="", speed=1.15):
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
                rate = str(int(175 * speed))
                subprocess.run(["say", "-r", rate, text], check=False)
            elif shutil.which("espeak-ng"):
                rate = str(int(160 * speed))
                subprocess.run(["espeak-ng", "-s", rate, text], check=False)
    threading.Thread(target=_run, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/demo", "/demo/"):
            return self._send(PAGE, "text/html; charset=utf-8")
        elif self.path == "/dashboard-controls.js":
            return self._send((Path(__file__).parent.parent / "dashboard" / "controls.js").read_text(), "text/javascript; charset=utf-8")
        elif self.path == "/dashboard-warnings.js":
            return self._send((Path(__file__).parent.parent / "dashboard" / "warnings.js").read_text(), "text/javascript; charset=utf-8")
        elif self.path == "/demo/state":
            return self._json(self.server.demo.snapshot(touch=True))
        elif self.path in ("/", "/index.html"):
            self._send(PAGE, "text/html; charset=utf-8")
        elif self.path == "/fallback-image":
            if self.server.fallback_image:
                self._send(self.server.fallback_image, "image/jpeg")
            else:
                self.send_error(404)
        elif self.path.startswith("/pi-frame?") and self.server.ros_camera:
            try:
                image, _captured_at = self.server.ros_camera.capture()
                self._send(image, "image/jpeg")
            except AppError:
                self.send_error(503, "No recent Pi camera frame")
        elif self.path.startswith("/guidance/events?") and self.server.guidance:
            try:
                since = int(self.path.split("since=", 1)[1].split("&")[0])
            except (IndexError, ValueError):
                since = 0
            self._json({"events": self.server.guidance.events_since(since)})
        elif self.path == "/status":
            self._json({
                "gemini_model": self.server.gemini.model,
                "gemini_key": bool(self.server.gemini.key),
                "el_voice": self.server.el_voice,
                "el_voice_source": self.server.el_voice_source,
                "el_key": bool(self.server.el_key),
                "el_model": self.server.el_model,
                "ros_camera": bool(self.server.ros_camera),
                "guidance": bool(self.server.guidance),
                "camera_status": (self.server.ros_camera.status()
                                  if self.server.ros_camera else "Not connected"),
                "fallback_image": bool(self.server.fallback_image),
                "fallback_image_bytes": (len(self.server.fallback_image)
                                         if self.server.fallback_image else 0),
            })
        elif self.path == "/debug/state":
            if hasattr(self.server, "ros_camera") and hasattr(self.server.ros_camera, "debug_state"):
                remote_state = self.server.ros_camera.debug_state()
                if remote_state:
                    if getattr(self.server, "demo", None):
                        remote_state["guardian"] = self.server.demo.snapshot()["guardian"]
                    self._json(remote_state)
                    return
            self._json(self._local_debug_state())
        else:
            self.send_error(404)

    def _local_debug_state(self):
        now = time.monotonic()
        cam_status = (self.server.ros_camera.status()
                      if self.server.ros_camera else
                      ("Static Image" if self.server.fallback_image else "Webcam"))
        guidance_data = {
            "active": False,
            "path_valid": False,
            "hazard_permitted": False,
            "direction": None,
            "direction_label": None,
            "tactile_flags": 0,
            "tactile_label": "neutral",
            "target_label": None,
            "distance_m": None,
            "bearing_deg": None,
            "age_s": None,
        }
        if hasattr(self.server, "guidance") and self.server.guidance and hasattr(self.server.guidance, "snapshot"):
            try:
                guidance_data = self.server.guidance.snapshot()
            except Exception:
                pass

        hazard_data = {
            "available": False,
            "severity": "none",
            "urgent": False,
            "caution": False,
            "direction": "clear",
            "distance_m": None,
            "phrase": None,
            "sensor_health": "ok" if hasattr(self.server, "hazard_state") else "unknown",
            "age_s": None,
        }
        if hasattr(self.server, "hazard_state") and self.server.hazard_state and hasattr(self.server.hazard_state, "snapshot"):
            try:
                hazard_data = self.server.hazard_state.snapshot(now)
            except Exception:
                pass

        guardian_data = {
            "state": "idle",
            "is_speaking": False,
            "sms_state": "idle",
            "last_observation": None,
            "trail_count": 0,
        }
        if hasattr(self.server, "guardian") and self.server.guardian and hasattr(self.server.guardian, "snapshot"):
            try:
                guardian_data = self.server.guardian.snapshot()
            except Exception:
                pass

        if getattr(self.server, "demo", None):
            guardian_data = self.server.demo.snapshot()["guardian"]

        vitals = {
            "depth_fps": None,
            "depth_latency_ms": None,
            "hazard_loop_ms": None,
            "planner_loop_ms": None,
            "safety_budget_ms": None,
            "safety_budget_pass": None,
        }
        sim = getattr(self.server, "simulation", None)
        if not isinstance(sim, dict):
            sim = {}
        if "hazard" in sim:
            hazard_data = {**sim["hazard"], "simulated": True}
        if "guidance" in sim:
            guidance_data = dict(sim["guidance"])
        if sim.get("fault"):
            hazard_data = {
                "simulated": True,
                "available": False,
                "severity": "none",
                "urgent": False,
                "caution": False,
                "direction": "clear",
                "distance_m": None,
                "phrase": None,
                "sensor_health": "fault",
                "age_s": 2.5,
            }
            guidance_data["hazard_permitted"] = False
            guidance_data["tactile_flags"] = 0

        lang = getattr(self.server, "lang", os.environ.get("DEVICE_LANG", "en"))

        return {
            "timestamp": round(now, 3),
            "device_lang": lang,
            "vitals": vitals,
            "simulation_active": bool(sim),
            "camera": {
                "status": cam_status,
                "age_s": None,
                "width": 640,
                "height": 480,
            },
            "guidance": guidance_data,
            "hazard": hazard_data,
            "guardian": guardian_data,
        }

    def do_POST(self):
        if self.path == "/demo/control":
            try:
                body = self._read_body()
                answer = self.server.demo.control(body.get("action"))
                return self._json({"answer": answer, **self.server.demo.snapshot()})
            except (AppError, ValueError, TypeError) as error:
                return self._json({"error": str(error)})
        if self.path == "/debug/simulate":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length else {}
            if hasattr(self.server, "ros_camera") and hasattr(self.server.ros_camera, "simulate"):
                remote_res = self.server.ros_camera.simulate(data.get("action", ""), data.get("value", ""))
                if remote_res is not None:
                    return self._json(remote_res)
            from companion.voice.pi_bridge import apply_simulation
            res = apply_simulation(self.server, data.get("action", ""), data.get("value", ""))
            return self._json(res)

        if self.path == "/debug/language":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length else {}
            lang = data.get("lang", "en")
            if lang in ("en", "ko", "zh", "ja", "es"):
                os.environ["DEVICE_LANG"] = lang
                self.server.lang = lang
                if hasattr(self.server, "ros_camera") and hasattr(self.server.ros_camera, "set_language"):
                    self.server.ros_camera.set_language(lang)
                return self._json({"device_lang": lang})
            self.send_error(400, "Invalid language code")
            return

        if self.path not in ("/ask", "/find", "/guide"):
            self.send_error(404)
            return
        runtime = getattr(self.server, "demo", None)
        generation = None
        try:
            body = self._read_body()
            if runtime:
                generation = runtime.begin(body.get("generation"))
            if self.path in ("/find", "/guide") and not runtime:
                raise AppError("Object workflow is unavailable.", 503)
            if self.path == "/find":
                runtime.clear_selection()
                query = body.get("text", "")
                if not isinstance(query, str) or not query.strip() or len(query) > 120:
                    raise AppError("Enter an object name of at most 120 characters.", 400)
                body["text"] = "Find the following object: " + query.strip()
            elif self.path == "/guide":
                label = runtime.consume_selection(body.get("selection_id"))
                body["text"] = "Guide me to the " + label
            return self._ask(body, runtime, generation, find_only=self.path == "/find", locate_only=self.path in ("/find", "/guide"))
        except (AppError, ValueError, TypeError) as error:
            return self._json({"error": str(error)})
        finally:
            if runtime and generation is not None:
                runtime.finish(generation)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length < 0 or length > 12 * 1024 * 1024:
            raise AppError("Request is too large.", 413)
        body = json.loads(self.rfile.read(length)) if length else {}
        if not isinstance(body, dict):
            raise AppError("Expected a request object.", 400)
        return body

    def _ask(self, body, runtime, generation, find_only=False, locate_only=False):
        # The browser owns scene playback; Guardian owns native laptop playback.
        play_host = False
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > 2000:
            raise AppError("Type a question of at most 2000 characters.", 400)
        captured_at = time.time()
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
        frame = None
        if self.server.ros_camera:
            try:
                frame = self.server.ros_camera.capture_frame()
                image_jpeg = frame["jpeg"]
                note(f"Pi ROS camera frame: {len(image_jpeg):,} bytes")
            except AppError as err:
                image_jpeg = None
                note(f"Pi camera unavailable: {err}", "warn")
        elif body.get("image"):
            image_jpeg = base64.b64decode(body["image"])
            raw_len = len(image_jpeg)
            image_jpeg = optimize_image(image_jpeg)
            note(f"Webcam frame: {raw_len:,} -> {len(image_jpeg):,} bytes")
        elif self.server.fallback_image:
            image_jpeg = self.server.fallback_image
            raw_len = len(image_jpeg)
            image_jpeg = optimize_image(image_jpeg)
            note(f"Using fallback image: {raw_len:,} -> {len(image_jpeg):,} bytes")
        else:
            image_jpeg = None
            note("No image available", "warn")

        # ---- Gemini ----
        note(f"Calling Gemini ({self.server.gemini.model}, streaming)…")
        gemini_t0 = time.monotonic()
        t_first_token = [None]

        def _on_chunk(chunk):
            if t_first_token[0] is None:
                t_first_token[0] = round(time.monotonic() - gemini_t0, 2)
                note(f"  first token: {t_first_token[0]}s", "pass")

        try:
            result = self.server.gemini.ask(
                audio_wav, image_jpeg, list(runtime.session.history) if runtime else [],
                stream=True,
                on_answer_chunk=_on_chunk,
                **({"text": text} if text else {}),
                **({"locate_only": True} if locate_only else {}),
            )
            gemini_s = round(time.monotonic() - gemini_t0, 2)
            note(f"Gemini OK — {gemini_s}s", "pass")
            note(f"  heard: {result['transcript']!r}")
            note(f"  answer: {result['answer']!r}")
            if result["landmark"]:
                note(f"  landmark: {result['landmark']!r}")
            note(f"  action: {result['device_action']}")
            if result["device_action"] == "navigate_target":
                note(f"  target: {result['target']!r} box_2d: {result['box_2d']}")
        except AppError as err:
            gemini_s = round(time.monotonic() - gemini_t0, 2)
            note(f"Gemini FAIL ({gemini_s}s): {err}", "fail")
            if play_host:
                play_host_audio(text="I'm sorry, I encountered an error. Please try again.")
            self._json({"error": str(err), "log": log})
            return

        if runtime and not runtime.current(generation):
            return self._json({"cancelled": True})
        if text:
            result["transcript"] = text
        resp = {
            "transcript": result["transcript"],
            "answer": result["answer"],
            "landmark": result["landmark"],
            "device_action": result["device_action"],
            "gemini_time": gemini_s,
            "gemini_first_token": t_first_token[0],
        }
        def apply_action():
            if find_only:
                from companion.voice.gemini import valid_box
                box = valid_box(result.get("box_2d"))
                label = str(result.get("target") or "").strip()[:40]
                if image_jpeg and box and label:
                    resp.update(target=label, box_2d=box,
                                target_image=base64.b64encode(image_jpeg).decode(),
                                selection_id=runtime.select_target(generation, label),
                                selection_ttl_s=60, workflow="found",
                                answer=f"I think I see the {label}. Check the highlighted object, then choose Guide to this object.")
                else:
                    resp.update(workflow="not_found", answer=(result["answer"] if image_jpeg else
                                "No camera image is available. Connect the camera and find the object again."))
                return
            if locate_only and (result["device_action"] != "navigate_target"
                                or not result.get("box_2d") or not image_jpeg):
                resp["answer"] = "I could not confirm the selected object in a fresh image. Find it again before requesting guidance."
                resp["device_action"] = "none"
                return
            if (result["device_action"] == "navigate_target" and result["box_2d"] is not None
                    and image_jpeg):
                resp["target"] = result["target"]
                resp["box_2d"] = result["box_2d"]
                resp["target_image"] = base64.b64encode(image_jpeg).decode()
                if not self.server.guidance:
                    resp["answer"] = "Guidance is unavailable here."
                elif not frame or not frame.get("stamp"):
                    resp["answer"] = "Guidance needs the live camera."
                else:
                    resp["answer"] = self.server.guidance.go_to(
                        result["target"] or "object", result["box_2d"],
                        frame["stamp"], frame["width"], frame["height"])
                note(resp["answer"])
            elif result["device_action"] == "navigate_backpack":
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

            if result["device_action"] == "guardian":
                resp["answer"] = "Open Guardian with the Guardian button. It will use this session's recent camera observations."
            elif runtime and result["device_action"] == "repeat":
                resp["answer"] = runtime.session.last_answer or "No answer to repeat yet."
            elif runtime and result["device_action"] == "help":
                resp["answer"] = runtime.status_text()
        if runtime:
            runtime.apply(generation, apply_action)
            runtime.remember(generation, {**result, "answer": resp["answer"]}, image_jpeg, captured_at)
        else:
            apply_action()

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
        if runtime and not runtime.current(generation):
            return self._json({"cancelled": True})
        self._json(resp)

    def _synthesize(self, text):
        """Returns (audio_bytes | None, first_byte_s, error_string)."""
        speed_env = os.environ.get("COMPANION_SPEECH_SPEED")
        try:
            speed = float(speed_env) if speed_env else 1.15
        except ValueError:
            speed = 1.15

        payload = {"text": text, "model_id": self.server.el_model}
        if abs(speed - 1.0) > 0.01:
            payload["voice_settings"] = {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": round(speed, 2),
            }

        url = (ELEVENLABS_API + "/text-to-speech/" + self.server.el_voice + "?"
               + urlencode({"output_format": "mp3_22050_32"}))
        req = Request(url,
                      data=json.dumps(payload).encode(),
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
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser cancelled an older camera preview request.

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
<title>Beacon — Live Dashboard</title>
<meta name="description" content="Beacon spatial guide — live camera, voice, object guidance and device telemetry">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Montserrat:wght@600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  color-scheme:dark;
  --bg:#05070A;--bg2:#0B1017;--card:#0B1017;
  --border:#1A2533;--border2:#34485e;--text:#FFFFFF;--dim:#8FA3B8;--dim2:#74899e;
  --accent:#35D7FF;--accent2:#168BFF;--accent-g:rgba(53,215,255,.18);
  --rec:#35D7FF;--rec-g:rgba(53,215,255,.2);
  --think:#FFB020;--think-g:rgba(255,176,32,.18);
  --pass:#3DFFB0;--fail:#FFB020;--warn:#FFB020;--hazard:#FF4D4D;--info:#8FA3B8;
  --r:8px;--font:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
  --display:'Montserrat',var(--font);--mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
body{font:14px/1.5 var(--font);background:var(--bg);color:var(--text);min-height:100vh;
  background-image:radial-gradient(ellipse at 20% 0%,rgba(22,139,255,.09),transparent 45%),linear-gradient(rgba(26,37,51,.22) 1px,transparent 1px),linear-gradient(90deg,rgba(26,37,51,.22) 1px,transparent 1px);background-size:auto,48px 48px,48px 48px}
button,input,summary{font:inherit}
button,summary{cursor:pointer}
button{transition:background .18s,border-color .18s,color .18s}
button:disabled{opacity:.45;cursor:not-allowed}
button:not(:disabled):hover{border-color:var(--accent);background:rgba(53,215,255,.08)}
:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
input::placeholder{color:var(--dim)}
[hidden]{display:none!important}
svg{display:block}
button svg{width:20px;height:20px}
small{font-size:12px;line-height:1.6;color:var(--dim)}
header{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:24px;min-height:82px;padding:14px 32px;background:var(--bg);border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;gap:10px;flex-shrink:0}
.brand-mark{width:50px;height:50px}
.brand h1{line-height:1}
.brand-word{width:136px;height:24px}
.brand-caption{font:10px var(--mono);letter-spacing:.2em;color:var(--dim);margin-top:9px;text-transform:uppercase}
.header-actions{margin-left:auto;display:flex;align-items:center;gap:18px}
.header-dots{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.dot{width:6px;height:6px;border-radius:50%;background:var(--dim2)}
.dot.ok{background:var(--pass);box-shadow:0 0 8px rgba(61,255,176,.35)}
.dot.err{background:var(--warn)}
.dot-label{font:10px var(--mono);color:var(--dim);margin-left:5px}
.lang-selector{display:flex;gap:2px;padding:3px;border:1px solid var(--border);border-radius:6px}
.lang-btn{background:transparent;border:1px solid transparent;color:var(--dim);font:11px var(--mono);padding:7px;border-radius:3px;min-height:32px}
.lang-btn.active{color:var(--accent);background:rgba(53,215,255,.09);border-color:var(--border2)}
.sound-test-btn,.clear-btn,.replay-btn{border:1px solid var(--border2);border-radius:4px;color:var(--dim);background:transparent;padding:8px 12px;font:11px var(--mono);min-height:36px}
.sound-test-btn{display:flex;gap:8px;align-items:center}
.workspace-intro{max-width:1800px;margin:auto;padding:32px 32px 24px;display:flex;justify-content:space-between;align-items:end;gap:20px}
.eyebrow{font:10px var(--mono);letter-spacing:.17em;text-transform:uppercase;color:var(--accent);display:flex;align-items:center;gap:9px;margin-bottom:10px}
.eyebrow:before{content:'';width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;border-bottom:7px solid var(--accent)}
.workspace-intro h2{font:700 clamp(24px,2.8vw,40px)/1.2 var(--display);letter-spacing:-.035em}
.workspace-intro h2 span{color:var(--accent)}
.intro-note{max-width:300px;text-align:right;font-size:12px;color:var(--dim)}
.workspace{max-width:1800px;margin:auto;padding:0 32px 32px;display:grid;grid-template-columns:minmax(0,1fr) 370px;gap:22px;align-items:start}
.interact{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(280px,1fr);gap:22px;min-width:0}
.scene-column,.voice-column{min-width:0}
.section-heading{font:600 12px var(--display);letter-spacing:.1em;text-transform:uppercase;display:flex;align-items:center;gap:10px;margin-bottom:16px}
.section-heading .section-number{font:11px var(--mono);color:var(--accent)}
.section-heading .section-note{margin-left:auto;color:var(--dim);font:10px var(--mono);letter-spacing:.03em;text-transform:none}
.cam-toggle-row{display:flex;border:1px solid var(--border);border-bottom:0;border-radius:8px 8px 0 0;padding:5px;gap:4px;background:var(--bg2)}
.cam-tab-btn{flex:1;background:none;border:1px solid transparent;border-radius:4px;padding:8px 4px;color:var(--dim);font:11px var(--mono);min-height:38px}
.cam-tab-btn.active{color:var(--accent);border-color:var(--border2);background:rgba(53,215,255,.06)}
.cam{width:100%;aspect-ratio:4/3;position:relative;background:radial-gradient(circle at center,rgba(22,139,255,.07),transparent 70%),var(--bg);border:1px solid var(--border);border-radius:0 0 8px 8px;overflow:hidden}
.cam:after{content:'';position:absolute;inset:12px;border:1px solid rgba(53,215,255,.2);clip-path:polygon(0 0,20px 0,20px 1px,1px 1px,1px 20px,0 20px,0 0,100% 0,100% 20px,calc(100% - 1px) 20px,calc(100% - 1px) 1px,calc(100% - 20px) 1px,calc(100% - 20px) 0,100% 0,100% 100%,calc(100% - 20px) 100%,calc(100% - 20px) calc(100% - 1px),calc(100% - 1px) calc(100% - 1px),calc(100% - 1px) calc(100% - 20px),100% calc(100% - 20px),100% 100%,0 100%,0 calc(100% - 20px),1px calc(100% - 20px),1px calc(100% - 1px),20px calc(100% - 1px),20px 100%,0 100%);pointer-events:none}
.cam video,.cam img{width:100%;height:100%;object-fit:contain;display:block;position:relative;z-index:1}
.cam canvas{position:relative;z-index:1}
.camera-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:var(--dim);font:10px var(--mono);letter-spacing:.1em}
.camera-empty svg{width:76px;height:76px;color:var(--accent);opacity:.5}
.cam-tag{position:absolute;top:20px;left:20px;z-index:2;padding:4px 8px;border:1px solid var(--border);background:var(--bg);font:10px var(--mono);color:var(--dim)}
.meter-wrap{position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--border);z-index:2}
.meter-bar{height:100%;width:0;background:var(--accent);transition:width 50ms}
.object-workflow{margin-top:18px;padding:20px;background:var(--card);border:1px solid var(--border);border-radius:var(--r)}
.workflow-title{display:flex;align-items:center;justify-content:space-between;gap:12px}
.workflow-title h2{font:600 16px var(--display);letter-spacing:-.02em}
.workflow-steps{display:flex;gap:8px;margin:18px 0;color:var(--dim);font:10px var(--mono)}
.workflow-steps span{flex:1;padding:7px 0;border-bottom:2px solid var(--border)}
.workflow-steps .current{color:var(--accent);border-color:var(--accent)}
.object-workflow p{color:var(--dim);font-size:12px;line-height:1.6;margin:12px 0}
.target-view{width:100%;border-radius:4px;display:block}
#object-preview{max-height:250px;object-fit:contain;background:var(--bg)}
.operator-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0}
.operator-row input{flex:1;min-width:100px;width:0;border:1px solid var(--border2);border-radius:4px;background:var(--bg);color:var(--text);padding:11px 12px;font-size:12px;min-height:42px}
.operator-btn{border:1px solid var(--border2);border-radius:4px;background:transparent;color:var(--text);padding:10px 12px;font-size:12px;min-height:40px}
.operator-btn.primary{background:rgba(53,215,255,.08);border-color:rgba(53,215,255,.4);color:var(--accent)}
.operator-btn.danger{color:var(--text);border-color:var(--border2)}
#stop-all{font:500 11px var(--mono);white-space:nowrap;border-color:var(--dim);display:flex;align-items:center;gap:8px}
#stop-all:before{content:'';width:8px;height:8px;background:currentColor}
#find-object,#ask-typed{color:var(--bg);background:var(--accent);border-color:var(--accent);font-weight:600}
#find-object:hover,#ask-typed:hover{background:#91eaff}
.voice-surface{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:20px}
.voice-invitation{font:600 22px/1.3 var(--display);letter-spacing:-.03em}
.voice-subtitle{color:var(--dim);font-size:12px;margin-top:8px}
.btn-area{display:flex;flex-direction:column;align-items:center;gap:12px;padding:32px 0 12px}
.talk{position:relative;width:86px;height:86px;border-radius:50%;border:1px solid var(--accent);color:var(--accent);background:radial-gradient(circle,rgba(53,215,255,.12),transparent);display:flex;align-items:center;justify-content:center;user-select:none;touch-action:none;box-shadow:0 0 0 10px rgba(53,215,255,.025),0 0 0 11px rgba(53,215,255,.06)}
.talk svg{width:28px;height:28px;fill:currentColor}
.talk.rec{background:rgba(53,215,255,.15);animation:breathe 1.6s ease-in-out infinite}
.talk.think{color:var(--think);border-color:var(--think)}
.talk .spin{display:none}
.talk.think svg.mic{display:none}
.talk.think .spin{display:block;width:26px;height:26px;border:2px solid var(--think);border-top-color:transparent;border-radius:50%;animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}
}
@keyframes breathe{50%{transform:scale(1.04)}
}
.status-text{font-size:12px;color:var(--dim);text-align:center;overflow-wrap:anywhere}
.rec-timer{font:11px var(--mono);color:var(--accent);min-height:16px}
.operator-controls small{display:block}
.audio-bar{display:flex;flex-wrap:wrap;gap:12px;border-top:1px solid var(--border);padding-top:16px;margin-top:20px;font-size:11px;color:var(--dim)}
.audio-bar label{display:flex;gap:6px;align-items:center;cursor:pointer}
.audio-bar input{accent-color:var(--accent)}
.resp{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:20px;margin-top:18px;display:none}
.resp.vis{display:block;animation:reveal .25s ease-out}
@keyframes reveal{from{opacity:0;transform:translateY(4px)}
to{opacity:1;transform:translateY(0)}
}
.resp .f{margin-bottom:16px}
.resp .f:last-child{margin:0}
.resp label{display:block;font:10px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--dim);margin-bottom:8px}
.resp p{font-size:14px;line-height:1.65;overflow-wrap:anywhere}
.resp .lm{color:var(--accent)}
.timings{display:flex;flex-wrap:wrap;gap:6px}
.timings span{font:10px var(--mono);padding:4px 6px;color:var(--dim);border:1px solid var(--border)}
.timings b{color:var(--text);font-weight:400}
.audio-player-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.audio-player-row audio{width:100%;min-width:0;height:36px}
.audio-status{font:11px var(--mono);color:var(--dim);margin-top:8px}
.debug{min-width:0;padding-left:22px;border-left:1px solid var(--border)}
.debug-header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:16px}
.hud-tabs{display:flex;gap:12px}
.hud-tab{border:0;border-bottom:2px solid transparent;background:none;color:var(--dim);padding:0 0 8px;font:600 11px var(--display);letter-spacing:.07em;text-transform:uppercase}
.hud-tab.active{color:var(--text);border-color:var(--accent)}
.hud-panel{display:flex;flex-direction:column;gap:14px}
.hud-card{padding:18px;background:var(--card);border:1px solid var(--border);border-radius:var(--r)}
.hud-card-title{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px;color:var(--dim);font:10px/1.6 var(--mono);letter-spacing:.07em;text-transform:uppercase}
.hud-pill{display:inline-block;flex-shrink:0;padding:3px 6px;font:9px/1.5 var(--mono);letter-spacing:.015em;border:1px solid var(--border);border-radius:3px}
.hud-pill.idle{color:var(--dim)}
.hud-pill.ok{color:var(--pass);border-color:rgba(61,255,176,.3);background:rgba(61,255,176,.04)}
.hud-pill.warn,.hud-pill.alert{color:var(--warn);border-color:rgba(255,176,32,.35);background:rgba(255,176,32,.04)}
.vitals-strip{display:flex;flex-wrap:wrap;gap:10px;padding:14px;border:1px solid var(--border);border-radius:var(--r);font:9px/1.6 var(--mono);background:var(--bg2)}
.vital-item{display:flex;gap:5px}
.vital-k{color:var(--dim)}
.vital-v{color:var(--text)}
.vital-pill{width:100%;color:var(--dim);border-top:1px solid var(--border);padding-top:8px;font-size:9px}
.vital-pill.ok{color:var(--pass)}
.vital-pill.alert{color:var(--warn)}
.dir-heading{display:flex;align-items:center;gap:14px}
.dir-icon{display:flex;align-items:center;justify-content:center;width:46px;height:46px;flex-shrink:0;border:1px solid var(--border);border-radius:4px;font-size:26px;color:var(--dim)}
.dir-icon.forward,.dir-icon.turn{color:var(--accent);border-color:var(--accent)}
.dir-icon.stop{color:var(--warn);border-color:var(--warn)}
.dir-title{font:600 14px var(--display)}
.dir-sub{font:10px/1.6 var(--mono);color:var(--dim);margin-top:6px}
.hands-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.hand-box{padding:12px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);text-align:center}
.hand-label{font:9px var(--mono);color:var(--dim);margin-bottom:10px}
.hand-svg{width:58px;height:58px;margin:auto}
.hand-arm{transform-origin:32px 32px;transition:transform .2s}
.hand-box.active{border-color:var(--accent)}
.hand-box.active .hand-arm{animation:servo-sweep .5s ease-in-out infinite alternate}
@keyframes servo-sweep{from{transform:rotate(-25deg)}
to{transform:rotate(25deg)}
}
.hand-state-text{font:10px var(--mono);color:var(--dim);margin-top:10px}
.hand-box.active .hand-state-text{color:var(--accent)}
.hazard-card.urgent{border-color:var(--hazard);background:linear-gradient(110deg,rgba(255,77,77,.09),transparent),var(--bg2)}
.hazard-card.urgent .hud-pill{color:var(--hazard);border-color:var(--hazard)}
.hazard-card.caution,.hazard-card.unavailable{border-color:rgba(255,176,32,.45)}
.hazard-card.clear{border-color:var(--border)}
.hazard-msg{font-size:13px;line-height:1.65;margin-bottom:16px}
.telemetry-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.tele-cell{padding:10px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px}
.tele-k{font:9px var(--mono);color:var(--dim);text-transform:uppercase}
.tele-v{font:10px/1.5 var(--mono);margin-top:7px;overflow-wrap:anywhere}
.warning-controls{border-top:1px solid var(--border);margin-top:16px;padding-top:4px}
#warning-audio{width:100%;color:var(--accent);border-color:var(--border2)}
#warning-audio[aria-pressed=true]{border-color:var(--accent);background:rgba(53,215,255,.08)}
#warning-audio-state{font:10px var(--mono);color:var(--dim)}
.observation-trail{font-size:12px;color:var(--dim);line-height:1.6;margin-top:12px;overflow-wrap:anywhere}
.sim-card{border-style:dashed}
.sim-card summary{list-style:none;display:flex;align-items:center;gap:10px;font:11px var(--mono);color:var(--dim)}
.sim-card summary::-webkit-details-marker{display:none}
.sim-card summary:before{content:'+';color:var(--accent);font-size:18px}
.sim-card[open] summary:before{content:'−'}
.sim-card summary .hud-pill{margin-left:auto}
.sim-content{margin-top:18px}
.sim-group{margin-top:14px}
.sim-label{font:9px var(--mono);color:var(--dim);margin-bottom:8px}
.sim-btn-row{display:flex;flex-wrap:wrap;gap:6px}
.sim-btn{border:1px solid var(--border2);border-radius:4px;background:transparent;color:var(--text);font:10px var(--mono);min-height:34px;padding:6px 8px}
.sim-btn.alert{color:var(--hazard);border-color:rgba(255,77,77,.4)}
.sim-btn.warn{color:var(--warn)}
.sim-btn.ok{color:var(--pass)}
.sim-btn.reset{color:var(--accent)}
.log-scroll{max-height:750px;overflow:auto;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:12px 0}
.log-line{display:flex;gap:8px;padding:5px 12px;font:10px/1.7 var(--mono)}
.log-t{color:var(--dim2);flex-shrink:0}
.log-tag{flex-shrink:0}
.log-tag.pass{color:var(--pass)}
.log-tag.fail,.log-msg.fail,.log-tag.warn,.log-msg.warn{color:var(--warn)}
.log-tag.info{color:var(--dim)}
.log-msg{overflow-wrap:anywhere;min-width:0}
.tag{font:10px var(--mono);color:var(--dim);overflow-wrap:anywhere}
.workspace-footer{display:flex;gap:12px;flex-wrap:wrap;border-top:1px solid var(--border);padding-top:18px;margin-top:22px;font:10px var(--mono);color:var(--dim)}
@media(min-width:1700px){.workspace{grid-template-columns:minmax(0,1fr) 420px;gap:32px}
.interact{gap:28px}
.voice-surface{padding:28px}
.btn-area{padding-top:48px;padding-bottom:24px}
}
@media(max-width:1250px){.workspace{grid-template-columns:minmax(0,1fr) 350px}
.interact{grid-template-columns:1fr}
.voice-column{margin-top:6px}
.header-dots{display:none}
.header-actions{gap:10px}
}
@media(max-width:800px){header{padding:12px 18px;gap:12px;flex-wrap:wrap}
.header-actions{margin-left:auto}
.lang-selector{order:3;width:100%}
.lang-btn{flex:1}
.sound-test-btn{font-size:0;gap:0;padding:9px}
.sound-test-btn svg{width:18px}
.workspace-intro{padding:24px 18px 20px}
.intro-note{display:none}
.workspace{padding:0 18px 24px;grid-template-columns:1fr;gap:28px}
.debug{border-left:0;padding-left:0}
.section-heading{margin-bottom:14px}
.hud-card-title{font-size:11px}
.hud-pill{font-size:10px}
.tele-k,.tele-v{font-size:11px}
.vitals-strip{font-size:11px}
.sim-btn{font-size:11px;min-height:40px}
.workspace-intro h2{font-size:28px}
.brand-word{width:120px}
.brand-mark{width:42px;height:42px}
.brand-caption{font-size:9px}
}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important;scroll-behavior:auto!important}
}
.telemetry-source{margin-bottom:14px}
.telemetry-source .hud-pill{display:block;text-align:center;padding:7px;font-size:10px;letter-spacing:.08em}
.sim-summary-note{margin-left:auto;font-size:10px;color:var(--dim)}
html{scroll-padding-top:150px}
.cam-tag{max-width:calc(100% - 40px)}
</style>
</head>
<body>

<header>
  <div class="brand"><svg class="brand-mark" viewBox="0 0 200 200" aria-hidden="true"><defs>
    <linearGradient id="lgLdashboard" x1="1" y1="0" x2="0" y2="0"><stop offset="0" stop-color="#35D7FF" stop-opacity=".9"/><stop offset="1" stop-color="#35D7FF" stop-opacity="0"/></linearGradient>
    <linearGradient id="lgRdashboard" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#35D7FF" stop-opacity=".9"/><stop offset="1" stop-color="#35D7FF" stop-opacity="0"/></linearGradient>
    <radialGradient id="lgHdashboard"><stop offset="0" stop-color="#35D7FF" stop-opacity=".85"/><stop offset="1" stop-color="#35D7FF" stop-opacity="0"/></radialGradient></defs>
  <circle class="lg-halo" cx="100" cy="72" r="36" fill="url(#lgHdashboard)"/>
  <path class="lg-ring" pathLength="1" d="M143.84 56.16A62 62 0 1 1 56.16 56.16" fill="none" stroke="#168BFF" stroke-width="5" stroke-linecap="round"/>
  <g class="lg-bl-w"><polygon class="lg-bl" points="100,72 14,52 14,92" fill="url(#lgLdashboard)"/></g>
  <g class="lg-br-w"><polygon class="lg-br" points="100,72 186,52 186,92" fill="url(#lgRdashboard)"/></g>
  <g class="lg-tower"><polygon points="88,63 100,48 112,63" fill="#fff"/><polygon points="91,82 109,82 115,150 85,150" fill="#fff"/></g>
  <g class="lg-lan-w"><rect class="lg-lantern" x="90" y="64" width="20" height="16" fill="#35D7FF"/></g>
  <g class="lg-waves" fill="none" stroke-width="4" stroke-linecap="round"><path pathLength="1" d="M58 166Q79 157 100 166T142 166" stroke="#35D7FF"/><path pathLength="1" d="M72 180Q86 173 100 180T128 180" stroke="#168BFF"/></g></svg><div><h1><svg class="brand-word" viewBox="0 -8 540 96" role="img" aria-label="Beacon"><g fill="none" stroke="#fff" stroke-width="12"><path transform="translate(0 0)" d="M6 6H34a17 17 0 0 1 0 34H6M6 40H38a17 17 0 0 1 0 34H6M6 0V80"/><path transform="translate(92 0)" d="M54 6H6V74H54M6 40H46M6 0V80"/><path transform="translate(180 0)" d="M2 80L32 3L62 80"/><path transform="translate(272 0)" d="M64 16A34 34 0 1 0 64 64"/><path transform="translate(472 0)" d="M6 80V6L56 74V0"/><circle cx="410" cy="40" r="34"/></g><polygon points="212,40 222,62 202,62" fill="#168BFF"/></svg></h1><p class="brand-caption">Spatial companion</p></div></div>
  <div class="lang-selector" id="lang-selector" role="group" aria-label="Device language">
    <button class="lang-btn active" data-lang="en" aria-label="English" onclick="setLanguage('en')">EN</button>
    <button class="lang-btn" data-lang="ko" aria-label="한국어" onclick="setLanguage('ko')">KO</button>
    <button class="lang-btn" data-lang="zh" aria-label="中文" onclick="setLanguage('zh')">ZH</button>
    <button class="lang-btn" data-lang="ja" aria-label="日本語" onclick="setLanguage('ja')">JA</button>
    <button class="lang-btn" data-lang="es" aria-label="Español" onclick="setLanguage('es')">ES</button>
  </div>
  <div class="header-actions">
    <button id="stop-all" class="operator-btn danger">Stop · Esc</button>
    <button class="sound-test-btn" id="btn-sound-test" title="Play test sound & speech"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/></svg>Test sound</button>
    <div class="header-dots">
      <span class="dot-label">Gemini</span><span class="dot" id="dot-gemini"></span>
      <span class="dot-label">11Labs</span><span class="dot" id="dot-el"></span>
      <span class="dot-label">Mic</span><span class="dot" id="dot-mic"></span>
      <span class="dot-label">Cam</span><span class="dot" id="dot-cam"></span>
    </div>
  </div>
</header>
<section class="workspace-intro" aria-label="Beacon live workspace">
  <div><p class="eyebrow">HackGT 2026 / Live workspace</p><h2>Say where. <span>Be there.</span></h2></div>
  <p class="intro-note">More context. Through sound and touch.<br>Built to complement the white cane.</p>
</section>
<main class="workspace">
<!-- left panel -->
<div class="interact">
 <section class="scene-column" aria-labelledby="perception-heading">
  <h2 class="section-heading" id="perception-heading"><span class="section-number">01</span> Perception <span class="section-note">See your surroundings</span></h2>
  <div class="cam-toggle-row">
    <button class="cam-tab-btn active" id="btn-view-cam" onclick="switchLeftView('cam')">Camera</button>
    <button class="cam-tab-btn" id="btn-view-map" onclick="switchLeftView('map')">Radar · illustrative</button>
  </div>
  <div class="cam" id="cam-box">
    <div class="camera-empty" aria-hidden="true"><svg viewBox="0 0 80 80" fill="none" stroke="currentColor"><circle cx="40" cy="40" r="30" stroke-dasharray="130 60"/><circle cx="40" cy="40" r="19" opacity=".5"/><path d="M40 8v15m0 34v15M8 40h15m34 0h15"/><circle cx="40" cy="40" r="3" fill="currentColor"/></svg><span>AWAITING CAMERA</span></div>
    <span class="cam-tag" id="cam-tag">…</span>
    <video id="webcam" autoplay playsinline muted style="display:none"></video>
    <img id="fallback" src="/fallback-image" alt="Current camera scene" style="display:none">
    <canvas id="planner-canvas" width="420" height="315" style="display:none;width:100%;height:100%"></canvas>
    <canvas id="snap" style="display:none"></canvas>
    <div class="meter-wrap"><div class="meter-bar" id="meter"></div></div>
  </div>

  <section class="object-workflow" aria-labelledby="object-heading">
    <div class="workflow-title"><h2 id="object-heading">Find an object</h2><span class="hud-pill idle" id="object-phase">READY</span></div>
    <div class="workflow-steps"><span id="step-find" class="current">1 · Find</span><span id="step-confirm">2 · Inspect</span><span id="step-guide">3 · Guide</span></div>
    <form id="object-form" class="operator-row">
      <input id="object-query" maxlength="120" placeholder="Chair, water bottle, doorway…" aria-label="Object to find" required>
      <button id="find-object" class="operator-btn primary" type="submit">Find object</button>
    </form>
    <p id="object-status" role="status">Name one visible object. Finding it does not start guidance.</p>
    <canvas id="object-preview" class="target-view" style="display:none" aria-label="Recognized target on the captured camera frame"></canvas>
    <div class="operator-row"><button id="guide-object" class="operator-btn primary" disabled>Guide to this object</button><button id="stop-guidance" class="operator-btn danger">Stop guidance</button></div>
    <small id="object-note">Guidance rechecks a fresh image. Depth routing needs the Pi.</small>
  </section>

 </section>
 <section class="voice-column" aria-labelledby="voice-heading">
  <h2 class="section-heading" id="voice-heading"><span class="section-number">02</span> Voice <span class="section-note">Ask. Listen. Explore.</span></h2>
  <div class="voice-surface">
   <h3 class="voice-invitation">What’s around you?</h3>
   <p class="voice-subtitle">Ask a question or read a sign. Beacon listens.</p>
  <div class="btn-area">
    <button class="talk" id="btn" disabled title="Hold to talk, release to send">
      <svg class="mic" viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
      <div class="spin"></div>
    </button>
    <span class="status-text" id="status" role="status" aria-live="polite">Initializing…</span>
    <span class="rec-timer" id="timer"></span>
  </div>

  <section class="operator-controls" aria-label="Laptop controls">
    <form id="question-form" class="operator-row"><input id="typed-question" maxlength="2000" placeholder="Ask about the scene…" aria-label="Typed scene question" required><button id="ask-typed" class="operator-btn" type="submit">Ask</button></form>
    <div class="operator-row"><button id="enable-mic" class="operator-btn">Enable mic</button><button id="repeat-answer" class="operator-btn">Repeat</button><button id="local-status" class="operator-btn">Status</button></div>
    <small id="operator-hint">Hold the microphone button or Space to talk. Release to send.</small>

  </section>

  <div class="audio-bar">
    <label title="Play audio via Web Audio / HTML5 in this browser tab"><input type="checkbox" id="opt-browser-audio" checked> Browser Audio</label>
    <label title="Scene audio plays once in the browser; Guardian uses native laptop audio"><input type="checkbox" id="opt-host-audio" disabled> Host playback unavailable</label>
    <label title="Play Pi hardware earcons (sound effects)"><input type="checkbox" id="opt-earcons" checked> Earcons</label>
  </div>

  </div>

  <div class="resp" id="resp">
    <div class="f"><label>Heard</label><p id="r-heard"></p></div>
    <div class="f"><label>Answer</label><p id="r-answer"></p></div>
    <div class="f" id="r-lm-wrap" style="display:none"><label>Landmark</label><p id="r-lm" class="lm"></p></div>
    <div class="f" id="r-target-wrap" style="display:none"><label>Target (frame sent to Gemini)</label><canvas id="r-target" class="target-view"></canvas></div>
    <div class="f" id="r-audio-wrap" style="display:none">
      <label>Voice Output</label>
      <div class="audio-player-row">
        <audio id="audio-player" controls preload="auto"></audio>
        <button class="replay-btn" id="replay-btn" title="Replay voice">Replay</button>
      </div>
      <div class="audio-status" id="audio-status"></div>
    </div>
    <div class="f"><label>Latency</label><div class="timings" id="r-timings"></div></div>
  </div>
  <div class="workspace-footer"><span>GEMINI + ELEVENLABS</span><span class="tag" id="model-tag">Connecting…</span></div>
 </section>
</div>

<!-- right panel: judge telemetry HUD & debug log -->
<div class="debug">
  <div class="debug-header">
    <div class="hud-tabs">
      <button class="hud-tab active" id="tab-hud">Telemetry</button>
      <button class="hud-tab" id="tab-log">Console</button>
    </div>
    <div style="margin-left:auto;display:flex;align-items:center;gap:.5rem">
      <span class="hud-pill idle" id="hud-cam-status">CAM: UNKNOWN</span>
      <span class="hud-pill idle" id="hud-lang">EN</span>
      <button class="clear-btn" id="clear-log" style="display:none">Clear</button>
    </div>
  </div>

  <div class="telemetry-source"><span class="hud-pill idle" id="sim-pill">LIVE TELEMETRY</span></div>

  <!-- Judge Telemetry HUD -->
  <div class="hud-panel" id="hud-panel">
    <!-- Real-Time Latency & Vitals Meter -->
    <div class="vitals-strip">
      <div class="vital-item"><span class="vital-k">CAM:</span> <span class="vital-v" id="vital-cam">Unmeasured</span></div>
      <div class="vital-item"><span class="vital-k">HAZARD:</span> <span class="vital-v" id="vital-haz">Unmeasured</span></div>
      <div class="vital-item"><span class="vital-k">PLANNER:</span> <span class="vital-v" id="vital-plan">Unmeasured</span></div>
      <div class="vital-item"><span class="vital-k">RTT:</span> <span class="vital-v" id="vital-rtt">—</span></div>
      <div class="vital-pill" id="vital-budget">LATENCY UNMEASURED</div>
    </div>

    <!-- Hazard Perception Radar -->
    <div class="hud-card hazard-card unavailable" id="hazard-card">
      <div class="hud-card-title">
        <span>Obstacle warnings</span>
        <span class="hud-pill warn" id="hazard-pill">UNKNOWN</span>
      </div>
      <div class="hazard-msg" id="hazard-msg">Waiting for live hazard sensing. Movement guidance is inhibited.</div>
      <div class="telemetry-row">
        <div class="tele-cell">
          <div class="tele-k">Severity</div>
          <div class="tele-v" id="haz-sev">UNKNOWN</div>
        </div>
        <div class="tele-cell">
          <div class="tele-k">Distance</div>
          <div class="tele-v" id="haz-dist">—</div>
        </div>
        <div class="tele-cell">
          <div class="tele-k">Heartbeat</div>
          <div class="tele-v" id="haz-age">—</div>
        </div>
      </div>
      <div class="warning-controls">
    <div class="operator-row"><button id="warning-audio" class="operator-btn" aria-pressed="false">Enable obstacle warnings</button><span id="warning-audio-state" role="status">Warning audio off</span></div>
    <small>Pi depth alerts → laptop speakers. Judge simulations are announced as simulated. Stop mutes warnings.</small>
      </div>
    </div>

    <!-- Active Heading Card -->
    <div class="hud-card">
      <div class="hud-card-title">
        <span>Guidance Direction & Route</span>
        <span class="hud-pill idle" id="hud-path-status">Guidance Idle</span>
      </div>
      <div class="dir-heading">
        <div class="dir-icon idle" id="dir-icon">○</div>
        <div class="dir-details">
          <div class="dir-title" id="dir-title">IDLE / CANE ONLY</div>
          <div class="dir-sub" id="dir-sub">Guidance inactive — navigate by cane</div>
        </div>
      </div>
    </div>

    <!-- Tactile Hand Units Simulator -->
    <div class="hud-card">
      <div class="hud-card-title">
        <span>Tactile Hand Units (ESP32)</span>
        <span class="hud-pill idle" id="hud-tactile-flags">0x00 NEUTRAL</span>
      </div>
      <div class="hands-grid">
        <div class="hand-box" id="hand-left">
          <div class="hand-label">Left Hand (Port 4210)</div>
          <svg class="hand-svg" viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="28" fill="#0B1017" stroke="rgba(255,255,255,0.1)" stroke-width="2"/>
            <line x1="32" y1="32" x2="32" y2="10" stroke="rgba(255,255,255,0.2)" stroke-dasharray="2 2" stroke-width="1.5"/>
            <g class="hand-arm" id="arm-left">
              <line x1="32" y1="32" x2="32" y2="12" stroke="#35D7FF" stroke-width="3" stroke-linecap="round"/>
              <circle cx="32" cy="12" r="4" fill="#35D7FF"/>
            </g>
            <circle cx="32" cy="32" r="5" fill="#34485e"/>
          </svg>
          <div class="hand-state-text" id="hand-left-text">REST (90°)</div>
        </div>
        <div class="hand-box" id="hand-right">
          <div class="hand-label">Right Hand (Port 4210)</div>
          <svg class="hand-svg" viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="28" fill="#0B1017" stroke="rgba(255,255,255,0.1)" stroke-width="2"/>
            <line x1="32" y1="32" x2="32" y2="10" stroke="rgba(255,255,255,0.2)" stroke-dasharray="2 2" stroke-width="1.5"/>
            <g class="hand-arm" id="arm-right">
              <line x1="32" y1="32" x2="32" y2="12" stroke="#35D7FF" stroke-width="3" stroke-linecap="round"/>
              <circle cx="32" cy="12" r="4" fill="#35D7FF"/>
            </g>
            <circle cx="32" cy="32" r="5" fill="#34485e"/>
          </svg>
          <div class="hand-state-text" id="hand-right-text">REST (90°)</div>
        </div>
      </div>
    </div>

    <!-- Guardian Voice Assistant -->
    <div class="hud-card">
      <div class="hud-card-title">
        <span>Guardian Voice Assistant</span>
        <span class="hud-pill idle" id="guardian-pill">STANDBY</span>
      </div>
      <div class="telemetry-row">
        <div class="tele-cell">
          <div class="tele-k">State</div>
          <div class="tele-v" id="guard-state">CLOSED</div>
        </div>
        <div class="tele-cell">
          <div class="tele-k">SMS Gate</div>
          <div class="tele-v" id="guard-sms">IDLE</div>
        </div>
        <div class="tele-cell">
          <div class="tele-k">Observations</div>
          <div class="tele-v" id="guard-obs">0 SAVED</div>
        </div>
      </div>
      <div class="operator-row"><button id="guardian-open" class="operator-btn primary">Open Guardian</button><button id="guardian-end" class="operator-btn" disabled>End Guardian</button></div>
      <small id="guardian-help">Uses the laptop microphone and speakers. Text messages are simulated.</small>
      <div id="observation-trail" class="observation-trail"></div>
    </div>
    <!-- Demo Simulator / Judge Controls -->
    <details class="hud-card sim-card">
      <summary><span>Demo simulator</span>
        <span class="sim-summary-note">Display only</span>
      </summary>
      <div class="sim-content"><small>Illustrative scenarios. Injected warnings are announced as simulated.</small>
      <div class="sim-group">
        <div class="sim-label">INJECT HAZARD SCENARIO:</div>
        <div class="sim-btn-row">
          <button class="sim-btn alert" onclick="injectSim('hazard', 'urgent_head')">Head Obstacle</button>
          <button class="sim-btn warn" onclick="injectSim('hazard', 'caution_corridor')">Side Obstacle</button>
          <button class="sim-btn alert" onclick="injectSim('hazard', 'dropoff')">Stairs / Drop</button>
          <button class="sim-btn ok" onclick="injectSim('hazard', 'clear')">Clear</button>
        </div>
      </div>
      <div class="sim-group">
        <div class="sim-label">INJECT TACTILE HEADING (ESP32 SERVOS):</div>
        <div class="sim-btn-row">
          <button class="sim-btn" onclick="injectSim('direction', 'forward')">↑ Forward (0x01)</button>
          <button class="sim-btn" onclick="injectSim('direction', 'left')">↰ Left (0x02)</button>
          <button class="sim-btn" onclick="injectSim('direction', 'right')">↱ Right (0x04)</button>
          <button class="sim-btn" onclick="injectSim('direction', 'stop')">Stop / Neutral</button>
        </div>
      </div>
      <div class="sim-group">
        <div class="sim-label">FAIL-SAFE VALIDATION:</div>
        <div class="sim-btn-row">
          <button class="sim-btn warn" onclick="injectSim('fault', 'heartbeat_drop')">Drop Heartbeat (Fail Toward Cane)</button>
          <button class="sim-btn reset" onclick="injectSim('reset', '')">Reset Live</button>
        </div>
      </div>
      </div>
    </details>
  </div>

  <!-- Console Log -->
  <div class="log-scroll" id="log-scroll" style="display:none"></div>
</div>
</main>

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
    fallback.style.display = serverStatus.camera_status === 'Ready' ? 'block' : 'none';
    camTag.textContent = 'Pi camera';
    dot('cam', serverStatus.camera_status === 'Ready');
    const refreshPiFrame = () => {
      fallback.src = '/pi-frame?t=' + Date.now();
    };
    fallback.onerror = () => { fallback.style.display = 'none'; dot('cam', false); };
    fallback.onload = () => { fallback.style.display = currentLeftView==='cam'?'block':'none'; dot('cam', true); };
    refreshPiFrame();
    setInterval(refreshPiFrame, 1000);
  } else if (serverStatus && serverStatus.fallback_image) {
    fallback.style.display = 'block';
    camTag.textContent = 'Static rehearsal image';
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
    fallback.style.display = 'none';
    dot('cam', false);
    camTag.textContent = 'No camera';
    log('Webcam unavailable — connect a camera or use a labelled rehearsal image', 'warn');
  }

  dashboardConfig = serverStatus || {};
  await pollOperator();
  statusEl.textContent = 'Ready. Enable the microphone or type a question.';
  updateOperatorControls();

  if (serverStatus && serverStatus.guidance) setInterval(pollGuidance, 1000);
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
async function playVoiceResponse(b64Audio, answerText, token=requestEpoch) {
  if(token!==requestEpoch || guardianState!=="closed" || warningSpeaking) return;
  lastVoiceData = {audio: b64Audio, answer: answerText};
  const audioWrap = $('r-audio-wrap');
  audioWrap.style.display = '';

  if (!optBrowserAudio.checked) {
    audioStatus.textContent = 'Browser audio disabled; enable Browser Audio to hear scene answers';
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
          if(token!==requestEpoch || guardianState!=="closed" || warningSpeaking) return;
          if (activeWebAudioSource) {
            try { activeWebAudioSource.stop(); } catch(e){}
          }
          activeWebAudioSource = ctx.createBufferSource();
          activeWebAudioSource.buffer = decoded;
          activeWebAudioSource.connect(ctx.destination);
          const playingSource = activeWebAudioSource;
          activeWebAudioSource.onended = () => {
            if (activeWebAudioSource === playingSource) activeWebAudioSource = null;
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
      if (!playedWebAudio && token===requestEpoch && guardianState==='closed') {
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
      if(token===requestEpoch && guardianState==='closed') speakFallback(answerText);
    }
  } else if (answerText) {
    speakFallback(answerText);
  }
}

function speakFallback(text) {
  if (warningSpeaking) return;
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

/* ================ named-object guidance ================ */
function drawTarget(b64, box, label, canvasId="r-target", token=requestEpoch){
  const img=new Image();
  img.onload=()=>{
    if(token!==requestEpoch) return;
    const c=$(canvasId), ctx=c.getContext('2d');
    c.width=img.naturalWidth; c.height=img.naturalHeight;
    ctx.drawImage(img,0,0);
    // box_2d is [y_min, x_min, y_max, x_max] on 0-1000.
    const [y0,x0,y1,x1]=box.map((v,i)=>v/1000*(i%2?c.width:c.height));
    ctx.lineWidth=Math.max(2,c.width/200); ctx.strokeStyle='#35D7FF'; ctx.fillStyle='#35D7FF';
    ctx.strokeRect(x0,y0,x1-x0,y1-y0);
    ctx.font=`${Math.max(14,Math.round(c.width/40))}px sans-serif`;
    ctx.fillText(label||'target',x0+4,Math.max(y0-6,18));
  };
  img.src='data:image/jpeg;base64,'+b64;
}

let guidanceEventN=0, guidancePrimed=false;
async function pollGuidance(){
  try{
    const r=await fetch('/guidance/events?since='+guidanceEventN);
    const d=await r.json();
    for(const e of d.events||[]){
      guidanceEventN=Math.max(guidanceEventN,e.n);
      if(!guidancePrimed) continue;  // Events from before this page loaded.
      log('Guidance: '+e.text, e.priority<=1?'warn':'info');
      if(!operatorBusy && guardianState === 'closed') speakFallback(e.text);
    }
    guidancePrimed=true;
  }catch(err){}
}

/* ================ Judge HUD Polling ================ */
const tabHud = $('tab-hud'), tabLog = $('tab-log'),
      hudPanel = $('hud-panel'), clearLogBtn = $('clear-log');

tabHud.onclick = () => {
  tabHud.classList.add('active'); tabLog.classList.remove('active');
  hudPanel.style.display = 'flex'; logScroll.style.display = 'none';
  clearLogBtn.style.display = 'none';
};
tabLog.onclick = () => {
  tabLog.classList.add('active'); tabHud.classList.remove('active');
  hudPanel.style.display = 'none'; logScroll.style.display = 'block';
  clearLogBtn.style.display = '';
};

let lastTelemetry = null;
let currentLeftView = 'cam';

async function setLanguage(lang) {
  try {
    const res = await fetch('/debug/language', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({lang})
    });
    if (res.ok) pollDebugState();
  } catch (e) {
    console.error('Language switch error:', e);
  }
}

async function injectSim(action, value) {
  try {
    const res = await fetch('/debug/simulate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action, value})
    });
    if (res.ok) pollDebugState();
  } catch (e) {
    console.error('Simulation error:', e);
  }
}

function switchLeftView(mode) {
  currentLeftView = mode;
  $('btn-view-cam').className = 'cam-tab-btn' + (mode === 'cam' ? ' active' : '');
  $('btn-view-map').className = 'cam-tab-btn' + (mode === 'map' ? ' active' : '');
  const canvas = $('planner-canvas');
  if (mode === 'map') {
    $('webcam').style.display = 'none';
    $('fallback').style.display = 'none';
    canvas.style.display = 'block';
    $('cam-tag').textContent = 'Illustrative radar · not live map';
    drawPlannerRadar(lastTelemetry || {});
  } else {
    canvas.style.display = 'none';
    const hasImage = dashboardConfig.fallback_image ||
      (dashboardConfig.ros_camera && fallback.complete && fallback.naturalWidth > 0);
    $('cam-tag').textContent = hasWebcam ? 'Live camera' : dashboardConfig.fallback_image
      ? 'Static rehearsal image' : hasImage ? 'Pi camera' : 'No camera';
    $('webcam').style.display = hasWebcam ? 'block' : 'none';
    $('fallback').style.display = !hasWebcam && hasImage ? 'block' : 'none';
  }
}

function drawPlannerRadar(d) {
  const canvas = $('planner-canvas');
  if (!canvas || canvas.style.display === 'none') return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  // Background grid
  ctx.fillStyle = '#05070A';
  ctx.fillRect(0, 0, w, h);

  const cx = w / 2;
  const cy = h - 35; // Robot near bottom center

  // Distance range rings (1m, 2m, 3m)
  const scale = 75; // 75px per meter
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  [1, 2, 3].forEach(m => {
    ctx.beginPath();
    ctx.arc(cx, cy, m * scale, Math.PI, 0);
    ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.fillText(m + 'm', cx + m * scale - 18, cy - 4);
  });

  // Polar angle rays (-45, 0, 45 deg)
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  [-45, 0, 45].forEach(deg => {
    const rad = (deg - 90) * Math.PI / 180;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(rad) * 3.2 * scale, cy + Math.sin(rad) * 3.2 * scale);
    ctx.stroke();
  });

  // Robot Origin
  ctx.fillStyle = '#35D7FF';
  ctx.shadowColor = 'rgba(53,215,255,0.6)';
  ctx.shadowBlur = 10;
  ctx.beginPath();
  ctx.arc(cx, cy, 8, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;

  // Direction heading pointer
  let dir = (d.guidance && d.guidance.direction != null) ? d.guidance.direction : 0;
  let angle = -Math.PI / 2; // Forward
  if (dir === 1) angle -= 0.45; // Left
  if (dir === 2) angle += 0.45; // Right

  ctx.strokeStyle = '#35D7FF';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + Math.cos(angle) * 22, cy + Math.sin(angle) * 22);
  ctx.stroke();

  // Target Waypoint
  const g = d.guidance || {};
  let targetX = cx + (dir === 1 ? -60 : (dir === 2 ? 60 : 0));
  let targetY = cy - 2.4 * scale;

  ctx.fillStyle = '#35D7FF';
  ctx.shadowColor = '#35D7FF';
  ctx.shadowBlur = 12;
  ctx.beginPath();
  ctx.arc(targetX, targetY, 9, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.fillStyle = '#ffffff';
  ctx.font = '11px Inter, sans-serif';
  ctx.fillText(g.target_label ? g.target_label.toUpperCase() : 'EXAMPLE TARGET', targetX + 14, targetY + 4);

  // A* Path Trajectory (curved green line)
  ctx.strokeStyle = (g.path_valid !== false) ? 'rgba(53,215,255,0.85)' : 'rgba(255,176,32,0.5)';
  ctx.lineWidth = 3;
  ctx.setLineDash([6, 4]);
  ctx.beginPath();
  ctx.moveTo(cx, cy - 8);
  ctx.quadraticCurveTo(cx + (targetX - cx) * 0.3, cy - 1.2 * scale, targetX, targetY);
  ctx.stroke();
  ctx.setLineDash([]);

  // Hazards
  const hazard = d.hazard || {};
  if (hazard.available && (hazard.urgent || hazard.caution)) {
    let hazDist = (hazard.distance_m || 1.1) * scale;
    let hazAngle = -Math.PI / 2 + (hazard.direction === 'left' ? -0.4 : (hazard.direction === 'right' ? 0.4 : 0));
    let hx = cx + Math.cos(hazAngle) * hazDist;
    let hy = cy + Math.sin(hazAngle) * hazDist;

    ctx.fillStyle = hazard.urgent ? 'rgba(255,77,77,0.85)' : 'rgba(255,176,32,0.85)';
    ctx.shadowColor = hazard.urgent ? '#FF4D4D' : '#FFB020';
    ctx.shadowBlur = 16;
    ctx.beginPath();
    ctx.arc(hx, hy, hazard.urgent ? 14 : 10, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 10px Inter, sans-serif';
    ctx.fillText(hazard.urgent ? 'DANGER' : 'CAUTION', hx + 16, hy + 4);
  }
}

let isPolling = false;
async function pollDebugState() {
  if (isPolling) return;
  isPolling = true;
  try {
    const t0 = performance.now();
    const res = await fetch('/debug/state', {signal: AbortSignal.timeout(2000)});
    const rtt = Math.round(performance.now() - t0);
    if (!res.ok) throw Error('Telemetry unavailable');
    const d = await res.json();
    lastTelemetry = d;
    updateHud(d, rtt);
    if (typeof obstacleWarnings !== 'undefined') obstacleWarnings.update(d, performance.now() - t0);
    if (currentLeftView === 'map') {
      drawPlannerRadar(d);
    }
  } catch (err) {
    lastTelemetry = {telemetry_unavailable: true};
    updateHud(lastTelemetry, null);
    if (typeof obstacleWarnings !== 'undefined') obstacleWarnings.update(lastTelemetry);
    if (currentLeftView === 'map') drawPlannerRadar(lastTelemetry);
  }
  finally { isPolling = false; }
}

function updateHud(d, rtt) {
  // Update Language
  const activeLang = d.device_lang || 'en';
  $('hud-lang').textContent = activeLang.toUpperCase();
  document.querySelectorAll('.lang-btn').forEach(btn => {
    btn.className = 'lang-btn' + (btn.getAttribute('data-lang') === activeLang ? ' active' : '');
  });

  // Update Vitals Meter
  $('vital-rtt').textContent = rtt != null ? rtt + 'ms' : '—';
  const v = d.vitals || {};
  $('vital-cam').textContent = v.depth_fps != null ? `${v.depth_fps} FPS · ${v.depth_latency_ms != null ? v.depth_latency_ms + 'ms' : 'unmeasured'}` : 'UNMEASURED';
  $('vital-haz').textContent = v.hazard_loop_ms != null ? `${v.hazard_loop_ms}ms` : 'UNMEASURED';
  $('vital-plan').textContent = v.planner_loop_ms != null ? `${v.planner_loop_ms}ms` : 'UNMEASURED';
  const budgetPass = v.safety_budget_pass === true;
  $('vital-budget').className = 'vital-pill ' + (budgetPass ? 'ok' : 'alert');
  $('vital-budget').textContent = v.safety_budget_pass == null ? 'LATENCY UNMEASURED' : budgetPass ? 'LATENCY <100ms' : 'LATENCY EXCEEDED';

  // Update Simulation Pill
  const simPill = $('sim-pill');
  if (d.simulation_active) {
    simPill.textContent = 'SIMULATION OVERRIDE';
    simPill.className = 'hud-pill warn';
  } else if (d.telemetry_unavailable) {
    simPill.textContent = 'TELEMETRY UNAVAILABLE';
    simPill.className = 'hud-pill alert';
  } else {
    simPill.textContent = 'LIVE TELEMETRY';
    simPill.className = 'hud-pill idle';
  }

  // Update Camera Status & Freshness
  const cam = d.camera || {};
  $('hud-cam-status').textContent = 'CAM: ' + (cam.status || 'UNKNOWN').toUpperCase();
  if (cam.status !== 'Ready' || (cam.age_s != null && cam.age_s > 1.5)) {
    $('hud-cam-status').className = 'hud-pill alert';
  } else {
    $('hud-cam-status').className = 'hud-pill ok';
  }

  // Update Guidance & Heading
  const g = d.guidance || {};
  const dirIcon = $('dir-icon');
  const dirTitle = $('dir-title');
  const dirSub = $('dir-sub');
  const pathPill = $('hud-path-status');

  if (g.active && g.path_valid) {
    pathPill.textContent = 'Active Route';
    pathPill.className = 'hud-pill ok';
  } else if (g.active && !g.path_valid) {
    pathPill.textContent = 'Searching Route';
    pathPill.className = 'hud-pill warn';
  } else {
    pathPill.textContent = 'Guidance Idle';
    pathPill.className = 'hud-pill idle';
  }

  const dirMap = {
    0: { icon: '↑', text: 'FORWARD', sub: 'Proceed straight along route', cls: 'forward' },
    1: { icon: '↰', text: 'TURN LEFT', sub: 'Bear left towards route', cls: 'turn' },
    2: { icon: '↱', text: 'TURN RIGHT', sub: 'Bear right towards route', cls: 'turn' },
    3: { icon: '↶', text: 'ROTATE LEFT', sub: 'Rotate left towards route', cls: 'turn' },
    4: { icon: '↷', text: 'ROTATE RIGHT', sub: 'Rotate right towards route', cls: 'turn' }
  };

  if (g.direction != null && dirMap[g.direction]) {
    const m = dirMap[g.direction];
    dirIcon.textContent = m.icon;
    dirIcon.className = 'dir-icon ' + m.cls;
    dirTitle.textContent = m.text;
    const tgt = g.target_label ? `Target: ${g.target_label}` : m.sub;
    dirSub.textContent = tgt;
  } else {
    dirIcon.textContent = '○';
    dirIcon.className = 'dir-icon idle';
    dirTitle.textContent = 'IDLE / CANE ONLY';
    dirSub.textContent = 'Guidance inactive — navigate by cane';
  }

  // Update Tactile Hand Servos
  const flags = g.tactile_flags || 0;
  const flagText = $('hud-tactile-flags');
  const leftBox = $('hand-left'), rightBox = $('hand-right');
  const leftTxt = $('hand-left-text'), rightTxt = $('hand-right-text');

  if (flags === 1) { // Front (both sweep)
    flagText.textContent = '0x01 FRONT (BOTH)';
    flagText.className = 'hud-pill ok';
    leftBox.className = 'hand-box active'; rightBox.className = 'hand-box active';
    leftTxt.textContent = 'SWEEP (FRONT)'; rightTxt.textContent = 'SWEEP (FRONT)';
  } else if (flags === 2) { // Left
    flagText.textContent = '0x02 LEFT HAND';
    flagText.className = 'hud-pill ok';
    leftBox.className = 'hand-box active'; rightBox.className = 'hand-box';
    leftTxt.textContent = 'SWEEP (LEFT)'; rightTxt.textContent = 'REST (90°)';
  } else if (flags === 4) { // Right
    flagText.textContent = '0x04 RIGHT HAND';
    flagText.className = 'hud-pill ok';
    leftBox.className = 'hand-box'; rightBox.className = 'hand-box active';
    leftTxt.textContent = 'REST (90°)'; rightTxt.textContent = 'SWEEP (RIGHT)';
  } else { // Neutral
    flagText.textContent = '0x00 NEUTRAL';
    flagText.className = 'hud-pill idle';
    leftBox.className = 'hand-box'; rightBox.className = 'hand-box';
    leftTxt.textContent = 'REST (90°)'; rightTxt.textContent = 'REST (90°)';
  }

  // Update Hazard Banner
  const h = d.hazard || {};
  const hCard = $('hazard-card');
  const hPill = $('hazard-pill');
  const hMsg = $('hazard-msg');
  const hSev = $('haz-sev');
  const hDist = $('haz-dist');
  const hAge = $('haz-age');

  hAge.textContent = h.age_s != null ? `${Math.round(h.age_s * 1000)}ms` : '—';
  hDist.textContent = h.distance_m != null ? `${h.distance_m.toFixed(1)}m` : '—';

  if (!h.available || (h.age_s != null && h.age_s > 0.5)) {
    hCard.className = 'hud-card hazard-card unavailable';
    hPill.textContent = 'NO SENSING';
    hPill.className = 'hud-pill alert';
    hMsg.textContent = 'Hazard detector offline or heartbeat lost (>0.5s). Guidance inhibited.';
    hSev.textContent = 'FAULT';
  } else if (h.urgent) {
    hCard.className = 'hud-card hazard-card urgent';
    hPill.textContent = 'URGENT';
    hPill.className = 'hud-pill alert';
    hMsg.textContent = h.phrase || 'Immediate obstacle in path! Guidance inhibited.';
    hSev.textContent = 'URGENT';
  } else if (h.caution) {
    hCard.className = 'hud-card hazard-card caution';
    hPill.textContent = 'CAUTION';
    hPill.className = 'hud-pill warn';
    hMsg.textContent = h.phrase || 'Corridor obstacle detected ahead.';
    hSev.textContent = 'CAUTION';
  } else {
    hCard.className = 'hud-card hazard-card clear';
    hPill.textContent = 'CLEAR';
    hPill.className = 'hud-pill ok';
    hMsg.textContent = 'No obstacle reported in the observed calibrated volume. Unobserved space is unknown.';
    hSev.textContent = 'CLEAR';
  }

  if (h.simulated === true) hMsg.textContent = 'Simulated warning. ' + hMsg.textContent;

  // Update Guardian Card
  const guard = d.guardian || {};
  $('guard-state').textContent = (guard.state || 'CLOSED').toUpperCase();
  $('guard-sms').textContent = (guard.sms_state || 'IDLE').toUpperCase();
  $('guard-obs').textContent = guard.last_observation ? '1 SAVED' : '0 SAVED';
  const guardPill = $('guardian-pill');
  if (guard.state === 'active' || guard.state === 'speaking') {
    guardPill.textContent = 'ACTIVE';
    guardPill.className = 'hud-pill ok';
  } else if (guard.state === 'cancel_window' || guard.state === 'opening') {
    guardPill.textContent = 'OPENING';
    guardPill.className = 'hud-pill warn';
  } else {
    guardPill.textContent = 'STANDBY';
    guardPill.className = 'hud-pill idle';
  }
}
setInterval(pollDebugState, 333);

</script>
<script src="/dashboard-warnings.js"></script>
<script src="/dashboard-controls.js"></script>
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
    parser.add_argument("--pi-url", help="Laptop mode: Pi bridge URL through an SSH tunnel")
    parser.add_argument("--topic", default="/camera/color/image_raw")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", default=None,
                        help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open the browser")
    parser.add_argument("--env-file", type=Path,
                        default=Path(__file__).resolve().parent.parent.parent / ".env",
                        help="Path to .env file (default: repo root .env)")
    args = parser.parse_args()
    if args.ros and args.pi_url:
        parser.error("Choose --ros on the Pi or --pi-url on the laptop")

    if args.env_file.is_file():
        for line in args.env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            k, sep, v = line.partition("=")
            if sep:
                parts = shlex.split(v, comments=True)
                os.environ.setdefault(k.strip(), parts[0] if parts else "")

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    el_key = os.environ.get("ELEVENLABS_API_KEY", "")

    if not gemini_key:
        print("⚠  GEMINI_API_KEY is not set.", file=sys.stderr)
    if not el_key:
        print("⚠  ELEVENLABS_API_KEY is not set.", file=sys.stderr)

    el_voice, el_source = resolve_voice(el_key)

    chosen_model = args.model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, args.port), Handler)
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
    elif args.pi_url:
        from companion.voice.pi_bridge import RemotePi
        server.ros_camera = RemotePi(args.pi_url)
        server.guidance = server.ros_camera

    if args.image:
        path = Path(args.image)
        if path.is_file():
            server.fallback_image = path.read_bytes()
            print(f"Fallback image: {path} ({len(server.fallback_image):,} bytes)")
        else:
            print(f"Warning: {path} not found", file=sys.stderr)

    from companion.demo.runtime import DemoRuntime
    server.demo = DemoRuntime(server)
    url = f"http://localhost:{args.port}/"
    print(f"Listening on {url}")
    print(f"Gemini model: {server.gemini.model}")
    print(f"ElevenLabs voice: {el_voice} ({el_source})")
    print("Voice output: browser for scene answers; laptop audio for Guardian")

    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.demo.close()
        server.server_close()
        if server.guidance:
            server.guidance.close()
        if server.ros_camera:
            server.ros_camera.close()


if __name__ == "__main__":
    main()
