import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.errors import AppError


def get_debug_state(server):
    now = time.monotonic()

    # 1. Camera
    camera_status = "No camera"
    camera_age = None
    width, height = 640, 480
    if hasattr(server, "camera") and server.camera is not None:
        try:
            camera_status = server.camera.status()
            if hasattr(server.camera, "received") and server.camera.received:
                camera_age = round(now - server.camera.received, 3)
            if hasattr(server.camera, "frame") and server.camera.frame is not None:
                width = getattr(server.camera.frame, "width", 640)
                height = getattr(server.camera.frame, "height", 480)
        except Exception:
            camera_status = "Error querying camera"

    # 2. Guidance
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
    if hasattr(server, "guidance") and server.guidance is not None:
        try:
            if hasattr(server.guidance, "snapshot"):
                guidance_data = server.guidance.snapshot()
        except Exception:
            pass

    # 3. Hazard
    hazard_data = {
        "available": False,
        "severity": "none",
        "urgent": False,
        "caution": False,
        "direction": "clear",
        "distance_m": None,
        "phrase": None,
        "sensor_health": "unknown",
        "age_s": None,
    }
    if hasattr(server, "hazard_state") and server.hazard_state is not None:
        try:
            if hasattr(server.hazard_state, "snapshot"):
                hazard_data = server.hazard_state.snapshot(now)
        except Exception:
            pass

    # 4. Guardian
    guardian_data = {
        "state": "closed",
        "is_speaking": False,
        "sms_state": "idle",
        "last_observation": None,
        "trail_count": 0,
    }
    if hasattr(server, "guardian") and server.guardian is not None:
        try:
            if hasattr(server.guardian, "snapshot"):
                guardian_data = server.guardian.snapshot()
        except Exception:
            pass

    try:
        from companion.i18n import get as i18n_get
    except ImportError:
        i18n_get = None

    lang = getattr(server, "lang", os.environ.get("DEVICE_LANG", "en"))

    vitals = {
        "depth_fps": 30,
        "depth_latency_ms": 33,
        "hazard_loop_ms": 16,
        "planner_loop_ms": 42,
        "safety_budget_ms": 91,
        "safety_budget_pass": True,
    }

    sim = getattr(server, "simulation", None)
    if not isinstance(sim, dict):
        sim = {}
    if "hazard" in sim:
        hazard_data = dict(sim["hazard"])
    if "guidance" in sim:
        guidance_data = dict(sim["guidance"])
    if sim.get("fault"):
        hazard_data = {
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

    return {
        "timestamp": round(now, 3),
        "device_lang": lang,
        "vitals": vitals,
        "simulation_active": bool(sim),
        "camera": {
            "status": camera_status,
            "age_s": camera_age,
            "width": width,
            "height": height,
        },
        "guidance": guidance_data,
        "hazard": hazard_data,
        "guardian": guardian_data,
    }


def apply_simulation(server, action, value):
    if not hasattr(server, "simulation") or not isinstance(server.simulation, dict):
        server.simulation = {}

    try:
        from companion.i18n import get as i18n_get
    except ImportError:
        i18n_get = None

    lang = getattr(server, "lang", os.environ.get("DEVICE_LANG", "en"))

    if action == "reset":
        server.simulation.clear()
        return {"status": "reset", "active": False}

    if action == "hazard":
        if value == "urgent_head":
            phrase = i18n_get("head_obstacle_ahead", lang) if i18n_get else "Head-height obstacle ahead."
            server.simulation["hazard"] = {
                "available": True,
                "severity": "urgent",
                "urgent": True,
                "caution": False,
                "direction": "ahead",
                "distance_m": 0.8,
                "phrase": f"Stop! {phrase}",
                "sensor_health": "ok",
                "age_s": 0.02,
            }
            if "guidance" in server.simulation:
                server.simulation["guidance"]["tactile_flags"] = 0
                server.simulation["guidance"]["tactile_label"] = "neutral"
                server.simulation["guidance"]["hazard_permitted"] = False
        elif value == "caution_corridor":
            phrase = i18n_get("obstacle_ahead", lang) if i18n_get else "Obstacle ahead."
            server.simulation["hazard"] = {
                "available": True,
                "severity": "caution",
                "urgent": False,
                "caution": True,
                "direction": "right",
                "distance_m": 1.7,
                "phrase": phrase,
                "sensor_health": "ok",
                "age_s": 0.03,
            }
        elif value == "dropoff":
            server.simulation["hazard"] = {
                "available": True,
                "severity": "urgent",
                "urgent": True,
                "caution": False,
                "direction": "ahead",
                "distance_m": 0.6,
                "phrase": "Stop! Drop-off ahead.",
                "sensor_health": "ok",
                "age_s": 0.02,
            }
            if "guidance" in server.simulation:
                server.simulation["guidance"]["tactile_flags"] = 0
                server.simulation["guidance"]["tactile_label"] = "neutral"
                server.simulation["guidance"]["hazard_permitted"] = False
        elif value == "clear":
            server.simulation["hazard"] = {
                "available": True,
                "severity": "none",
                "urgent": False,
                "caution": False,
                "direction": "clear",
                "distance_m": None,
                "phrase": None,
                "sensor_health": "ok",
                "age_s": 0.01,
            }
            if "guidance" in server.simulation:
                server.simulation["guidance"]["hazard_permitted"] = True

    elif action == "direction":
        dir_map = {
            "forward": (0, "forward", 1, "front"),
            "left": (1, "left", 2, "left"),
            "right": (2, "right", 4, "right"),
            "stop": (3, "stop", 0, "neutral"),
            "neutral": (None, "idle", 0, "neutral"),
        }
        d_code, d_lbl, flags, t_lbl = dir_map.get(value, (None, "idle", 0, "neutral"))
        server.simulation["guidance"] = {
            "active": value != "neutral",
            "path_valid": value != "stop",
            "hazard_permitted": True,
            "direction": d_code,
            "direction_label": d_lbl,
            "tactile_flags": flags,
            "tactile_label": t_lbl,
            "target_label": "backpack",
            "distance_m": 2.4,
            "bearing_deg": -10.0 if value == "left" else (10.0 if value == "right" else 0.0),
            "age_s": 0.02,
        }

    elif action == "fault":
        server.simulation["fault"] = True
        server.simulation["hazard"] = {
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
        if "guidance" in server.simulation:
            server.simulation["guidance"]["hazard_permitted"] = False
            server.simulation["guidance"]["tactile_flags"] = 0

    return {"status": "ok", "active": bool(server.simulation), "simulation": server.simulation}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, status, data, content_type):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            return self._send(200, {"camera": self.server.camera.status()}, "application/json")
        if self.path == "/guidance/state":
            snapshot = self.server.guidance.snapshot() if hasattr(self.server.guidance, "snapshot") else {}
            return self._send(200, snapshot, "application/json")
        if self.path == "/debug/state":
            return self._send(200, get_debug_state(self.server), "application/json")
        if self.path == "/frame":
            try:
                image, _captured_at = self.server.camera.capture()
                return self._send(200, image, "image/jpeg")
            except AppError as error:
                return self._send(error.status, {"error": str(error)}, "application/json")
        self.send_error(404)

    def do_POST(self):
        if self.path == "/guidance/start":
            answer = self.server.guidance.start()
            return self._send(200, {"answer": answer}, "application/json")
        if self.path == "/guidance/stop":
            self.server.guidance.stop()
            return self._send(200, {"answer": "Guidance stopped."}, "application/json")
        if self.path == "/debug/simulate":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length)) if length else {}
                res = apply_simulation(self.server, data.get("action", ""), data.get("value", ""))
                return self._send(200, res, "application/json")
            except Exception as e:
                return self._send(400, {"error": str(e)}, "application/json")
        if self.path == "/debug/language":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length)) if length else {}
                lang = data.get("lang", "en")
                if lang in ("en", "ko", "zh", "ja", "es"):
                    os.environ["DEVICE_LANG"] = lang
                    self.server.lang = lang
                    return self._send(200, {"device_lang": lang}, "application/json")
                return self._send(400, {"error": "Invalid language code"}, "application/json")
            except Exception as e:
                return self._send(400, {"error": str(e)}, "application/json")
        self.send_error(404)


