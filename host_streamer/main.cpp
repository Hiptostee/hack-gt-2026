#include <librealsense2/rs.hpp>

#include <arpa/inet.h>
#include <algorithm>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>

#include "realsense_mapper/rgbd_protocol.hpp"

namespace
{
using realsense_mapper::RgbdFrameHeader;

volatile std::sig_atomic_t running = 1;
volatile std::sig_atomic_t listening_socket = -1;

void stop(int)
{
  running = 0;
  // close() is async-signal-safe and wakes the blocking accept() call so the
  // streamer can actually terminate and release TCP/USB resources.
  if (listening_socket >= 0) {
    close(static_cast<int>(listening_socket));
    listening_socket = -1;
  }
}

bool send_all(int socket_fd, const void * data, std::size_t bytes)
{
  const auto * cursor = static_cast<const std::uint8_t *>(data);
  while (bytes > 0 && running) {
    const auto sent = send(socket_fd, cursor, bytes, 0);
    if (sent <= 0) {
      return false;
    }
    cursor += sent;
    bytes -= static_cast<std::size_t>(sent);
  }
  return bytes == 0;
}

int make_server(std::uint16_t port)
{
  const int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    throw std::runtime_error("socket() failed");
  }
  int enabled = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));

  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_ANY);
  address.sin_port = htons(port);
  if (bind(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0 ||
    listen(fd, 1) < 0)
  {
    close(fd);
    throw std::runtime_error("Could not listen on TCP port " + std::to_string(port));
  }
  return fd;
}

void set_supported_option(rs2::sensor & sensor, rs2_option option, float requested)
{
  if (!sensor.supports(option)) {
    return;
  }
  const auto range = sensor.get_option_range(option);
  sensor.set_option(option, std::clamp(requested, range.min, range.max));
}
}  // namespace

int main(int argc, char ** argv)
{
  const auto port = static_cast<std::uint16_t>(argc > 1 ? std::stoi(argv[1]) : 50051);
  std::signal(SIGINT, stop);
  std::signal(SIGTERM, stop);
  std::signal(SIGPIPE, SIG_IGN);

  try {
    rs2::config config;
    // Use the standard matching D415 RGB-D profile. This camera/firmware does
    // not expose 640x360 as a compatible RGB8 + Z16 pair.
    config.enable_stream(RS2_STREAM_COLOR, 640, 480, RS2_FORMAT_RGB8, 30);
    config.enable_stream(RS2_STREAM_DEPTH, 640, 480, RS2_FORMAT_Z16, 30);
    rs2::pipeline pipeline;
    const auto profile = pipeline.start(config);
    const auto device = profile.get_device();
    const auto depth_sensor = device.first<rs2::depth_sensor>();
    const float depth_scale = depth_sensor.get_depth_scale();
    for (auto sensor : device.query_sensors()) {
      set_supported_option(sensor, RS2_OPTION_ENABLE_AUTO_EXPOSURE, 0.0F);
      if (sensor.is<rs2::depth_sensor>()) {
        set_supported_option(sensor, RS2_OPTION_EXPOSURE, 4000.0F);
        set_supported_option(sensor, RS2_OPTION_GAIN, 16.0F);
      } else {
        // Leave timing margin inside the 33 ms frame. Running at the absolute
        // 32 ms exposure limit made this D415 intermittently stop delivering.
        set_supported_option(sensor, RS2_OPTION_EXPOSURE, 280.0F);
        set_supported_option(sensor, RS2_OPTION_GAIN, 128.0F);
      }
    }
    rs2::align align_to_color(RS2_STREAM_COLOR);

    std::cerr << "D415 native capture started at 640x480x30 with manual exposure: "
              << device.get_info(RS2_CAMERA_INFO_NAME) << " serial "
              << device.get_info(RS2_CAMERA_INFO_SERIAL_NUMBER) << '\n';

    const int server_fd = make_server(port);
    listening_socket = server_fd;
    std::uint32_t sequence = 0;
    while (running) {
      std::cerr << "Waiting for Docker RGB-D receiver on TCP " << port << "...\n";
      const int client_fd = accept(server_fd, nullptr, nullptr);
      if (client_fd < 0) {
        if (!running) {
          break;
        }
        continue;
      }
      std::cerr << "Docker receiver connected.\n";

      while (running) {
        rs2::frameset captured;
        try {
          captured = pipeline.wait_for_frames(5000);
        } catch (const rs2::error & error) {
          // A transient USB timeout should not tear down ROS, Docker, and the
          // entire UI. Keep the pipeline alive so delivery can recover.
          std::cerr << "Camera frame timeout; retrying: " << error.what() << '\n';
          continue;
        }
        auto frames = align_to_color.process(captured);
        const auto color = frames.get_color_frame();
        const auto depth = frames.get_depth_frame();
        if (!color || !depth) {
          continue;
        }

        const auto video_profile = color.get_profile().as<rs2::video_stream_profile>();
        const auto intrinsics = video_profile.get_intrinsics();
        RgbdFrameHeader header{};
        header.magic = realsense_mapper::kRgbdMagic;
        header.version = realsense_mapper::kRgbdVersion;
        header.width = static_cast<std::uint32_t>(color.get_width());
        header.height = static_cast<std::uint32_t>(color.get_height());
        header.rgb_bytes = header.width * header.height * 3U;
        header.depth_bytes = header.width * header.height * 2U;
        header.sequence = sequence++;
        header.timestamp_ns = static_cast<std::uint64_t>(
          std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
        header.fx = intrinsics.fx;
        header.fy = intrinsics.fy;
        header.ppx = intrinsics.ppx;
        header.ppy = intrinsics.ppy;
        header.depth_scale = depth_scale;
        std::memcpy(header.distortion, intrinsics.coeffs, sizeof(header.distortion));

        if (!send_all(client_fd, &header, sizeof(header)) ||
          !send_all(client_fd, color.get_data(), header.rgb_bytes) ||
          !send_all(client_fd, depth.get_data(), header.depth_bytes))
        {
          std::cerr << "Docker receiver disconnected.\n";
          break;
        }
      }
      close(client_fd);
    }
    close(server_fd);
    listening_socket = -1;
    pipeline.stop();
  } catch (const rs2::error & error) {
    std::cerr << "Librealsense error: " << error.what() << '\n';
    return 2;
  } catch (const std::exception & error) {
    std::cerr << "Streamer error: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
