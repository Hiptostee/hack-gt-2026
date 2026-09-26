#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <memory>

#include "nav_msgs/msg/path.hpp"
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/bool.hpp"
#include "std_msgs/msg/u_int8.hpp"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"
#include "tf2/utils.h"

using std::placeholders::_1;

class BackpackDirection final : public rclcpp::Node
{
public:
  BackpackDirection()
  : Node("backpack_direction")
  {
    lookahead_m_ = declare_parameter<double>("lookahead_m", 0.55);
    arrival_m_ = declare_parameter<double>("arrival_m", 0.35);
    turn_start_rad_ = declare_parameter<double>("turn_start_rad", 0.32);
    turn_stop_rad_ = declare_parameter<double>("turn_stop_rad", 0.18);
    rotate_start_rad_ = declare_parameter<double>("rotate_start_rad", 1.05);
    rotate_stop_rad_ = declare_parameter<double>("rotate_stop_rad", 0.85);
    path_timeout_s_ = declare_parameter<double>("path_timeout_s", 1.5);
    validity_timeout_s_ = declare_parameter<double>("validity_timeout_s", 0.5);

    command_pub_ = create_publisher<std_msgs::msg::UInt8>("/backpack/direction", 10);
    active_sub_ = create_subscription<std_msgs::msg::Bool>(
      "/backpack/guidance_active", 10,
      [this](std_msgs::msg::Bool::ConstSharedPtr message) {
        active_ = message->data;
        last_active_ = std::chrono::steady_clock::now();
      });
    path_sub_ = create_subscription<nav_msgs::msg::Path>(
      "/backpack/path", 10, std::bind(&BackpackDirection::path_callback, this, _1));
    valid_sub_ = create_subscription<std_msgs::msg::Bool>(
      "/backpack/path_valid", 10,
      [this](std_msgs::msg::Bool::ConstSharedPtr message) {
        valid_ = message->data;
        last_validity_ = std::chrono::steady_clock::now();
        if (!valid_) {
          path_.reset();
          last_direction_ = kForward;
          have_direction_ = false;
        }
      });
    timer_ = create_wall_timer(std::chrono::milliseconds(200),
      std::bind(&BackpackDirection::publish_direction, this));
    RCLCPP_INFO(get_logger(),
      "Backpack direction ready: 0=forward, 1=left, 2=right, 3=rotate left, 4=rotate right");
  }

private:
  static constexpr std::uint8_t kForward = 0;
  static constexpr std::uint8_t kLeft = 1;
  static constexpr std::uint8_t kRight = 2;
  static constexpr std::uint8_t kRotateLeft = 3;
  static constexpr std::uint8_t kRotateRight = 4;

  void path_callback(nav_msgs::msg::Path::ConstSharedPtr message)
  {
    if (message->poses.size() < 2) {
      path_.reset();
      return;
    }
    path_ = message;
    last_path_ = std::chrono::steady_clock::now();
  }

  void publish_direction()
  {
    const auto now = std::chrono::steady_clock::now();
    if (!active_ || !valid_ || !path_ ||
      std::chrono::duration<double>(now - last_active_).count() > validity_timeout_s_ ||
      std::chrono::duration<double>(now - last_validity_).count() > validity_timeout_s_ ||
      std::chrono::duration<double>(now - last_path_).count() > path_timeout_s_)
    {
      return;
    }

    const auto & poses = path_->poses;
    const auto & start = poses.front().pose.position;
    double remaining = lookahead_m_;
    double goal_x = start.x;
    double goal_y = start.y;
    double route_length = 0.0;
    for (std::size_t index = 1; index < poses.size(); ++index) {
      const auto & from = poses[index - 1].pose.position;
      const auto & to = poses[index].pose.position;
      const double dx = to.x - from.x;
      const double dy = to.y - from.y;
      const double length = std::hypot(dx, dy);
      route_length += length;
      if (length < 1e-6) {continue;}
      if (remaining <= length) {
        const double fraction = remaining / length;
        goal_x = from.x + fraction * dx;
        goal_y = from.y + fraction * dy;
        break;
      }
      remaining -= length;
      goal_x = to.x;
      goal_y = to.y;
    }
    if (route_length < arrival_m_ || std::hypot(goal_x - start.x, goal_y - start.y) < 0.05) {
      return;
    }

    const double heading = tf2::getYaw(poses.front().pose.orientation);
    const double desired = std::atan2(goal_y - start.y, goal_x - start.x);
    const double error = std::atan2(std::sin(desired - heading), std::cos(desired - heading));
    std::uint8_t direction = kForward;
    if (error > rotate_start_rad_ ||
      (last_direction_ == kRotateLeft && error > rotate_stop_rad_))
    {
      direction = kRotateLeft;
    } else if (error < -rotate_start_rad_ ||
      (last_direction_ == kRotateRight && error < -rotate_stop_rad_))
    {
      direction = kRotateRight;
    } else if (error > turn_start_rad_ ||
      ((last_direction_ == kLeft || last_direction_ == kRotateLeft) &&
      error > turn_stop_rad_))
    {
      direction = kLeft;
    } else if (error < -turn_start_rad_ ||
      ((last_direction_ == kRight || last_direction_ == kRotateRight) &&
      error < -turn_stop_rad_))
    {
      direction = kRight;
    }
    if (!have_direction_ || direction != last_direction_) {
      const char * names[] = {"forward", "left", "right", "rotate left", "rotate right"};
      RCLCPP_INFO(get_logger(), "Direction %u (%s)", direction, names[direction]);
    }
    last_direction_ = direction;
    have_direction_ = true;
    std_msgs::msg::UInt8 command;
    command.data = direction;
    command_pub_->publish(command);
  }

  double lookahead_m_{0.55};
  double arrival_m_{0.35};
  double turn_start_rad_{0.32};
  double turn_stop_rad_{0.18};
  double rotate_start_rad_{1.05};
  double rotate_stop_rad_{0.85};
  double path_timeout_s_{1.5};
  double validity_timeout_s_{0.5};
  std::uint8_t last_direction_{kForward};
  bool have_direction_{false};
  bool valid_{false};
  bool active_{false};
  std::chrono::steady_clock::time_point last_active_{};
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr active_sub_;
  nav_msgs::msg::Path::ConstSharedPtr path_;
  std::chrono::steady_clock::time_point last_path_{};
  std::chrono::steady_clock::time_point last_validity_{};
  rclcpp::Publisher<std_msgs::msg::UInt8>::SharedPtr command_pub_;
  rclcpp::Subscription<nav_msgs::msg::Path>::SharedPtr path_sub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr valid_sub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<BackpackDirection>());
  rclcpp::shutdown();
  return 0;
}
