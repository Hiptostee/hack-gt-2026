#include <memory>
#include "nav_msgs/msg/odometry.hpp"
#include "rclcpp/rclcpp.hpp"

// DEPRECATED: retained so existing CMake targets still build.
// Do not put this node in the mapping path. The old implementation low-pass
// filtered poses and stamped them with now(), breaking image/pose timing.
class StableOdometry final : public rclcpp::Node
{
public:
  StableOdometry() : Node("stable_odometry_deprecated")
  {
    RCLCPP_WARN(get_logger(),
      "stable_odometry_node is deprecated; mapping.launch.py no longer uses it");
  }
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<StableOdometry>());
  rclcpp::shutdown();
  return 0;
}
