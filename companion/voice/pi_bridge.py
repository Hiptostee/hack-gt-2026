"""Pi-only camera and navigation bridge. Gemini credentials stay on the laptop."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.errors import AppError


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
        elif self.path == "/guidance/stop":
            self.server.guidance.stop()
            answer = "Guidance stopped."
        else:
            return self.send_error(404)
        self._send(200, {"answer": answer}, "application/json")


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
