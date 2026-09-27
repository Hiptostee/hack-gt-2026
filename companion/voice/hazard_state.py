"""Strict Stage A snapshot contract and monotonic lifetime policy (no ROS/audio imports)."""
from collections import OrderedDict, deque
from dataclasses import dataclass
import json
import math
import os
import threading

HEARTBEAT_S = 0.5
HEALTH_VALUES = {"ok", "degraded", "unavailable"}


@dataclass(frozen=True)
class Alert:
    key: str
    phrase: str
    priority: int
    expires_at: float
    repeat_s: float


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def parse_snapshot(raw, ros_ns, received):
    if not isinstance(raw, str) or len(raw) > 16384:
        raise ValueError("invalid snapshot size")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    data = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported schema")
    session = data.get("source_session")
    if not isinstance(session, str) or not 1 <= len(session) <= 128:
        raise ValueError("invalid source session")
    if not integer(data.get("sequence")):
        raise ValueError("invalid sequence")
    published = data.get("published_stamp_ns")
    if not integer(published, 1) or not 0 <= ros_ns - published <= 500_000_000:
        raise ValueError("stale/future publication")
    health = data.get("health")
    if not isinstance(health, dict) or set(health) != {"depth", "body_pose", "floor", "labels"}:
        raise ValueError("invalid health")
    if any(not isinstance(x, str) or x not in HEALTH_VALUES for x in health.values()):
        raise ValueError("invalid health enum")
    events = data.get("events")
    if not isinstance(events, list) or len(events) > 8:
        raise ValueError("too many events")
    alerts, ids = [], set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("invalid event")
        identity = event.get("id")
        if not isinstance(identity, str) or not 1 <= len(identity) <= 128 or identity in ids:
            raise ValueError("invalid event id")
        ids.add(identity)
        observed, ttl = event.get("observed_stamp_ns"), event.get("ttl_ms")
        if not integer(observed, 1) or observed > published or observed > ros_ns:
            raise ValueError("invalid observation stamp")
        if not integer(ttl, 1) or ttl > 250:
            raise ValueError("invalid ttl")
        if (event.get("kind") != "upper_body_obstacle" or
                event.get("severity") not in ("urgent", "caution") or
                event.get("direction") not in ("left", "center", "right") or
                event.get("height_band") not in ("head", "torso") or
                event.get("evidence") != "depth_cluster" or event.get("label") is not None):
            raise ValueError("unsupported Stage A evidence/enums")
        distance = event.get("distance_m")
        if distance is not None and (type(distance) not in (int, float) or
                                     not math.isfinite(distance) or distance <= 0):
            raise ValueError("invalid distance")
        expires = received + ttl / 1000 - (ros_ns - observed) / 1e9
        if expires <= received:
            continue  # Republishing does not renew evidence.
        urgent = event["severity"] == "urgent"
        band, direction = event["height_band"], event["direction"]
        phrase = f"{'urgent' if urgent else 'caution'}:{band}:{direction}"
        alerts.append((Alert(session + ":" + identity, phrase, 0 if urgent else 2,
                             expires, 2.0 if urgent else 3.0),
                       distance if distance is not None else math.inf))
    alerts.sort(key=lambda entry: (entry[0].priority, entry[1]))
    return data, [entry[0] for entry in alerts]


