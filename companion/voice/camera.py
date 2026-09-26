"""Frame sources. Both return (jpeg_bytes, captured_at) or raise AppError."""
from pathlib import Path

from companion.errors import AppError


class StaticImageCamera:
    """Dev stand-in for machines with no ROS stack. Never reports a capture time."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise AppError("Image file not found: " + str(self.path), 503)
        data = self.path.read_bytes()
        if not data.startswith(b"\xff\xd8\xff"):
            raise AppError("Dev image must be a JPEG.", 503)
        self.data = data

    def status(self):
        return "Static dev image, not a live camera"

    def capture(self):
        return self.data, None

    def close(self):
        pass


class NoCamera:
    def status(self):
        return "No camera configured"

    def capture(self):
        raise AppError("No camera is configured.", 503)

    def close(self):
        pass


def open_camera(ros_topic=None, image_path=None):
    if ros_topic:
        from companion.ros_camera import RosCamera

        return RosCamera(ros_topic)
    if image_path:
        return StaticImageCamera(image_path)
    return NoCamera()
