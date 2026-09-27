#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <string>

#include "geometry_msgs/msg/transform_stamped.hpp"
#include "nav_msgs/msg/path.hpp"
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/bool.hpp"
#include "std_msgs/msg/u_int8.hpp"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"
#include "tf2_ros/buffer.h"
#include "tf2_ros/transform_listener.h"
#include "tf2/utils.h"

using std::placeholders::_1;

class BackpackDirection final : public rclcpp::Node
{
public:
  BackpackDirection()
  : Node("backpack_direction"), tf_buffer_(get_clock()), tf_listener_(tf_buffer_)
  {
    lookahead_m_ = declare_parameter<double>("lookahead_m", 0.55);
    arrival_m_ = declare_parameter<double>("arrival_m", 0.10);
    lateral_start_m_ = declare_parameter<double>("lateral_start_m", 0.18);
    lateral_stop_m_ = declare_parameter<double>("lateral_stop_m", 0.10);
    path_timeout_s_ = declare_parameter<double>("path_timeout_s", 1.5);
    validity_timeout_s_ = declare_parameter<double>("validity_timeout_s", 1.5);

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
        }
      });
    timer_ = create_wall_timer(std::chrono::milliseconds(200),
      std::bind(&BackpackDirection::publish_direction, this));
    RCLCPP_INFO(get_logger(),
      "Backpack direction ready: 0=forward, 1=move right, 2=move left, 3=stop");
  }

private:
  static constexpr std::uint8_t kForward = 0;
  static constexpr std::uint8_t kRight = 1;
  static constexpr std::uint8_t kLeft = 2;
  static constexpr std::uint8_t kStop = 3;

  void emit_direction(std::uint8_t direction, const char * stop_reason = "")
  {
    if (!have_direction_ || direction != last_direction_ ||
      (direction == kStop && stop_reason_ != stop_reason))
    {
      const char * names[] = {"forward", "move right", "move left", "stop"};
      if (direction == kStop) {
        RCLCPP_INFO(get_logger(), "Direction 3 (stop: %s)", stop_reason);
      } else {
        RCLCPP_INFO(get_logger(), "Direction %u (%s)", direction, names[direction]);
      }
    }
    last_direction_ = direction;
    stop_reason_ = stop_reason;
    have_direction_ = true;
    std_msgs::msg::UInt8 command;
    command.data = direction;
    command_pub_->publish(command);
  }

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
    if (!active_ ||
      std::chrono::duration<double>(now - last_active_).count() > validity_timeout_s_)
    {
      emit_direction(kStop, "guidance inactive");
      return;
    }
    if (!valid_ ||
      std::chrono::duration<double>(now - last_validity_).count() > validity_timeout_s_)
    {
      emit_direction(kStop, "path invalid or stale");
      return;
    }
    if (!path_ || std::chrono::duration<double>(now - last_path_).count() > path_timeout_s_) {
      emit_direction(kStop, "path missing or stale");
      return;
    }

    geometry_msgs::msg::TransformStamped camera_tf;
    try {
      camera_tf = tf_buffer_.lookupTransform(
        path_->header.frame_id, "camera_link", tf2::TimePointZero);
    } catch (const tf2::TransformException &) {
      emit_direction(kStop, "camera transform unavailable");
      return;
    }

    const double x = camera_tf.transform.translation.x;
    const double y = camera_tf.transform.translation.y;
    const double theta = tf2::getYaw(camera_tf.transform.rotation);
    const auto & poses = path_->poses;
    const auto & final = poses.back().pose.position;
    if (std::hypot(final.x - x, final.y - y) <= arrival_m_) {
      emit_direction(kStop, "route endpoint reached");
      return;
    }

    // Start looking ahead from the closest point on the route to the current
    // camera X/Y, instead of the pose captured when A* last ran.
    double closest_distance_sq = std::numeric_limits<double>::infinity();
    std::size_t closest_segment = 0;
    double goal_x = 0.0;
    double goal_y = 0.0;
    for (std::size_t index = 1; index < poses.size(); ++index) {
      const auto & from = poses[index - 1].pose.position;
      const auto & to = poses[index].pose.position;
      const double dx = to.x - from.x;
      const double dy = to.y - from.y;
      const double length_sq = dx * dx + dy * dy;
      if (length_sq < 1e-12) {continue;}
      const double fraction = std::clamp(
        ((x - from.x) * dx + (y - from.y) * dy) / length_sq, 0.0, 1.0);
      const double projected_x = from.x + fraction * dx;
      const double projected_y = from.y + fraction * dy;
      const double distance_sq =
        (projected_x - x) * (projected_x - x) + (projected_y - y) * (projected_y - y);
      if (distance_sq < closest_distance_sq) {
        closest_distance_sq = distance_sq;
        closest_segment = index;
        goal_x = projected_x;
        goal_y = projected_y;
      }
    }
    if (closest_segment == 0) {
      emit_direction(kStop, "route has no usable segment");
      return;
    }

    double remaining = lookahead_m_;
    for (std::size_t index = closest_segment; index < poses.size(); ++index) {
      const auto & to = poses[index].pose.position;
      const double dx = to.x - goal_x;
      const double dy = to.y - goal_y;
      const double length = std::hypot(dx, dy);
      if (length < 1e-6) {continue;}
      if (remaining <= length) {
        const double fraction = remaining / length;
        goal_x += fraction * dx;
        goal_y += fraction * dy;
        break;
      }
      remaining -= length;
      goal_x = to.x;
      goal_y = to.y;
    }

    const double dx = goal_x - x;
    const double dy = goal_y - y;
    if (std::hypot(dx, dy) < 0.03) {
      emit_direction(kStop, "route point too close");
      return;
    }
    // Translation error in the camera's 2D frame: positive lateral is left.
    const double lateral_error = -std::sin(theta) * dx + std::cos(theta) * dy;
    std::uint8_t direction = kForward;
    if (lateral_error > lateral_start_m_ ||
      (last_direction_ == kLeft && lateral_error > lateral_stop_m_))
    {
      direction = kLeft;
    } else if (lateral_error < -lateral_start_m_ ||
      (last_direction_ == kRight && lateral_error < -lateral_stop_m_))
    {
      direction = kRight;
    }
    emit_direction(direction);
  }

  double lookahead_m_{0.55};
  double arrival_m_{0.10};
  double lateral_start_m_{0.18};
  double lateral_stop_m_{0.10};
  double path_timeout_s_{1.5};
  double validity_timeout_s_{0.5};
  std::uint8_t last_direction_{kStop};
  std::string stop_reason_;
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
  tf2_ros::Buffer tf_buffer_;
  tf2_ros::TransformListener tf_listener_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<BackpackDirection>());
  rclcpp::shutdown();
  return 0;
}