class HazardState:
    """One replaceable snapshot; clock/source guards and bounded repeat history."""
    def __init__(self):
        self.lock = threading.RLock()
        self.session = None
        self.retired = deque(maxlen=32)
        self.sequence = -1
        self.received = float("-inf")
        self.last_clock = None
        self.health = {}
        self.alerts = []
        self.good_frames = 0
        self.reported_health = None
        self.ever_ready = False
        self.spoken = OrderedDict()
        self.last_activity = 0.0

    def _clock(self, ros_ns, mono):
        jumped = False
        if self.last_clock:
            old_ros, old_mono = self.last_clock
            jumped = abs((ros_ns - old_ros) / 1e9 - (mono - old_mono)) > 0.1
        self.last_clock = (ros_ns, mono)
        if jumped:
            self.received = float("-inf")
            self.health, self.alerts = {}, []
            self.good_frames = 0
        return jumped

    def receive(self, raw, ros_ns, mono):
        with self.lock:
            if self._clock(ros_ns, mono):
                return False
            try:
                data, alerts = parse_snapshot(raw, ros_ns, mono)
            except (ValueError, TypeError, OverflowError, RecursionError):
                return False  # Invalid traffic does not refresh the heartbeat.
            session, sequence = data["source_session"], data["sequence"]
            if session in self.retired or (session == self.session and sequence <= self.sequence):
                return False
            if session != self.session:
                if self.session is not None:
                    self.retired.append(self.session)
                self.good_frames = 0
            self.session, self.sequence = session, sequence
            # Transport delay also consumes the health lease; receipt cannot
            # grant another full heartbeat interval to an old snapshot.
            self.received = mono - (ros_ns - data["published_stamp_ns"]) / 1e9
            self.health, self.alerts = data["health"], alerts
            good = self.health["depth"] == self.health["body_pose"] == "ok"
            self.good_frames = self.good_frames + 1 if good else 0
            return True

    def poll(self, ros_ns, mono, output_ready=True):
        with self.lock:
            self._clock(ros_ns, mono)
            live = mono - self.received < HEARTBEAT_S
            healthy = (live and output_ready and self.good_frames >= 3 and
                       self.health.get("depth") == self.health.get("body_pose") == "ok")
            alerts = [a for a in self.alerts if a.expires_at > mono] if live else []
            permitted = healthy and not any(a.priority == 0 for a in alerts)
            if not healthy:
                alerts.append(Alert("health:fault", "unavailable", 1, mono + 0.5, 15.0))
            elif self.reported_health != "ready":
                alerts.append(Alert("health:ready", "restored" if self.ever_ready else "ready",
                                    4, mono + 0.5, 0.0))
            elif mono - self.last_activity >= 15:
                alerts.append(Alert("health:alive", "alive", 4, mono + 0.5, 15.0))
            alerts.sort(key=lambda a: a.priority)
            for alert in alerts:
                prior = self.spoken.get(alert.key)
                if prior is None or alert.priority < prior[1] or mono - prior[0] >= alert.repeat_s:
                    return permitted, alert
            return permitted, None

    def spoken_alert(self, alert, mono):
        with self.lock:
            self.spoken[alert.key] = (mono, alert.priority)
            self.spoken.move_to_end(alert.key)
            while len(self.spoken) > 64:
                self.spoken.popitem(last=False)
            self.last_activity = mono
            if alert.phrase in ("ready", "restored"):
                self.reported_health = "ready"
                self.ever_ready = True
                self.spoken.pop("health:fault", None)
            elif alert.phrase == "unavailable":
                self.reported_health = "fault"

    def snapshot(self, mono):
        with self.lock:
            has_received = not math.isinf(self.received)
            live = (mono - self.received < HEARTBEAT_S) if has_received else False
            age_s = round(mono - self.received, 3) if has_received else None
            active_alerts = [a for a in self.alerts if a.expires_at > mono] if live else []
            urgent = any(a.priority == 0 for a in active_alerts)
            caution = any(a.priority == 2 for a in active_alerts)
            severity = "urgent" if urgent else ("caution" if caution else "none")
            health = self.health.copy() if self.health else {}
            phrase = active_alerts[0].phrase if active_alerts else None
            from companion.i18n import get, get_hazard_key
            direction = "ahead" if active_alerts else "unknown"
            if phrase and len(phrase.split(":")) == 3:
                severity_key, band, direction = phrase.split(":")
                phrase = get(get_hazard_key(severity_key, band, direction),
                             os.environ.get("DEVICE_LANG", "en"))
            return {
                "available": live and health.get("depth") == health.get("body_pose") == "ok",
                "severity": severity,
                "urgent": urgent,
                "caution": caution,
                "direction": direction,
                "phrase": phrase,
                "warning_ttl_ms": max(0, int((active_alerts[0].expires_at - mono) * 1000)) if active_alerts else 0,
                "unavailable_phrase": get("hazard_unavailable", os.environ.get("DEVICE_LANG", "en")),
                "sensor_health": self.health.get("depth", "unknown") if self.health else "unknown",
                "age_s": age_s,
            }
