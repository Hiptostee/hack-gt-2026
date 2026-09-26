"""Process-level regressions; Python is used only by this optional test."""
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time

sender, listener = sys.argv[1:]

# Exercise hotspot updates without touching the host network or real nmcli.
with tempfile.TemporaryDirectory() as directory:
    mock = Path(directory) / "nmcli"
    log = Path(directory) / "calls.jsonl"
    mock.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["NMCLI_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if "show" in args:
    if os.environ["NMCLI_EXISTS"] == "1":
        print("tactile-ap")
        sys.exit(0)
    sys.exit(10)
if "modify" in args and os.environ.get("NMCLI_FAIL_MODIFY") == "1":
    sys.exit(2)
''')
    mock.chmod(0o755)
    hotspot = Path(__file__).resolve().parents[1] / "scripts" / "pi_hotspot.sh"
    for exists, fail, password, channel in (
        ("1", "0", "test-password", "6"),
        ("0", "0", "test-password", "6"),
        ("1", "1", "test-password", "6"),
        ("1", "0", "short", "6"),
        ("1", "0", "test-password", "99"),
    ):
        log.write_text("")
        env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                   NMCLI_LOG=str(log), NMCLI_EXISTS=exists, NMCLI_FAIL_MODIFY=fail,
                   TACTILE_AP_PSK=password, TACTILE_AP_CHANNEL=channel)
        result = subprocess.run(["bash", str(hotspot), "up"], env=env,
                                capture_output=True, timeout=2)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any("delete" in call for call in calls), calls
        invalid = password == "short" or channel == "99"
        if invalid:
            assert result.returncode == 2 and not calls, (result, calls)
        elif fail == "1":
            assert result.returncode != 0 and not any("up" in call for call in calls), calls
        else:
            assert result.returncode == 0, result
            operation = "modify" if exists == "1" else "add"
            assert any(call[:2] == ["connection", operation] for call in calls), calls
            assert calls[-1] == ["connection", "up", "tactile-ap"], calls


def read_line(process, timeout=2):
    # Use unbuffered pipes so select also works after earlier line reads.
    deadline = time.monotonic() + timeout
    line = b""
    while not line.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        assert remaining > 0 and select.select([process.stderr], [], [], remaining)[0], line
        char = process.stderr.read(1)
        assert char, (process.poll(), line)
        line += char
    return line.decode()


for value in ("nan", "inf", "1e-300", "20junk", "2", "1001"):
    result = subprocess.run([sender, "--rate", value], input=b"q\n", capture_output=True, timeout=2)
    assert result.returncode == 2, (value, result)
for value in ("0", "-1", "65536", "4210junk", ""):
    for args in ([listener, "--port", value], [sender, "--left", "127.0.0.1:" + value]):
        result = subprocess.run(args, input=b"q\n", capture_output=True, timeout=2)
        assert result.returncode == 2, (args, result)

# Both hands receive the same bytes, including the neutral shutdown burst.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as left, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as right:
    for sock in (left, right):
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(2)
    args = [sender, "--left", f"127.0.0.1:{left.getsockname()[1]}",
            "--right", f"127.0.0.1:{right.getsockname()[1]}"]
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        try:
            process.stdin.write(b"f\n")
            while True:
                data = left.recv(64)
                assert data == right.recv(64)
                if data[2] == 1:
                    break
            process.stdin.write(b"partial")  # no newline; shutdown must not wait for it
            time.sleep(0.05)
            process.send_signal(stop_signal)
            assert process.wait(timeout=2) == 0
            for sock in (left, right):
                packets = []
                sock.settimeout(0.1)
                while True:
                    try:
                        packets.append(sock.recv(64))
                    except socket.timeout:
                        break
                assert len(packets) >= 3 and all(p[2] == 0 for p in packets[-3:]), packets
                sock.settimeout(2)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

# The loss/gap report must divide by actual gaps, excluding failsafe resets.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
    udp.bind(("127.0.0.1", 0))
    port = udp.getsockname()[1]
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
    process = subprocess.Popen([listener, "--port", str(port), "--verbose"], stderr=subprocess.PIPE, bufsize=0)
    try:
        assert "listening" in read_line(process)
        def send(seq, flags=1):
            udp.sendto(bytes((0xA5, seq, flags)), ("127.0.0.1", port))
        send(100)
        time.sleep(0.04)
        send(101)
        send(101, 0)  # duplicate
        udp.sendto(b"bad packet", ("127.0.0.1", port))
        time.sleep(0.6)
        send(0)
        time.sleep(0.04)
        send(1)
        lines = []
        while not lines or "5 s:" not in lines[-1]:
            lines.append(read_line(process, timeout=6))
        report = lines[-1]
        assert "4 ok, 0 lost" in report and "1 stale, 1 malformed" in report, report
        assert any("FAILSAFE" in line for line in lines), lines
        times = [float(re.search(r"\[\s*([\d.]+)\]", line)[1]) for line in lines if "127.0.0.1 seq=" in line]
        expected = ((times[1] - times[0]) + (times[3] - times[2])) * 500
        measured = float(re.search(r"gap avg ([\d.]+)", report)[1])
        assert abs(expected - measured) < 2, (expected, measured, lines)
        # The average excludes resets, but max must include ongoing outages.
        max_gap = float(re.search(r"max ([\d.]+) ms", report)[1])
        assert max_gap >= 4000, report
    finally:
        process.terminate()
        process.communicate(timeout=2)

print("tool regressions passed")
