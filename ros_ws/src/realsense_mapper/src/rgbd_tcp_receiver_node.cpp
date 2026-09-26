#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/distortion_models.hpp"
#include "sensor_msgs/image_encodings.hpp"
#include "sensor_msgs/msg/camera_info.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "realsense_mapper/rgbd_protocol.hpp"

using namespace std::chrono_literals;

class RgbdTcpReceiver final : public rclcpp::Node
{
public:
  RgbdTcpReceiver()
  : Node("rgbd_tcp_receiver"), running_(true), socket_fd_(-1)
  {
    host_ = declare_parameter<std::string>("host", "host.docker.internal");
    port_ = declare_parameter<int>("port", 50051);
    frame_id_ = declare_parameter<std::string>("frame_id", "camera_link");
    const auto qos = rclcpp::SensorDataQoS();
    color_pub_ = create_publisher<sensor_msgs::msg::Image>("/camera/color/image_raw", qos);
    depth_pub_ = create_publisher<sensor_msgs::msg::Image>(
      "/camera/aligned_depth_to_color/image_raw", qos);
    info_pub_ = create_publisher<sensor_msgs::msg::CameraInfo>(
      "/camera/color/camera_info", qos);
    worker_ = std::thread(&RgbdTcpReceiver::run, this);
  }

  ~RgbdTcpReceiver() override
  {
    running_ = false;
    const int fd = socket_fd_.exchange(-1);
    if (fd >= 0) {
      shutdown(fd, SHUT_RDWR);
      close(fd);
    }
    if (worker_.joinable()) {
      worker_.join();
    }
  }

private:
  static bool receive_all(int fd, void * data, std::size_t bytes)
  {
    auto * cursor = static_cast<std::uint8_t *>(data);
    while (bytes > 0) {
      const auto received = recv(fd, cursor, bytes, 0);
      if (received <= 0) {
        return false;
      }
      cursor += received;
      bytes -= static_cast<std::size_t>(received);
    }
    return true;
  }

  int connect_to_host() const
  {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo * addresses = nullptr;
    const auto service = std::to_string(port_);
    if (getaddrinfo(host_.c_str(), service.c_str(), &hints, &addresses) != 0) {
      return -1;
    }
    int fd = -1;
    for (auto * address = addresses; address != nullptr; address = address->ai_next) {
      fd = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
      if (fd >= 0 && connect(fd, address->ai_addr, address->ai_addrlen) == 0) {
        break;
      }
      if (fd >= 0) {
        close(fd);
      }
      fd = -1;
    }
    freeaddrinfo(addresses);
    return fd;
  }

  void run()
  {
    while (running_ && rclcpp::ok()) {
      const int fd = connect_to_host();
      if (fd < 0) {
        RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 5000,
          "Waiting for native D415 streamer at %s:%d", host_.c_str(), port_);
        std::this_thread::sleep_for(1s);
        continue;
      }
      socket_fd_ = fd;
      RCLCPP_INFO(get_logger(), "Connected to native D415 streamer at %s:%d",
        host_.c_str(), port_);
      receive_frames(fd);
      if (socket_fd_.exchange(-1) == fd) {
        close(fd);
      }
      if (running_) {
        RCLCPP_WARN(get_logger(), "D415 stream disconnected; reconnecting");
      }
    }
  }

  void receive_frames(int fd)
  {
    realsense_mapper::RgbdFrameHeader header{};
    while (running_ && rclcpp::ok() && receive_all(fd, &header, sizeof(header))) {
      if (header.magic != realsense_mapper::kRgbdMagic ||
        header.version != realsense_mapper::kRgbdVersion ||
        header.width == 0 || header.height == 0 ||
        header.rgb_bytes != header.width * header.height * 3U ||
        header.depth_bytes != header.width * header.height * 2U)
      {
        RCLCPP_ERROR(get_logger(), "Invalid RGB-D frame header");
        return;
      }

      std::vector<std::uint8_t> rgb(header.rgb_bytes);
      std::vector<std::uint8_t> depth(header.depth_bytes);
      if (!receive_all(fd, rgb.data(), rgb.size()) ||
        !receive_all(fd, depth.data(), depth.size()))
      {
        return;
      }
      publish(header, std::move(rgb), std::move(depth));
    }
  }

  void publish(
    const realsense_mapper::RgbdFrameHeader & header,
    std::vector<std::uint8_t> rgb, std::vector<std::uint8_t> depth)
  {
    builtin_interfaces::msg::Time stamp;
    stamp.sec = static_cast<std::int32_t>(header.timestamp_ns / 1000000000ULL);
    stamp.nanosec = static_cast<std::uint32_t>(header.timestamp_ns % 1000000000ULL);

    sensor_msgs::msg::Image color_message;
    color_message.header.stamp = stamp;
    color_message.header.frame_id = frame_id_;
    color_message.height = header.height;
    color_message.width = header.width;
    color_message.encoding = sensor_msgs::image_encodings::RGB8;
    color_message.is_bigendian = false;
    color_message.step = header.width * 3U;
    color_message.data = std::move(rgb);

    sensor_msgs::msg::Image depth_message;
    depth_message.header = color_message.header;
    depth_message.height = header.height;
    depth_message.width = header.width;
    depth_message.encoding = sensor_msgs::image_encodings::TYPE_16UC1;
    depth_message.is_bigendian = false;
    depth_message.step = header.width * 2U;
    depth_message.data = std::move(depth);

    sensor_msgs::msg::CameraInfo camera_info;
    camera_info.header = color_message.header;
    camera_info.height = header.height;
    camera_info.width = header.width;
    camera_info.distortion_model = sensor_msgs::distortion_models::PLUMB_BOB;
    camera_info.d.assign(header.distortion, header.distortion + 5);
    camera_info.k = {
      header.fx, 0.0, header.ppx,
      0.0, header.fy, header.ppy,
      0.0, 0.0, 1.0};
    camera_info.r = {1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0};
    camera_info.p = {
      header.fx, 0.0, header.ppx, 0.0,
      0.0, header.fy, header.ppy, 0.0,
      0.0, 0.0, 1.0, 0.0};

    color_pub_->publish(std::move(color_message));
    depth_pub_->publish(std::move(depth_message));
    info_pub_->publish(std::move(camera_info));
  }

  std::string host_;
  std::string frame_id_;
  int port_;
  std::atomic<bool> running_;
  std::atomic<int> socket_fd_;
  std::thread worker_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr color_pub_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr depth_pub_;
  rclcpp::Publisher<sensor_msgs::msg::CameraInfo>::SharedPtr info_pub_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<RgbdTcpReceiver>());
  rclcpp::shutdown();
  return 0;
}
