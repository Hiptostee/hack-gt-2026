#include <chrono>
#include <cmath>
#include <memory>
#include <string>

#include "geometry_msgs/msg/transform_stamped.hpp"
#include "nav_msgs/msg/odometry.hpp"
#include "geometry_msgs/msg/pose_with_covariance_stamped.hpp"
#include "rclcpp/rclcpp.hpp"
#include "tf2/LinearMath/Quaternion.h"
#include "tf2/LinearMath/Transform.h"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"
#include "tf2_ros/buffer.h"
#include "tf2_ros/transform_broadcaster.h"
#include "tf2_ros/transform_listener.h"

using std::placeholders::_1;

class OpenVinsOdomBridge final : public rclcpp::Node
{
public:
  OpenVinsOdomBridge()
  : Node("openvins_odom_bridge"), tf_buffer_(get_clock()), tf_listener_(tf_buffer_),
    tf_broadcaster_(this)
  {
    max_visual_age_ = declare_parameter<double>("max_visual_age", 0.5);
    odom_pub_ = create_publisher<nav_msgs::msg::Odometry>(
      "/odometry/filtered", rclcpp::SensorDataQoS());
    visual_sub_ = create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
      "/ov_msckf/poseimu", rclcpp::QoS(10),
      [this](geometry_msgs::msg::PoseWithCovarianceStamped::ConstSharedPtr message) {
        last_visual_stamp_ = rclcpp::Time(message->header.stamp);
        have_visual_update_ = true;
      });
    odom_sub_ = create_subscription<nav_msgs::msg::Odometry>(
      "/ov_msckf/odomimu", rclcpp::QoS(10),
      std::bind(&OpenVinsOdomBridge::on_odometry, this, _1));
  }

private:
  void on_odometry(nav_msgs::msg::Odometry::ConstSharedPtr input)
  {
    const rclcpp::Time stamp(input->header.stamp);
    if (!have_visual_update_ || stamp < last_visual_stamp_ ||
      (stamp - last_visual_stamp_).seconds() > max_visual_age_) {
      return;  // Never let IMU-only propagation masquerade as tracked VIO.
    }
    if (input->header.frame_id != "global" || input->child_frame_id != "imu") {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000, "Unexpected OpenVINS frames: %s -> %s",
        input->header.frame_id.c_str(), input->child_frame_id.c_str());
      return;
    }
    const auto & q = input->pose.pose.orientation;
    if (!std::isfinite(q.x) || !std::isfinite(q.y) || !std::isfinite(q.z) ||
      !std::isfinite(q.w) || q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w < 0.5) {
      return;
    }

    geometry_msgs::msg::TransformStamped camera_to_imu;
    try {
      // Static mounting transform, published by mapping.launch.py.
      camera_to_imu = tf_buffer_.lookupTransform("camera_link", "imu_link", tf2::TimePointZero);
    } catch (const tf2::TransformException & ex) {
      RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 5000, "Waiting for camera/IMU TF: %s", ex.what());
      return;
    }

    tf2::Transform t_camera_imu;
    tf2::fromMsg(camera_to_imu.transform, t_camera_imu);
    tf2::Quaternion q_global_imu(q.x, q.y, q.z, q.w);
    q_global_imu.normalize();
    const auto & p = input->pose.pose.position;
    tf2::Transform t_global_imu(q_global_imu, tf2::Vector3(p.x, p.y, p.z));
    const tf2::Transform t_global_camera = t_global_imu * t_camera_imu.inverse();

    nav_msgs::msg::Odometry output;
    output.header.stamp = input->header.stamp;
    output.header.frame_id = "odom";  // OpenVINS's local global frame is ROS odom.
    output.child_frame_id = "camera_link";
    output.pose.pose.position.x = t_global_camera.getOrigin().x();
    output.pose.pose.position.y = t_global_camera.getOrigin().y();
    output.pose.pose.position.z = t_global_camera.getOrigin().z();
    output.pose.pose.orientation = tf2::toMsg(t_global_camera.getRotation());
    output.pose.covariance = input->pose.covariance;
    // A 4 cm lever arm couples orientation uncertainty into camera position.
    const double arm2 = t_camera_imu.getOrigin().length2();
    for (int axis = 0; axis < 3; ++axis) {
      output.pose.covariance[6 * axis + axis] +=
        arm2 * input->pose.covariance[6 * (axis + 3) + (axis + 3)];
    }
    // RTAB-Map consumes pose only. OpenVINS labels angular rate as unestimated;
    // do not publish a misleading twist in the camera frame.
    output.twist.covariance[0] = 1e6;
    output.twist.covariance[7] = 1e6;
    output.twist.covariance[14] = 1e6;
    output.twist.covariance[21] = 1e6;
    output.twist.covariance[28] = 1e6;
    output.twist.covariance[35] = 1e6;
    odom_pub_->publish(output);

    geometry_msgs::msg::TransformStamped transform;
    transform.header = output.header;
    transform.child_frame_id = output.child_frame_id;
    transform.transform.translation.x = output.pose.pose.position.x;
    transform.transform.translation.y = output.pose.pose.position.y;
    transform.transform.translation.z = output.pose.pose.position.z;
    transform.transform.rotation = output.pose.pose.orientation;
    tf_broadcaster_.sendTransform(transform);
  }

  double max_visual_age_{0.5};
  bool have_visual_update_{false};
  rclcpp::Time last_visual_stamp_{0, 0, RCL_ROS_TIME};
  tf2_ros::Buffer tf_buffer_;
  tf2_ros::TransformListener tf_listener_;
  tf2_ros::TransformBroadcaster tf_broadcaster_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr visual_sub_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_sub_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<OpenVinsOdomBridge>());
  rclcpp::shutdown();
  return 0;
}
