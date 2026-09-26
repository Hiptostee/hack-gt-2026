#include <algorithm>
#include <chrono>
#include <cmath>
#include <memory>
#include <optional>

#include "geometry_msgs/msg/transform_stamped.hpp"
#include "nav_msgs/msg/odometry.hpp"
#include "rclcpp/rclcpp.hpp"
#include "tf2/LinearMath/Quaternion.h"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"
#include "tf2_ros/transform_broadcaster.h"

class StableOdometry final : public rclcpp::Node
{
public:
  StableOdometry()
  : Node("stable_odometry"), broadcaster_(this)
  {
    publisher_ = create_publisher<nav_msgs::msg::Odometry>(
      "/odometry/filtered", rclcpp::SensorDataQoS());
    subscriber_ = create_subscription<nav_msgs::msg::Odometry>(
      "/visual_odom_valid", rclcpp::SensorDataQoS(),
      [this](nav_msgs::msg::Odometry::ConstSharedPtr message) {
        target_ = *message;
        if (!current_) {
          current_ = *message;
        }
      });
    timer_ = create_wall_timer(
      std::chrono::milliseconds(16), std::bind(&StableOdometry::tick, this));
  }

private:
  void tick()
  {
    if (!target_ || !current_) {
      return;
    }
    const auto stamp = now();
    double dt = 0.016;
    if (last_tick_) {
      dt = std::clamp((stamp - *last_tick_).seconds(), 0.001, 0.1);
    }
    last_tick_ = stamp;
    const double alpha = 1.0 - std::exp(-dt / 0.25);

    auto & current_position = current_->pose.pose.position;
    const auto & target_position = target_->pose.pose.position;
    const double dx = target_position.x - current_position.x;
    const double dy = target_position.y - current_position.y;
    const double dz = target_position.z - current_position.z;
    const double distance = std::sqrt(dx * dx + dy * dy + dz * dz);
    const double position_fraction = distance > 0.0 ?
      std::min(alpha, 1.5 * dt / distance) : 0.0;
    current_position.x += position_fraction * dx;
    current_position.y += position_fraction * dy;
    current_position.z += position_fraction * dz;

    tf2::Quaternion current_rotation;
    tf2::Quaternion target_rotation;
    tf2::fromMsg(current_->pose.pose.orientation, current_rotation);
    tf2::fromMsg(target_->pose.pose.orientation, target_rotation);
    current_rotation.normalize();
    target_rotation.normalize();
    if (current_rotation.dot(target_rotation) < 0.0) {
      target_rotation = tf2::Quaternion(
        -target_rotation.x(), -target_rotation.y(),
        -target_rotation.z(), -target_rotation.w());
    }
    const double angle = current_rotation.angleShortestPath(target_rotation);
    const double rotation_fraction = angle > 0.0 ?
      std::min(alpha, 1.5 * dt / angle) : 0.0;
    auto limited_rotation = current_rotation.slerp(target_rotation, rotation_fraction);
    limited_rotation.normalize();
    current_->pose.pose.orientation = tf2::toMsg(limited_rotation);

    nav_msgs::msg::Odometry output = *current_;
    output.header.stamp = stamp;
    output.header.frame_id = "odom";
    output.child_frame_id = "camera_link";
    output.pose.covariance = target_->pose.covariance;
    output.twist.twist = geometry_msgs::msg::Twist();
    for (int axis = 0; axis < 6; ++axis) {
      // RTAB-Map uses odometry covariance during graph optimization. An
      // "unestimated" 1e6 variance makes its loop-closure constraints unsafe.
      output.twist.covariance[6 * axis + axis] = axis < 3 ? 0.25 : 0.09;
    }
    publisher_->publish(output);

    geometry_msgs::msg::TransformStamped transform;
    transform.header = output.header;
    transform.child_frame_id = output.child_frame_id;
    transform.transform.translation.x = current_position.x;
    transform.transform.translation.y = current_position.y;
    transform.transform.translation.z = current_position.z;
    transform.transform.rotation = current_->pose.pose.orientation;
    broadcaster_.sendTransform(transform);
  }

  tf2_ros::TransformBroadcaster broadcaster_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr publisher_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr subscriber_;
  rclcpp::TimerBase::SharedPtr timer_;
  std::optional<nav_msgs::msg::Odometry> target_;
  std::optional<nav_msgs::msg::Odometry> current_;
  std::optional<rclcpp::Time> last_tick_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<StableOdometry>());
  rclcpp::shutdown();
  return 0;
}
