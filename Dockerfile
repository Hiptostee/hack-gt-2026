FROM ros:jazzy-ros-base-noble

LABEL org.opencontainers.image.title="RealSense RTAB-Map ROS 2 development environment" \
      org.opencontainers.image.description="ROS 2 Jazzy RGB-D SLAM with Intel RealSense and RTAB-Map"

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=jazzy \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    dbus-x11 \
    iproute2 \
    libopencv-core-dev \
    libopencv-dnn-dev \
    libopencv-imgproc-dev \
    libgl1-mesa-dri \
    mesa-utils \
    novnc \
    python3-colcon-common-extensions \
    ros-jazzy-realsense2-camera \
    ros-jazzy-realsense2-description \
    ros-jazzy-rtabmap-ros \
    ros-jazzy-robot-localization \
    ros-jazzy-rviz2 \
    tigervnc-standalone-server \
    tigervnc-tools \
    usbutils \
    util-linux \
    websockify \
    xfce4 \
    xfce4-terminal \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/models && \
    curl -L --fail --retry 3 \
      'https://huggingface.co/opencv/object_detection_yolox/resolve/main/object_detection_yolox_2022nov.onnx?download=true' \
      -o /opt/models/yolox.onnx

WORKDIR /workspace/ros_ws
COPY ros_ws/src src

RUN . /opt/ros/jazzy/setup.sh && \
    colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

COPY scripts/container-entrypoint.sh /usr/local/bin/container-entrypoint.sh
RUN chmod +x /usr/local/bin/container-entrypoint.sh

ENV DISPLAY=:1 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    QT_X11_NO_MITSHM=1 \
    VNC_RESOLUTION=1600x1000

EXPOSE 5901 6080
ENTRYPOINT ["/usr/local/bin/container-entrypoint.sh"]
