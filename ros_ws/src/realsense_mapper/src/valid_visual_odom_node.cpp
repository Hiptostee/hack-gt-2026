#include <cmath>
#include <memory>

#include "geometry_msgs/msg/transform_stamped.hpp"
#include "nav_msgs/msg/odometry.hpp"
#include "rclcpp/rclcpp.hpp"
#include "tf2_ros/transform_broadcaster.h"

// Validate RGB-D odometry without changing its geometry or timestamp.
// Bad samples are dropped; good samples are forwarded exactly as measured.
class ValidVisualOdom final : public rclcpp::Node
{
public:
  ValidVisualOdom()
  : Node("valid_visual_odom"), broadcaster_(this)
  {
    publisher_ = create_publisher<nav_msgs::msg::Odometry>(
      "/odometry/filtered", rclcpp::SensorDataQoS());
    subscriber_ = create_subscription<nav_msgs::msg::Odometry>(
      "/visual_odom", rclcpp::SensorDataQoS(),
      std::bind(&ValidVisualOdom::callback, this, std::placeholders::_1));
  }

private:
  void callback(const nav_msgs::msg::Odometry::ConstSharedPtr input)
  {
    const auto & p = input->pose.pose.position;
    const auto & q = input->pose.pose.orientation;
    const double q_norm2 = q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w;

    if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z) ||
        !std::isfinite(q_norm2) || q_norm2 < 0.5 || q_norm2 > 1.5 ||
        !std::isfinite(input->pose.covariance[0]) ||
        input->pose.covariance[0] >= 1000.0) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 3000,
        "RGB-D tracking invalid; dropping measurement instead of modifying it");
      return;
    }

    nav_msgs::msg::Odometry output = *input;
    output.header.frame_id = "odom";
    output.child_frame_id = "camera_link";

    // Critical: preserve input->header.stamp. The map must use the pose that
    // belongs to the RGB-D frame, not a smoothed pose stamped with now().
    publisher_->publish(output);

    geometry_msgs::msg::TransformStamped tf;
    tf.header = output.header;
    tf.child_frame_id = output.child_frame_id;
    tf.transform.translation.x = p.x;
    tf.transform.translation.y = p.y;
    tf.transform.translation.z = p.z;
    tf.transform.rotation = q;
    broadcaster_.sendTransform(tf);
  }

  tf2_ros::TransformBroadcaster broadcaster_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr publisher_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr subscriber_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<ValidVisualOdom>());
  rclcpp::shutdown();
  return 0;
}
