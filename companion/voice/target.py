"""Named-object guidance without ROS: box conversion, the planner message, and
what the user hears for each planner status (companion/navigate/specs.md)."""
import json

from companion.voice.audio import FAULT, INFO

# Planner states that arrive after guidance started, not as the reply to a request.
EVENTS = {"arrived", "tracking_lost", "tracking_restored", "expired"}


def to_pixels(box_2d, width, height):
    """0-1000 [y_min, x_min, y_max, x_max] -> (x, y, width, height) in the ROS frame."""
    y_min, x_min, y_max, x_max = box_2d
    x0, x1 = round(x_min * width / 1000), round(x_max * width / 1000)
    y0, y1 = round(y_min * height / 1000), round(y_max * height / 1000)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def detection_message(request_id, label, pixel_box, stamp):
    """Matches /yolo/black_backpack. The planner parses it with regexes that
    depend on key order and on compact separators, and reads stamp as
    <sec>.<9-digit nanosec>, which a JSON float cannot carry."""
    x, y, w, h = pixel_box
    detection = json.dumps({"label": label, "confidence": 1, "x": x, "y": y,
                            "width": w, "height": h}, separators=(",", ":"))
    return (f'{{"stamp":{stamp},"request_id":{json.dumps(request_id)},'
            f'"detections":[{detection}]}}')


def distance_phrase(meters):
    if meters < 1.0:
        return "less than a meter away"
    half_meters = round(meters * 2) / 2
    return f"about {half_meters:g} meter{'' if half_meters == 1 else 's'} away"


def bearing_phrase(degrees):
    """Positive is to the user's left (camera_link y)."""
    side = "left" if degrees > 0 else "right"
    magnitude = abs(degrees)
    if magnitude <= 15:
        return "straight ahead"
    if magnitude <= 45:
        return "slightly to your " + side
    if magnitude <= 120:
        return "to your " + side
    return "behind you"


def _where(status):
    return f"{distance_phrase(status['distance_m'])}, {bearing_phrase(status['bearing_deg'])}"


def reply(label, status):
    """(sentence, guiding) for the planner's answer to a go-to request."""
    state = status.get("state") if status else None
    located = state in ("ok", "near") and "distance_m" in status and "bearing_deg" in status
    if state == "ok" and located:
        return f"I think I see the {label} {_where(status)}. Guiding you now.", True
    if state == "near" and located:
        return f"I think the {label} is {_where(status)}.", False
    if state in ("no_depth", "no_depth_frame"):
        return (f"I think I see the {label}, but I can't judge how far it is. "
                "Try again from a little closer."), False
    if state == "too_far":
        return (f"I think I see the {label}, but it's too far to judge the distance. "
                "Ask again when you're closer."), False
    if state == "stale":
        return "That took too long. Ask again so I can use a fresh view.", False
    if state == "tracking_lost":
        return "I can't track movement right now, so I can't guide you. Use your cane.", False
    if state in ("no_map", "no_camera_info"):
        return "The map isn't ready yet, so I can't plan a route.", False
    if state is None:
        return "The navigation system didn't respond, so guidance has not started.", False
    return f"I think I see the {label}, but I can't find a route to it on my map.", False


def event(label, state):
    """(sentence, priority) for a state in EVENTS."""
    if state == "arrived":
        return (f"You should be near the {label}. "
                "It should be about an arm's length ahead."), INFO
    if state == "tracking_lost":
        return "Tracking lost. Guidance paused. Use your cane.", FAULT
    if state == "tracking_restored":
        return "Tracking is back. Guidance resumed.", INFO
    return f"Guidance to the {label} timed out.", INFO
