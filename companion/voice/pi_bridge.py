"""Pi-only camera and navigation bridge. Gemini credentials stay on the laptop."""
import argparse
import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from companion.errors import AppError
from companion.voice.gemini import valid_box

# The target round trip includes the planner's reply, which may take
# guidance.STATUS_TIMEOUT_S.
TARGET_TIMEOUT_S = 6


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, status, data, content_type, headers=None):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/status":
            return self._send(200, {"camera": self.server.camera.status()}, "application/json")
        if url.path == "/frame":
            try:
                frame = self.server.camera.capture_frame()
            except AppError as error:
                return self._send(error.status, {"error": str(error)}, "application/json")
            return self._send(200, frame["jpeg"], "image/jpeg",
                              {"X-Frame-Stamp": frame["stamp"],
                               "X-Frame-Width": str(frame["width"]),
                               "X-Frame-Height": str(frame["height"])})
        if url.path == "/guidance/events":
            try:
                since = int(parse_qs(url.query).get("since", ["0"])[0])
            except ValueError:
                since = 0
            return self._send(200, {"events": self.server.guidance.events_since(since)},
                              "application/json")
        self.send_error(404)

    def do_POST(self):
        if self.path == "/guidance/start":
            answer = self.server.guidance.start()
        elif self.path == "/guidance/stop":
            self.server.guidance.stop()
            answer = "Guidance stopped."
        elif self.path == "/guidance/target":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                box = valid_box(body["box_2d"])
                if box is None:
                    raise ValueError("box_2d")
                answer = self.server.guidance.go_to(
                    str(body["label"])[:40], box, str(body["stamp"]),
                    int(body["width"]), int(body["height"]))
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": "Bad target request"}, "application/json")
        else:
            return self.send_error(404)
        self._send(200, {"answer": answer}, "application/json")


class RemotePi:
    """Laptop-side client for a Pi bridge reached through an SSH port forward."""

    def __init__(self, url):
        if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
            raise ValueError("--pi-url must point to a localhost SSH tunnel")
        self.url = url.rstrip("/")
        self.lock = threading.Lock()
        self.recent = deque(maxlen=4)

    def _request(self, path, method="GET", body=None, timeout=3):
        """Returns (body bytes, response headers)."""
        try:
            data = json.dumps(body).encode() if body is not None else None
            request = Request(self.url + path, data=data, method=method,
                              headers={"Content-Type": "application/json"} if data else {})
            with urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers
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
            return json.loads(self._request("/status")[0])["camera"]
        except (AppError, ValueError, KeyError):
            return "Pi bridge unavailable"

    def capture(self):
        return self.capture_frame()["jpeg"], None

    def capture_frame(self):
        jpeg, headers = self._request("/frame")
        frame = {"jpeg": jpeg, "captured_at": None, "stamp": headers.get("X-Frame-Stamp")}
        try:
            frame["width"] = int(headers.get("X-Frame-Width"))
            frame["height"] = int(headers.get("X-Frame-Height"))
        except (TypeError, ValueError):
            frame["stamp"] = None
        with self.lock:
            self.recent.append(frame)
        return frame

    def frame_info(self, jpeg):
        with self.lock:
            frame = next((f for f in reversed(self.recent) if f["jpeg"] is jpeg), None)
        return frame if frame and frame["stamp"] else None

    def start(self):
        try:
            return json.loads(self._request("/guidance/start", "POST")[0])["answer"]
        except AppError as error:
            return str(error)

    def go_to(self, label, box_2d, stamp, width, height):
        body = {"label": label, "box_2d": box_2d, "stamp": stamp,
                "width": width, "height": height}
        try:
            reply = self._request("/guidance/target", "POST", body, timeout=TARGET_TIMEOUT_S)
            return json.loads(reply[0])["answer"]
        except AppError as error:
            return str(error)
        except (ValueError, KeyError):
            return "The navigation system didn't respond, so guidance has not started."

    def events_since(self, n):
        try:
            return json.loads(self._request(f"/guidance/events?since={int(n)}")[0])["events"]
        except (AppError, ValueError, KeyError):
            return []

    def stop(self):
        try:
            self._request("/guidance/stop", "POST")
        except AppError:
            pass

    def close(self):
        pass


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
