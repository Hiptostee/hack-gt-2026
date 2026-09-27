"""Start the Pi tunnel, local Gemini voice page, and Docker RViz viewer."""

import argparse
import getpass
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parent.parent


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


def stop_process(process, graceful_interrupt=False):
    if process is None or process.poll() is not None:
        return
    if graceful_interrupt:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-ip", default=os.environ.get("PI_LAN_IP", "100.73.168.115"))
    parser.add_argument("--pi-user", default=os.environ.get("PI_SSH_USER", "raspi"))
    parser.add_argument("--esp32-host",
                        help="Single ESP32 address for this demo")
    parser.add_argument("--two-hands", action="store_true", help="Use separate left and right ESP32s")
    parser.add_argument("--left-hand")
    parser.add_argument("--right-hand")
    parser.add_argument("--no-tactile", action="store_true", help="Run without the ESP32 hands")
    args = parser.parse_args()
    two_hands = args.two_hands or args.left_hand is not None or args.right_hand is not None or (
        args.esp32_host is None and
        ("TACTILE_LEFT_HOST" in os.environ or "TACTILE_RIGHT_HOST" in os.environ)
    )
    left_hand = args.left_hand or os.environ.get("TACTILE_LEFT_HOST", "tactile-left.local")
    right_hand = args.right_hand or os.environ.get("TACTILE_RIGHT_HOST", "tactile-right.local")
    esp32_host = args.esp32_host or os.environ.get("TACTILE_ESP32_HOST", "10.89.33.186")

    if port_open(8080) or port_open(8081):
        parser.error("Port 8080 or 8081 is already in use. Stop the old web server or SSH tunnel first.")

    env = os.environ.copy()
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

    tunnel = voice = viewer = tactile = None
    compose = ["docker", "compose", "--profile", "viewer"]
    try:
        print(f"Opening SSH bridge to {args.pi_user}@{args.pi_ip}...", flush=True)
        tunnel = subprocess.Popen([
            "ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
            "-L", "8081:127.0.0.1:8081", f"{args.pi_user}@{args.pi_ip}",
        ], cwd=ROOT)
        wait_for_bridge(tunnel)

        print("Starting local Gemini voice page...", flush=True)
        voice = subprocess.Popen([
            sys.executable, "-m", "companion.voice.web_test", "--pi-url",
            "http://127.0.0.1:8081", "--no-open",
        ], cwd=ROOT, env=env)
        deadline = time.monotonic() + 15
        while not port_open(8080):
            if voice.poll() is not None:
                raise RuntimeError("The Gemini web service exited during startup.")
            if time.monotonic() > deadline:
                raise RuntimeError("The Gemini web service did not open port 8080.")
            time.sleep(0.2)

        if not args.no_tactile:
            print("Starting laptop-to-ESP32 tactile sender...", flush=True)
            tactile_command = [sys.executable, "-m", "companion.voice.tactile_link"]
            if two_hands:
                tactile_command += ["--left", left_hand, "--right", right_hand]
            else:
                tactile_command += ["--host", esp32_host]
            tactile = subprocess.Popen(tactile_command, cwd=ROOT)

        print("Starting Docker RViz viewer...", flush=True)
        viewer = subprocess.Popen(compose + ["up", "rviz-viewer", "--build"], cwd=ROOT, env=viewer_env)
        webbrowser.open("http://localhost:8080")
        print("Voice: http://localhost:8080 | RViz: localhost:5901 | Ctrl-C stops laptop services", flush=True)
        while True:
            for label, process in (("SSH tunnel", tunnel), ("voice page", voice),
                                   ("tactile sender", tactile), ("RViz viewer", viewer)):
                if process is None:
                    continue
                if process.poll() is not None:
                    raise RuntimeError(f"{label} exited with status {process.returncode}.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping laptop services...", flush=True)
    except (RuntimeError, OSError) as error:
        print(f"Launch failed: {error}", file=sys.stderr)
        return 1
    finally:
        stop_process(tactile, graceful_interrupt=True)
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