class RemotePi:
    """Laptop-side client for a Pi bridge reached through an SSH port forward."""

    def __init__(self, url):
        if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
            raise ValueError("--pi-url must point to a localhost SSH tunnel")
        self.url = url.rstrip("/")

    def _request(self, path, method="GET"):
        try:
            request = Request(self.url + path, method=method)
            with urlopen(request, timeout=3) as response:
                return response.read()
        except HTTPError as error:
            try:
                detail = json.load(error).get("error", "Pi camera unavailable")
            except (ValueError, AttributeError):
                detail = "Pi bridge request failed"
            raise AppError(detail, error.code) from None
        except (URLError, TimeoutError, OSError):
            raise AppError("Cannot reach the Pi bridge. Check the SSH tunnel.", 503) from None

    def status(self):
        try:
            return json.loads(self._request("/status"))["camera"]
        except (AppError, ValueError, KeyError):
            return "Pi bridge unavailable"

    def capture(self):
        return self._request("/frame"), None

    def start(self):
        try:
            return json.loads(self._request("/guidance/start", "POST"))["answer"]
        except AppError as error:
            return str(error)

    def stop(self):
        try:
            self._request("/guidance/stop", "POST")
        except AppError:
            pass

    def debug_state(self):
        try:
            return json.loads(self._request("/debug/state"))
        except (AppError, ValueError, KeyError):
            return None

    def guidance_state(self):
        try:
            return json.loads(self._request("/guidance/state"))
        except (AppError, ValueError, KeyError):
            return None

    def simulate(self, action, value):
        try:
            body = json.dumps({"action": action, "value": value}).encode()
            request = Request(self.url + "/debug/simulate", data=body,
                              headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=3) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def set_language(self, lang):
        try:
            body = json.dumps({"lang": lang}).encode()
            request = Request(self.url + "/debug/language", data=body,
                              headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=3) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def close(self):
        pass  # Nothing held open; each request is its own connection.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default="/camera/color/image_raw")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    from companion.ros_camera import RosCamera
    from companion.voice.guidance import RosGuidance

    camera = RosCamera(args.topic)
    guidance = RosGuidance()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.camera = camera
    server.guidance = guidance
    print(f"Pi bridge listening on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        guidance.close()
        camera.close()


if __name__ == "__main__":
    main()
