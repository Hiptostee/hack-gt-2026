#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>

#include "nav_msgs/msg/occupancy_grid.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/point_cloud2.hpp"

using namespace std::chrono_literals;

class MappingStatusNode final : public rclcpp::Node
{
public:
  MappingStatusNode()
  : Node("mapping_status"), cloud_points_(0), known_cells_(0), map_width_(0), map_height_(0)
  {
    auto map_qos = rclcpp::QoS(1).transient_local().reliable();
    cloud_sub_ = create_subscription<sensor_msgs::msg::PointCloud2>(
      "/rtabmap/cloud_map", rclcpp::QoS(1).best_effort(),
      [this](sensor_msgs::msg::PointCloud2::ConstSharedPtr msg) {
        cloud_points_ = static_cast<std::uint64_t>(msg->width) * msg->height;
      });

    grid_sub_ = create_subscription<nav_msgs::msg::OccupancyGrid>(
      "/rtabmap/map", map_qos,
      [this](nav_msgs::msg::OccupancyGrid::ConstSharedPtr msg) {
        map_width_ = msg->info.width;
        map_height_ = msg->info.height;
        std::uint64_t known = 0;
        for (const auto cell : msg->data) {
          known += cell >= 0 ? 1U : 0U;
        }
        known_cells_ = known;
      });

    timer_ = create_wall_timer(5s, std::bind(&MappingStatusNode::report, this));
    RCLCPP_INFO(get_logger(),
      "Waiting for /rtabmap/cloud_map and /rtabmap/map (open noVNC at localhost:6080)");
  }

private:
  void report() const
  {
    RCLCPP_INFO(get_logger(), "map status: cloud=%lu points, grid=%ux%u, known=%lu cells",
      static_cast<unsigned long>(cloud_points_), map_width_, map_height_,
      static_cast<unsigned long>(known_cells_));
  }

  rclcpp::Subscription<sensor_msgs::msg::PointCloud2>::SharedPtr cloud_sub_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr grid_sub_;
  rclcpp::TimerBase::SharedPtr timer_;
  std::uint64_t cloud_points_;
  std::uint64_t known_cells_;
  std::uint32_t map_width_;
  std::uint32_t map_height_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<MappingStatusNode>());
  rclcpp::shutdown();
  return 0;
}
