#include <algorithm>
#include <cmath>
#include <memory>

#include "nav_msgs/msg/odometry.hpp"
#include "rclcpp/rclcpp.hpp"
#include "tf2/LinearMath/Quaternion.h"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"

class ValidVisualOdom final : public rclcpp::Node
{
public:
  ValidVisualOdom()
  : Node("valid_visual_odom")
  {
    publisher_ = create_publisher<nav_msgs::msg::Odometry>(
      "/visual_odom_valid", rclcpp::SensorDataQoS());
    subscriber_ = create_subscription<nav_msgs::msg::Odometry>(
      "/visual_odom", rclcpp::SensorDataQoS(),
      [this](nav_msgs::msg::Odometry::ConstSharedPtr input) {
        auto output = *input;
        const auto & p = output.pose.pose.position;
        const auto & q = output.pose.pose.orientation;
        const double norm2 = q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w;
        if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z) ||
          !std::isfinite(norm2) || norm2 < 0.5 || norm2 > 1.5 ||
          !std::isfinite(output.pose.covariance[0]) ||
          output.pose.covariance[0] >= 1000.0)
        {
          RCLCPP_WARN_THROTTLE(
            get_logger(), *get_clock(), 5000,
            "RGB-D tracking lost; withholding invalid visual odometry");
          return;
        }

        if (last_output_) {
          const double dt =
            (rclcpp::Time(output.header.stamp) -
            rclcpp::Time(last_output_->header.stamp)).seconds();
          if (dt <= 0.0) {
            return;
          }
          const auto & previous = last_output_->pose.pose.position;
          const double dx = p.x - previous.x;
          const double dy = p.y - previous.y;
          const double dz = p.z - previous.z;
          const double distance = std::sqrt(dx * dx + dy * dy + dz * dz);
          const double max_step = std::min(0.25, 1.6 * dt);
          if (distance > max_step) {
            const double fraction = max_step / distance;
            output.pose.pose.position.x = previous.x + fraction * dx;
            output.pose.pose.position.y = previous.y + fraction * dy;
            output.pose.pose.position.z = previous.z + fraction * dz;
            output.twist.twist.linear.x = 0.0;
            output.twist.twist.linear.y = 0.0;
            output.twist.twist.linear.z = 0.0;
            for (int axis = 0; axis < 3; ++axis) {
              const int diagonal = 6 * axis + axis;
              output.pose.covariance[diagonal] =
                std::max(output.pose.covariance[diagonal], 0.04);
            }
            RCLCPP_WARN_THROTTLE(
              get_logger(), *get_clock(), 5000,
              "Limiting implausible RGB-D position change (%.2f m in %.2f s)",
              distance, dt);
          }

          tf2::Quaternion previous_rotation;
          tf2::Quaternion measured_rotation;
          tf2::fromMsg(last_output_->pose.pose.orientation, previous_rotation);
          tf2::fromMsg(output.pose.pose.orientation, measured_rotation);
          previous_rotation.normalize();
          measured_rotation.normalize();
          if (previous_rotation.dot(measured_rotation) < 0.0) {
            measured_rotation = tf2::Quaternion(
              -measured_rotation.x(), -measured_rotation.y(),
              -measured_rotation.z(), -measured_rotation.w());
          }
          const double angle = previous_rotation.angleShortestPath(measured_rotation);
          const double max_angle = std::min(0.35, 2.0 * dt);
          if (angle > max_angle) {
            auto limited = previous_rotation.slerp(measured_rotation, max_angle / angle);
            limited.normalize();
            output.pose.pose.orientation = tf2::toMsg(limited);
            for (int axis = 3; axis < 6; ++axis) {
              const int diagonal = 6 * axis + axis;
              output.pose.covariance[diagonal] =
                std::max(output.pose.covariance[diagonal], 0.04);
            }
          }
        }

        // RGB-D reports very small covariance even when the pose visibly
        // jitters. Keep the EKF from snapping to each noisy sample.
        for (int axis = 0; axis < 3; ++axis) {
          const int diagonal = 6 * axis + axis;
          output.pose.covariance[diagonal] =
            std::max(output.pose.covariance[diagonal], 0.01);
          output.twist.covariance[diagonal] =
            std::max(output.twist.covariance[diagonal], 0.04);
        }
        for (int axis = 3; axis < 6; ++axis) {
          const int diagonal = 6 * axis + axis;
          output.pose.covariance[diagonal] =
            std::max(output.pose.covariance[diagonal], 0.0025);
          output.twist.covariance[diagonal] =
            std::max(output.twist.covariance[diagonal], 0.01);
        }
        publisher_->publish(output);
        last_output_ = std::make_shared<nav_msgs::msg::Odometry>(output);
      });
  }

private:
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr publisher_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr subscriber_;
  nav_msgs::msg::Odometry::SharedPtr last_output_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<ValidVisualOdom>());
  rclcpp::shutdown();
  return 0;
}
