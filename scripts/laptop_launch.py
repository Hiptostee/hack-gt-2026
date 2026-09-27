"""Start Beacon on the laptop; optional Pi tunnel and Docker RViz viewer."""

import argparse
import getpass
import os
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parent.parent


def load_env(path, env):
    """Read literal KEY=value entries without executing shell code."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, sep, raw = line.partition("=")
        key = key.strip()
        if not sep or not key.replace("_", "").isalnum():
            raise ValueError("Invalid .env entry; use KEY=value")
        value = shlex.split(raw, comments=True)
        if len(value) > 1:
            raise ValueError("Quote values containing spaces in .env")
        env.setdefault(key, value[0] if value else "")


def parser_for_demo():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-ip", default=os.environ.get("PI_LAN_IP", "100.73.168.115"))
    parser.add_argument("--pi-user", default=os.environ.get("PI_SSH_USER", "raspi"))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--viewer", action="store_true", help="Also launch Docker RViz (optional)")
    parser.add_argument("--standalone", action="store_true", help="Laptop webcam rehearsal, without Pi/depth routing")
    parser.add_argument("--image", help="Static rehearsal JPEG; requires --standalone")
    parser.add_argument("--no-open", action="store_true")
    return parser


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def wait_for_bridge(tunnel, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            raise RuntimeError("SSH tunnel exited. Check the Pi address and SSH login.")
        try:
            with urlopen("http://127.0.0.1:8081/status", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Pi bridge did not answer on port 8081. Start scripts/pi_launch.sh on the Pi first.")


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    parser = parser_for_demo()
    args = parser.parse_args()
    if args.image and not args.standalone:
        parser.error("--image requires --standalone; live Pi failures never use an old image")
    if args.viewer and args.standalone:
        parser.error("--viewer needs the Pi connection")

    if port_open(8080) or (not args.standalone and port_open(8081)):
        parser.error("Port 8080 or 8081 is already in use. Stop the old web server or SSH tunnel first.")

    env = os.environ.copy()
    try:
        load_env(args.env_file, env)
    except ValueError as error:
        parser.error(str(error))
    env["GUARDIAN_SMS"] = "fake"
    if not env.get("GEMINI_API_KEY"):
        if not sys.stdin.isatty():
            parser.error("Set GEMINI_API_KEY before starting the laptop launcher.")
        env["GEMINI_API_KEY"] = getpass.getpass("Gemini API key: ").strip()
        if not env["GEMINI_API_KEY"]:
            parser.error("A Gemini API key is required.")
    env["PI_LAN_IP"] = args.pi_ip
    env["ROS_DOMAIN_ID"] = "42"
    env["ROS_DISCOVERY_SERVER"] = f"{args.pi_ip}:11811"
    viewer_env = {key: value for key, value in env.items()
                  if key not in {"GEMINI_API_KEY", "ELEVENLABS_API_KEY"}}

    tunnel = voice = viewer = None
    compose = ["docker", "compose", "--profile", "viewer"]
    try:
        if not args.standalone:
            print(f"Opening SSH bridge to {args.pi_user}@{args.pi_ip}...", flush=True)
            tunnel = subprocess.Popen([
                "ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
                "-L", "8081:127.0.0.1:8081", f"{args.pi_user}@{args.pi_ip}",
            ], cwd=ROOT)
            wait_for_bridge(tunnel)

        print("Starting local Gemini voice page...", flush=True)
        voice_command = [sys.executable, "-m", "companion.voice.web_test", "--no-open"]
        if not args.standalone:
            voice_command += ["--pi-url", "http://127.0.0.1:8081"]
        if args.image:
            voice_command += ["--image", args.image]
        voice = subprocess.Popen(voice_command, cwd=ROOT, env=env)
        deadline = time.monotonic() + 15
        while not port_open(8080):
            if voice.poll() is not None:
                raise RuntimeError("The Gemini web service exited during startup.")
            if time.monotonic() > deadline:
                raise RuntimeError("The Gemini web service did not open port 8080.")
            time.sleep(0.2)

        if args.viewer:
            print("Starting Docker RViz viewer...", flush=True)
            viewer = subprocess.Popen(compose + ["up", "rviz-viewer", "--build"], cwd=ROOT, env=viewer_env)
        if not args.no_open:
            webbrowser.open("http://localhost:8080/")
        print("Beacon: http://localhost:8080/ | Ctrl-C stops laptop services", flush=True)
        while True:
            for label, process in (("SSH tunnel", tunnel), ("voice page", voice), ("RViz viewer", viewer)):
                if process is not None and process.poll() is not None:
                    raise RuntimeError(f"{label} exited with status {process.returncode}.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping laptop services...", flush=True)
    except (RuntimeError, OSError) as error:
        print(f"Launch failed: {error}", file=sys.stderr)
        return 1
    finally:
        stop_process(voice)
        stop_process(tunnel)
        stop_process(viewer)
        if viewer is not None:
            try:
                subprocess.run(compose + ["stop", "rviz-viewer"], cwd=ROOT,
                               env=viewer_env, check=False)
            except OSError as error:
                print(f"Could not stop Docker viewer: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
