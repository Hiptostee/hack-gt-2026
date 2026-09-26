#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <deque>
#include <limits>
#include <memory>
#include <optional>
#include <queue>
#include <regex>
#include <string>
#include <utility>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include "geometry_msgs/msg/point_stamped.hpp"
#include "geometry_msgs/msg/pose_stamped.hpp"
#include "nav_msgs/msg/occupancy_grid.hpp"
#include "nav_msgs/msg/odometry.hpp"
#include "nav_msgs/msg/path.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/image_encodings.hpp"
#include "sensor_msgs/msg/camera_info.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "std_msgs/msg/string.hpp"
#include "std_msgs/msg/bool.hpp"
#include "tf2_geometry_msgs/tf2_geometry_msgs.hpp"
#include "tf2_ros/buffer.h"
#include "tf2_ros/transform_listener.h"

using std::placeholders::_1;

class BackpackPathPlanner final : public rclcpp::Node
{
public:
  BackpackPathPlanner()
  : Node("backpack_path_planner"), tf_buffer_(get_clock()), tf_listener_(tf_buffer_)
  {
    turn_penalty_ = declare_parameter<double>("turn_penalty", 0.35);
    obstacle_threshold_ = declare_parameter<int>("obstacle_threshold", 50);
    inflation_radius_ = declare_parameter<double>("inflation_radius", 0.25);
    standoff_distance_ = declare_parameter<double>("standoff_distance", 0.70);
    max_prediction_age_ = declare_parameter<double>("max_prediction_age", 0.75);
    allow_unknown_ = declare_parameter<bool>("allow_unknown", true);
    preferred_clearance_ = declare_parameter<double>("preferred_clearance", 0.55);
    clearance_weight_ = declare_parameter<double>("clearance_weight", 2.0);
    goal_smoothing_alpha_ = declare_parameter<double>("goal_smoothing_alpha", 0.20);
    start_ignore_radius_ = declare_parameter<double>("start_ignore_radius", 0.20);
    wall_closing_radius_ = declare_parameter<double>("wall_closing_radius", 0.12);

    path_pub_ = create_publisher<nav_msgs::msg::Path>("/backpack/path", 10);
    goal_pub_ = create_publisher<geometry_msgs::msg::PointStamped>("/backpack/goal", 10);
    path_valid_pub_ = create_publisher<std_msgs::msg::Bool>("/backpack/path_valid", 10);
    image_pub_ = create_publisher<sensor_msgs::msg::Image>(
      "/backpack/planner_image", rclcpp::SensorDataQoS());

    map_sub_ = create_subscription<nav_msgs::msg::OccupancyGrid>(
      "/rtabmap/map", rclcpp::QoS(1).transient_local().reliable(),
      std::bind(&BackpackPathPlanner::map_callback, this, _1));
    depth_sub_ = create_subscription<sensor_msgs::msg::Image>(
      "/camera/aligned_depth_to_color/image_raw", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::Image::ConstSharedPtr msg) {
        depth_buffer_.push_back(msg);
        if (depth_buffer_.size() > 150) {
          depth_buffer_.pop_front();
        }
      });
    info_sub_ = create_subscription<sensor_msgs::msg::CameraInfo>(
      "/camera/color/camera_info", rclcpp::SensorDataQoS(),
      [this](sensor_msgs::msg::CameraInfo::ConstSharedPtr msg) {camera_info_ = msg;});
    image_sub_ = create_subscription<sensor_msgs::msg::Image>(
      "/yolo/annotated_image", rclcpp::SensorDataQoS(),
      std::bind(&BackpackPathPlanner::image_callback, this, _1));
    detection_sub_ = create_subscription<std_msgs::msg::String>(
      "/yolo/black_backpack", 10,
      std::bind(&BackpackPathPlanner::detection_callback, this, _1));
    visual_odom_sub_ = create_subscription<nav_msgs::msg::Odometry>(
      "/visual_odom_valid", rclcpp::SensorDataQoS(),
      [this](nav_msgs::msg::Odometry::ConstSharedPtr) {
        last_visual_odom_ = std::chrono::steady_clock::now();
        have_visual_odom_ = true;
      });
    watchdog_timer_ = create_wall_timer(std::chrono::milliseconds(50), [this]() {
      if (!visual_odometry_fresh()) {
        clear_path();
        publish_path_valid(false);
        return;
      }
      // Keep routing from the moving wearer to the last observed backpack,
      // even when the detector no longer has it in the current image.
      if (target_odom_ && map_ &&
        std::chrono::duration<double>(
          std::chrono::steady_clock::now() - last_plan_attempt_).count() >= 0.5)
      {
        const auto target = target_in_current_map();
        if (target) {
          goal_pub_->publish(*target);
          plan_to(*target);
        } else {
          clear_path();
          publish_path_valid(false);
        }
      }
    });

    RCLCPP_INFO(get_logger(), "Backpack A* planner ready (turn penalty %.2f)", turn_penalty_);
  }

private:
  struct Box {int x; int y; int width; int height; double confidence;};
  struct OpenNode {double f; double g; int state;};
  struct Greater {bool operator()(const OpenNode & a, const OpenNode & b) const {return a.f > b.f;}};
  static constexpr int kDirections = 8;
  static constexpr std::array<int, kDirections> kDx{{1, 1, 0, -1, -1, -1, 0, 1}};
  static constexpr std::array<int, kDirections> kDy{{0, 1, 1, 1, 0, -1, -1, -1}};

  bool visual_odometry_fresh() const
  {
    return have_visual_odom_ &&
      std::chrono::duration<double>(
      std::chrono::steady_clock::now() - last_visual_odom_).count() <= max_prediction_age_;
  }

  void publish_path_valid(bool valid)
  {
    std_msgs::msg::Bool message;
    message.data = valid;
    path_valid_pub_->publish(message);
  }

  void clear_path()
  {
    if (current_path_) {
      nav_msgs::msg::Path empty;
      empty.header.frame_id = current_path_->header.frame_id;
      empty.header.stamp = now();
      path_pub_->publish(empty);
      current_path_.reset();
    }
  }

  void map_callback(nav_msgs::msg::OccupancyGrid::ConstSharedPtr message)
  {
    map_ = message;
    cv::Mat occupied(
      static_cast<int>(message->info.height), static_cast<int>(message->info.width),
      CV_8UC1, cv::Scalar(0));
    for (int y = 0; y < occupied.rows; ++y) {
      auto * row = occupied.ptr<std::uint8_t>(y);
      for (int x = 0; x < occupied.cols; ++x) {
        const int value = message->data[y * occupied.cols + x];
        row[x] = value >= obstacle_threshold_ ? 255 : 0;
      }
    }
    // Depth maps often leave one- or two-cell holes in otherwise continuous
    // walls. Morphological closing joins those wall fragments before A* sees
    // them, without turning unknown space itself into an obstacle.
    const int close_cells = std::max(
      1, static_cast<int>(std::ceil(wall_closing_radius_ / message->info.resolution)));
    const auto kernel = cv::getStructuringElement(
      cv::MORPH_ELLIPSE, cv::Size(2 * close_cells + 1, 2 * close_cells + 1));
    cv::morphologyEx(occupied, occupied, cv::MORPH_CLOSE, kernel);
    blocked_cells_.assign(message->data.size(), false);

    cv::Mat free_mask(
      static_cast<int>(message->info.height), static_cast<int>(message->info.width), CV_8UC1);
    for (int y = 0; y < free_mask.rows; ++y) {
      auto * row = free_mask.ptr<std::uint8_t>(y);
      for (int x = 0; x < free_mask.cols; ++x) {
        const int value = message->data[y * free_mask.cols + x];
        const bool blocked = occupied.at<std::uint8_t>(y, x) != 0;
        blocked_cells_[y * free_mask.cols + x] = blocked;
        row[x] = !blocked && ((value < 0 && allow_unknown_) || value >= 0) ? 255 : 0;
      }
    }
    cv::Mat clearance;
    cv::distanceTransform(free_mask, clearance, cv::DIST_L2, 3);
    clearance_meters_.resize(message->data.size());
    for (int y = 0; y < clearance.rows; ++y) {
      const auto * row = clearance.ptr<float>(y);
      for (int x = 0; x < clearance.cols; ++x) {
        clearance_meters_[y * clearance.cols + x] = row[x] * message->info.resolution;
      }
    }
  }

  std::optional<Box> parse_best_box(const std::string & json) const
  {
    static const std::regex pattern(
      R"("confidence":([0-9eE+.-]+),"x":([0-9-]+),"y":([0-9-]+),"width":([0-9-]+),"height":([0-9-]+))");
    std::optional<Box> best;
    for (auto it = std::sregex_iterator(json.begin(), json.end(), pattern);
      it != std::sregex_iterator(); ++it)
    {
      Box box{std::stoi((*it)[2]), std::stoi((*it)[3]), std::stoi((*it)[4]),
        std::stoi((*it)[5]), std::stod((*it)[1])};
      if (!best || box.confidence > best->confidence) {best = box;}
    }
    return best;
  }

  std::optional<rclcpp::Time> detection_stamp(const std::string & json) const
  {
    static const std::regex pattern(R"("stamp":([0-9]+)\.([0-9]{9}))");
    std::smatch match;
    if (!std::regex_search(json, match, pattern)) {
      return std::nullopt;
    }
    return rclcpp::Time(
      std::stoll(match[1]) * 1000000000LL + std::stoll(match[2]), RCL_ROS_TIME);
  }

  std::optional<double> median_depth(const Box & box) const
  {
    if (!depth_ || depth_->data.empty()) {return std::nullopt;}
    const int x0 = std::max(0, box.x + box.width / 3);
    const int x1 = std::min(static_cast<int>(depth_->width), box.x + 2 * box.width / 3);
    const int y0 = std::max(0, box.y + box.height / 3);
    const int y1 = std::min(static_cast<int>(depth_->height), box.y + 2 * box.height / 3);
    std::vector<float> samples;
    for (int y = y0; y < y1; y += 2) {
      for (int x = x0; x < x1; x += 2) {
        float meters = 0.0F;
        const auto offset = static_cast<std::size_t>(y) * depth_->step;
        if (depth_->encoding == sensor_msgs::image_encodings::TYPE_16UC1) {
          const auto * row = reinterpret_cast<const std::uint16_t *>(depth_->data.data() + offset);
          meters = static_cast<float>(row[x]) * 0.001F;
        } else if (depth_->encoding == sensor_msgs::image_encodings::TYPE_32FC1) {
          const auto * row = reinterpret_cast<const float *>(depth_->data.data() + offset);
          meters = row[x];
        } else {
          return std::nullopt;
        }
        if (std::isfinite(meters) && meters > 0.2F && meters < 10.0F) {samples.push_back(meters);}
      }
    }
    if (samples.empty()) {return std::nullopt;}
    const auto middle = samples.begin() + samples.size() / 2;
    std::nth_element(samples.begin(), middle, samples.end());
    return *middle;
  }

  std::optional<geometry_msgs::msg::PointStamped> target_in_current_map()
  {
    if (!target_odom_ || !map_) {return std::nullopt;}
    try {
      const auto transform = tf_buffer_.lookupTransform(
        map_->header.frame_id, "odom", tf2::TimePointZero);
      geometry_msgs::msg::PointStamped target;
      tf2::doTransform(*target_odom_, target, transform);
      target.header.frame_id = map_->header.frame_id;
      target.header.stamp = now();
      return target;
    } catch (const tf2::TransformException & error) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 3000, "Cannot update remembered backpack: %s", error.what());
      return std::nullopt;
    }
  }

  void detection_callback(const std_msgs::msg::String::ConstSharedPtr message)
  {
    if (!visual_odometry_fresh()) {
      clear_path();
      publish_path_valid(false);
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 2000,
        "Visual odometry stale; refusing to plan from EKF prediction alone");
      return;
    }
    const auto box = parse_best_box(message->data);
    if (!box || !map_ || !camera_info_ || depth_buffer_.empty()) {return;}
    const auto stamp = detection_stamp(message->data);
    if (!stamp) {return;}
    double closest_age = std::numeric_limits<double>::infinity();
    sensor_msgs::msg::Image::ConstSharedPtr closest_depth;
    for (const auto & candidate : depth_buffer_) {
      const double age = std::abs((rclcpp::Time(candidate->header.stamp) - *stamp).seconds());
      if (age < closest_age) {
        closest_age = age;
        closest_depth = candidate;
      }
    }
    if (closest_age > 0.07) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 3000,
        "Backpack image has no matching depth frame (nearest %.3f s)", closest_age);
      return;
    }
    depth_ = closest_depth;
    const auto z = median_depth(*box);
    if (!z) {
      RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 3000, "Backpack detected but has no valid depth");
      return;
    }

    const double u = box->x + box->width * 0.5;
    const double v = box->y + box->height * 0.5;
    geometry_msgs::msg::PointStamped camera_point;
    camera_point.header = depth_->header;
    camera_point.point.x = (u - camera_info_->k[2]) * *z / camera_info_->k[0];
    camera_point.point.y = (v - camera_info_->k[5]) * *z / camera_info_->k[4];
    camera_point.point.z = *z;

    try {
      const auto transform = tf_buffer_.lookupTransform(
        "odom", camera_point.header.frame_id, camera_point.header.stamp,
        rclcpp::Duration::from_seconds(0.15));
      geometry_msgs::msg::PointStamped target;
      tf2::doTransform(camera_point, target, transform);
      target.header.frame_id = "odom";
      if (!target_odom_ ||
        std::hypot(target.point.x - target_odom_->point.x,
        target.point.y - target_odom_->point.y) > 1.0)
      {
        target_odom_ = target;
      } else {
        target_odom_->header = target.header;
        target_odom_->point.x += goal_smoothing_alpha_ *
          (target.point.x - target_odom_->point.x);
        target_odom_->point.y += goal_smoothing_alpha_ *
          (target.point.y - target_odom_->point.y);
        target_odom_->point.z += goal_smoothing_alpha_ *
          (target.point.z - target_odom_->point.z);
      }
      const auto current_target = target_in_current_map();
      if (current_target) {
        goal_pub_->publish(*current_target);
        plan_to(*current_target);
      }
    } catch (const tf2::TransformException & error) {
      RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 3000, "Cannot transform backpack: %s", error.what());
    }
  }

  bool world_to_cell(double wx, double wy, int & x, int & y) const
  {
    const auto & origin = map_->info.origin;
    const double yaw = 2.0 * std::atan2(origin.orientation.z, origin.orientation.w);
    const double dx = wx - origin.position.x;
    const double dy = wy - origin.position.y;
    const double local_x = std::cos(yaw) * dx + std::sin(yaw) * dy;
    const double local_y = -std::sin(yaw) * dx + std::cos(yaw) * dy;
    x = static_cast<int>(std::floor(local_x / map_->info.resolution));
    y = static_cast<int>(std::floor(local_y / map_->info.resolution));
    return x >= 0 && y >= 0 && x < static_cast<int>(map_->info.width) &&
           y < static_cast<int>(map_->info.height);
  }

  geometry_msgs::msg::Point cell_to_world(int x, int y) const
  {
    const auto & origin = map_->info.origin;
    const double yaw = 2.0 * std::atan2(origin.orientation.z, origin.orientation.w);
    const double lx = (x + 0.5) * map_->info.resolution;
    const double ly = (y + 0.5) * map_->info.resolution;
    geometry_msgs::msg::Point point;
    point.x = origin.position.x + std::cos(yaw) * lx - std::sin(yaw) * ly;
    point.y = origin.position.y + std::sin(yaw) * lx + std::cos(yaw) * ly;
    return point;
  }

  bool traversable(int x, int y) const
  {
    const int radius = static_cast<int>(std::ceil(inflation_radius_ / map_->info.resolution));
    const int width = static_cast<int>(map_->info.width);
    const int height = static_cast<int>(map_->info.height);
    for (int oy = -radius; oy <= radius; ++oy) {
      for (int ox = -radius; ox <= radius; ++ox) {
        if (ox * ox + oy * oy > radius * radius) {continue;}
        const int nx = x + ox;
        const int ny = y + oy;
        if (nx < 0 || ny < 0 || nx >= width || ny >= height) {return false;}
        if (have_planning_start_ &&
          std::hypot(nx - planning_start_x_, ny - planning_start_y_) *
          map_->info.resolution <= start_ignore_radius_)
        {
          // Depth returns from the wearer's body and the occupied cell under
          // the camera must not disconnect the route at its origin.
          continue;
        }
        if (blocked_cells_.size() == map_->data.size() && blocked_cells_[ny * width + nx]) {
          return false;
        }
        const int value = map_->data[ny * width + nx];
        if (value < 0) {
          if (!allow_unknown_) {return false;}
        } else if (value >= obstacle_threshold_) {
          return false;
        }
      }
    }
    return true;
  }

  double clearance_penalty(int x, int y) const
  {
    if (clearance_meters_.size() != map_->data.size()) {return 0.0;}
    const double clearance = clearance_meters_[y * map_->info.width + x];
    const double deficit = std::max(0.0, preferred_clearance_ - clearance);
    return clearance_weight_ * deficit / std::max(0.01, preferred_clearance_);
  }

  bool line_of_sight(
    const std::pair<int, int> & from, const std::pair<int, int> & to) const
  {
    int x = from.first;
    int y = from.second;
    const int dx = std::abs(to.first - x);
    const int dy = std::abs(to.second - y);
    const int sx = x < to.first ? 1 : -1;
    const int sy = y < to.second ? 1 : -1;
    int error = dx - dy;
    while (true) {
      if (!traversable(x, y)) {return false;}
      if (x == to.first && y == to.second) {return true;}
      const int previous_x = x;
      const int previous_y = y;
      const int doubled = 2 * error;
      if (doubled > -dy) {error -= dy; x += sx;}
      if (doubled < dx) {error += dx; y += sy;}
      if (x != previous_x && y != previous_y &&
        (!traversable(x, previous_y) || !traversable(previous_x, y)))
      {
        return false;
      }
    }
  }

  std::vector<std::pair<int, int>> simplify_path(
    const std::vector<std::pair<int, int>> & input) const
  {
    if (input.size() < 3) {return input;}
    std::vector<std::pair<int, int>> output{input.front()};
    std::size_t anchor = 0;
    while (anchor + 1 < input.size()) {
      std::size_t furthest = input.size() - 1;
      while (furthest > anchor + 1 && !line_of_sight(input[anchor], input[furthest])) {
        --furthest;
      }
      output.push_back(input[furthest]);
      anchor = furthest;
    }
    return output;
  }

  std::optional<std::pair<int, int>> nearest_free(int x, int y) const
  {
    const int max_radius = std::max(3, static_cast<int>(1.5 / map_->info.resolution));
    for (int radius = 0; radius <= max_radius; ++radius) {
      for (int oy = -radius; oy <= radius; ++oy) {
        for (int ox = -radius; ox <= radius; ++ox) {
          if (std::max(std::abs(ox), std::abs(oy)) != radius) {continue;}
          if (traversable(x + ox, y + oy)) {return {{x + ox, y + oy}};}
        }
      }
    }
    return std::nullopt;
  }

  void plan_to(const geometry_msgs::msg::PointStamped & detected_target)
  {
    // Limit unsuccessful A* retries too. A target outside the current map
    // would otherwise trigger a full search on every 50 ms watchdog tick.
    last_plan_attempt_ = std::chrono::steady_clock::now();
    geometry_msgs::msg::TransformStamped camera_tf;
    try {
      camera_tf = tf_buffer_.lookupTransform(
        map_->header.frame_id, "camera_link", tf2::TimePointZero);
    } catch (const tf2::TransformException &) {return;}

    const double sxw = camera_tf.transform.translation.x;
    const double syw = camera_tf.transform.translation.y;
    double gxw = detected_target.point.x;
    double gyw = detected_target.point.y;
    const double distance = std::hypot(gxw - sxw, gyw - syw);
    if (distance > standoff_distance_) {
      const double scale = (distance - standoff_distance_) / distance;
      gxw = sxw + (gxw - sxw) * scale;
      gyw = syw + (gyw - syw) * scale;
    }

    int sx, sy, gx, gy;
    if (!world_to_cell(sxw, syw, sx, sy) || !world_to_cell(gxw, gyw, gx, gy)) {return;}
    planning_start_x_ = sx;
    planning_start_y_ = sy;
    have_planning_start_ = true;
    const auto start = nearest_free(sx, sy);
    const auto goal = nearest_free(gx, gy);
    if (!start || !goal) {
      RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 3000, "No free map cell near start or backpack");
      return;
    }

    const int width = static_cast<int>(map_->info.width);
    const int cells = width * static_cast<int>(map_->info.height);
    const int states = cells * kDirections;
    const double inf = std::numeric_limits<double>::infinity();
    std::vector<double> costs(states, inf);
    std::vector<int> parent(states, -1);
    std::priority_queue<OpenNode, std::vector<OpenNode>, Greater> open;
    for (int heading = 0; heading < kDirections; ++heading) {
      const int state = ((start->second * width + start->first) * kDirections) + heading;
      costs[state] = 0.0;
      open.push({std::hypot(goal->first - start->first, goal->second - start->second), 0.0, state});
    }

    int final_state = -1;
    while (!open.empty()) {
      const OpenNode current = open.top(); open.pop();
      if (current.g != costs[current.state]) {continue;}
      const int cell = current.state / kDirections;
      const int heading = current.state % kDirections;
      const int x = cell % width;
      const int y = cell / width;
      if (x == goal->first && y == goal->second) {final_state = current.state; break;}
      for (int next_heading = 0; next_heading < kDirections; ++next_heading) {
        const int nx = x + kDx[next_heading];
        const int ny = y + kDy[next_heading];
        if (!traversable(nx, ny)) {continue;}
        // Do not let a diagonal step squeeze between two obstacle corners.
        if (kDx[next_heading] != 0 && kDy[next_heading] != 0 &&
          (!traversable(x + kDx[next_heading], y) ||
          !traversable(x, y + kDy[next_heading])))
        {
          continue;
        }
        int heading_delta = std::abs(next_heading - heading);
        heading_delta = std::min(heading_delta, kDirections - heading_delta);
        const double step = (next_heading % 2 == 0) ? 1.0 : std::sqrt(2.0);
        const double next_g = current.g + step + turn_penalty_ * heading_delta +
          clearance_penalty(nx, ny);
        const int next_state = ((ny * width + nx) * kDirections) + next_heading;
        if (next_g >= costs[next_state]) {continue;}
        costs[next_state] = next_g;
        parent[next_state] = current.state;
        const double heuristic = std::hypot(goal->first - nx, goal->second - ny);
        open.push({next_g + heuristic, next_g, next_state});
      }
    }
    if (final_state < 0) {
      RCLCPP_WARN_THROTTLE(get_logger(), *get_clock(), 3000, "No known-free path to backpack");
      return;
    }

    std::vector<std::pair<int, int>> cells_path;
    for (int state = final_state; state >= 0; state = parent[state]) {
      const int cell = state / kDirections;
      cells_path.emplace_back(cell % width, cell / width);
    }
    std::reverse(cells_path.begin(), cells_path.end());
    cells_path = simplify_path(cells_path);
    nav_msgs::msg::Path path;
    path.header.frame_id = map_->header.frame_id;
    path.header.stamp = now();
    // Anchor the displayed route at the wearer, not at the center of the
    // nearest occupancy-grid cell. A* itself still starts on a traversable
    // cell so obstacle guarantees apply to the planned portion.
    geometry_msgs::msg::PoseStamped wearer_pose;
    wearer_pose.header = path.header;
    wearer_pose.pose.position.x = camera_tf.transform.translation.x;
    wearer_pose.pose.position.y = camera_tf.transform.translation.y;
    wearer_pose.pose.position.z = camera_tf.transform.translation.z;
    wearer_pose.pose.orientation = camera_tf.transform.rotation;
    path.poses.push_back(wearer_pose);
    for (std::size_t index = 0; index < cells_path.size(); ++index) {
      geometry_msgs::msg::PoseStamped pose;
      pose.header = path.header;
      pose.pose.position = cell_to_world(cells_path[index].first, cells_path[index].second);
      if (index + 1 < cells_path.size()) {
        const auto next = cell_to_world(cells_path[index + 1].first, cells_path[index + 1].second);
        const double yaw = std::atan2(next.y - pose.pose.position.y, next.x - pose.pose.position.x);
        pose.pose.orientation.z = std::sin(yaw * 0.5);
        pose.pose.orientation.w = std::cos(yaw * 0.5);
      } else {
        pose.pose.orientation.w = 1.0;
      }
      path.poses.push_back(pose);
    }
    current_path_ = path;
    path_pub_->publish(path);
    publish_path_valid(true);
  }

  void image_callback(const sensor_msgs::msg::Image::ConstSharedPtr message)
  {
    if (message->encoding != sensor_msgs::image_encodings::RGB8 || message->data.empty()) {return;}
    cv::Mat source(static_cast<int>(message->height), static_cast<int>(message->width), CV_8UC3,
      const_cast<unsigned char *>(message->data.data()), message->step);
    cv::Mat output = source.clone();
    std::vector<std::optional<cv::Point>> pixels;
    const bool tracking_valid = visual_odometry_fresh();
    if (!tracking_valid) {
      clear_path();
      publish_path_valid(false);
      cv::rectangle(output, cv::Rect(0, 0, output.cols, 58), cv::Scalar(220, 30, 30), cv::FILLED);
      cv::putText(output, "TRACKING LOST - STOP", cv::Point(18, 39),
        cv::FONT_HERSHEY_SIMPLEX, 0.95, cv::Scalar(255, 255, 255), 3, cv::LINE_AA);
    }
    if (tracking_valid && camera_info_ && current_path_ && !current_path_->poses.empty()) {
      try {
        const auto transform = tf_buffer_.lookupTransform(
          message->header.frame_id, current_path_->header.frame_id, message->header.stamp,
          rclcpp::Duration::from_seconds(0.1));
        for (const auto & pose : current_path_->poses) {
          geometry_msgs::msg::PointStamped map_point, camera_point;
          map_point.header = current_path_->header;
          map_point.point = pose.pose.position;
          tf2::doTransform(map_point, camera_point, transform);
          if (camera_point.point.z <= 0.1) {pixels.push_back(std::nullopt); continue;}
          const int u = std::lround(camera_info_->k[0] * camera_point.point.x / camera_point.point.z + camera_info_->k[2]);
          const int v = std::lround(camera_info_->k[4] * camera_point.point.y / camera_point.point.z + camera_info_->k[5]);
          pixels.emplace_back(cv::Point(u, v));
        }
      } catch (const tf2::TransformException &) {}
    }
    bool route_drawn = false;
    const cv::Rect image_bounds(0, 0, output.cols, output.rows);
    // The camera origin itself has zero optical depth and cannot be projected.
    // Use the bottom-center of the image as the wearer's visual anchor and
    // connect it to the first forward route point.
    for (std::size_t i = 1; i < pixels.size(); ++i) {
      if (!pixels[i]) {continue;}
      cv::Point from(output.cols / 2, output.rows - 1);
      cv::Point to = *pixels[i];
      if (cv::clipLine(image_bounds, from, to)) {
        cv::arrowedLine(
          output, from, to, cv::Scalar(255, 60, 30), 5, cv::LINE_AA, 0, 0.12);
        route_drawn = true;
      }
      break;
    }
    for (std::size_t i = 1; i < pixels.size(); ++i) {
      if (!pixels[i - 1] || !pixels[i]) {continue;}
      cv::Point from = **(pixels.begin() + i - 1);
      cv::Point to = **(pixels.begin() + i);
      if (cv::clipLine(image_bounds, from, to)) {
        cv::line(output, from, to, cv::Scalar(255, 60, 30), 5, cv::LINE_AA);
        route_drawn = true;
      }
    }
    if (route_drawn) {
      cv::putText(output, "A* route", cv::Point(18, output.rows - 22),
        cv::FONT_HERSHEY_SIMPLEX, 0.7, cv::Scalar(255, 60, 30), 2, cv::LINE_AA);
    }
    sensor_msgs::msg::Image published = *message;
    published.data.assign(output.datastart, output.dataend);
    image_pub_->publish(std::move(published));
  }

  double turn_penalty_;
  int obstacle_threshold_;
  double inflation_radius_;
  double standoff_distance_;
  double max_prediction_age_;
  bool allow_unknown_;
  double preferred_clearance_;
  double clearance_weight_;
  double goal_smoothing_alpha_;
  double start_ignore_radius_;
  double wall_closing_radius_;
  int planning_start_x_{0};
  int planning_start_y_{0};
  bool have_planning_start_{false};
  bool have_visual_odom_{false};
  std::chrono::steady_clock::time_point last_visual_odom_{};
  std::chrono::steady_clock::time_point last_plan_attempt_{};
  tf2_ros::Buffer tf_buffer_;
  tf2_ros::TransformListener tf_listener_;
  nav_msgs::msg::OccupancyGrid::ConstSharedPtr map_;
  std::vector<float> clearance_meters_;
  std::vector<bool> blocked_cells_;
  sensor_msgs::msg::Image::ConstSharedPtr depth_;
  std::deque<sensor_msgs::msg::Image::ConstSharedPtr> depth_buffer_;
  sensor_msgs::msg::CameraInfo::ConstSharedPtr camera_info_;
  std::optional<nav_msgs::msg::Path> current_path_;
  std::optional<geometry_msgs::msg::PointStamped> target_odom_;
  rclcpp::Publisher<nav_msgs::msg::Path>::SharedPtr path_pub_;
  rclcpp::Publisher<geometry_msgs::msg::PointStamped>::SharedPtr goal_pub_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr path_valid_pub_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr image_pub_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr map_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr depth_sub_;
  rclcpp::Subscription<sensor_msgs::msg::CameraInfo>::SharedPtr info_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr image_sub_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr detection_sub_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr visual_odom_sub_;
  rclcpp::TimerBase::SharedPtr watchdog_timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<BackpackPathPlanner>());
  rclcpp::shutdown();
  return 0;
}
