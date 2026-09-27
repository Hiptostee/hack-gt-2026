#include <chrono>
#include <iomanip>
#include <mutex>
#include <random>
#include <sstream>
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "sensor_msgs/msg/camera_info.hpp"
#include "sensor_msgs/msg/imu.hpp"
#include "std_msgs/msg/string.hpp"
#include "hazard_warnings/policy.hpp"

using namespace std::chrono_literals;
class HazardWarnings final : public rclcpp::Node {
  using Steady=std::chrono::steady_clock;
  hazards::Config config_;
  hazards::Transform camera_body_,imu_body_;
  hazards::Policy policy_;
  bool calibrated_{false},coverage_verified_{false};
  double scale16_{0.001},max_tilt_{0.2},max_rotation_{0.7};
  std::string camera_frame_,imu_frame_,session_,depth_health_{"unavailable"},pose_health_{"unavailable"};
  uint64_t sequence_{0};
  int64_t last_stamp_{0},last_clock_{0};
  Steady::time_point last_processed_{},last_clock_steady_{};
  std::mutex input_mutex_,state_mutex_;
  sensor_msgs::msg::Image::ConstSharedPtr depth_;
  sensor_msgs::msg::CameraInfo::ConstSharedPtr info_;
  sensor_msgs::msg::Imu::ConstSharedPtr imu_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_;
  rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr depth_sub_;
  rclcpp::Subscription<sensor_msgs::msg::CameraInfo>::SharedPtr info_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_sub_;
  rclcpp::CallbackGroup::SharedPtr processing_group_,heartbeat_group_;
  rclcpp::TimerBase::SharedPtr process_timer_,heartbeat_timer_;
  static int64_t stamp(const builtin_interfaces::msg::Time& s) {return int64_t(s.sec)*1000000000+s.nanosec;}
  static bool fresh(int64_t now,int64_t captured,int64_t limit=150000000) {
    return captured>0 && now>=captured && now-captured<=limit;
  }
  hazards::Transform transform(const std::string& name) {
    auto xyz=declare_parameter<std::vector<double>>(name+"_xyz",{0.0,0.0,0.0});
    auto angles=declare_parameter<std::vector<double>>(name+"_rpy",{0.0,0.0,0.0});
    if(xyz.size()!=3 || angles.size()!=3) throw std::invalid_argument("transform requires three xyz/rpy values");
    for(double x:xyz) if(!std::isfinite(x)) throw std::invalid_argument("nonfinite transform");
    for(double x:angles) if(!std::isfinite(x)) throw std::invalid_argument("nonfinite transform");
    return hazards::rpy(angles[0],angles[1],angles[2],{xyz[0],xyz[1],xyz[2]});
  }
public:
  HazardWarnings():Node("hazard_warnings") {
    calibrated_=declare_parameter("calibrated",false);
    coverage_verified_=declare_parameter("coverage_verified",false);
    camera_frame_=declare_parameter<std::string>("camera_optical_frame","camera_color_optical_frame");
    imu_frame_=declare_parameter<std::string>("imu_frame","imu_link");
    camera_body_=transform("camera_to_body"); imu_body_=transform("imu_to_body");
    config_.half_width=declare_parameter("body_half_width_m",0.0)+declare_parameter("lateral_margin_m",0.15);
    config_.torso_min=declare_parameter("torso_min_m",0.0);
    config_.head_min=declare_parameter("head_min_m",0.0);
    config_.head_max=declare_parameter("head_max_m",0.0);
    config_.caution=declare_parameter("caution_m",1.5);
    config_.urgent=declare_parameter("urgent_m",0.8);
    config_.min_depth=declare_parameter("min_depth_m",0.2);
    config_.max_depth=declare_parameter("max_depth_m",4.0);
    config_.min_support=declare_parameter("min_cluster_pixels",3);
    config_.urgent_support=declare_parameter("urgent_cluster_pixels",12);
    config_.cluster_gap=declare_parameter("cluster_gap_m",0.08);
    config_.min_valid_fraction=declare_parameter("min_valid_fraction",0.5);
    scale16_=declare_parameter("depth_16u_scale_m",0.001);
    max_tilt_=declare_parameter("max_tilt_rad",0.2);
    max_rotation_=declare_parameter("max_rotation_rad_s",0.7);
    if(!(config_.half_width>0.15 && config_.torso_min>0 && config_.head_min>config_.torso_min &&
         config_.head_max>config_.head_min && config_.min_depth>0 && config_.urgent>config_.min_depth &&
         config_.caution>config_.urgent && config_.max_depth>config_.caution+0.2 &&
         config_.cluster_gap>0 && config_.min_valid_fraction>0 && config_.min_valid_fraction<=1 &&
         config_.min_support>=2 && config_.urgent_support>=config_.min_support && scale16_>0 &&
         max_tilt_>0 && max_tilt_<1 && max_rotation_>0)) calibrated_=false;
    std::random_device random;
    std::ostringstream id; id<<std::hex<<random()<<random()<<random()<<random(); session_=id.str();
    pub_=create_publisher<std_msgs::msg::String>("/hazard_warning",rclcpp::QoS(1).reliable().durability_volatile());
    auto qos=rclcpp::SensorDataQoS().keep_last(1);
    depth_sub_=create_subscription<sensor_msgs::msg::Image>(
      "/camera/aligned_depth_to_color/image_raw",qos,[this](sensor_msgs::msg::Image::ConstSharedPtr m) {
        std::lock_guard<std::mutex> lock(input_mutex_); depth_=m;
      });
    info_sub_=create_subscription<sensor_msgs::msg::CameraInfo>(
      "/camera/color/camera_info",qos,[this](sensor_msgs::msg::CameraInfo::ConstSharedPtr m) {
        std::lock_guard<std::mutex> lock(input_mutex_); info_=m;
      });
    imu_sub_=create_subscription<sensor_msgs::msg::Imu>(
      "/imu/data",qos,[this](sensor_msgs::msg::Imu::ConstSharedPtr m) {
        std::lock_guard<std::mutex> lock(input_mutex_); imu_=m;
      });
    processing_group_=create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);
    heartbeat_group_=create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);
    process_timer_=create_wall_timer(20ms,[this]{process();},processing_group_);
    heartbeat_timer_=create_wall_timer(100ms,[this]{publish();},heartbeat_group_);
    RCLCPP_INFO(get_logger(),"Stage A geometry only; calibrated=%d coverage_verified=%d. No floor or semantic labels.",
      calibrated_,coverage_verified_);
  }
private:
  void process() {
    sensor_msgs::msg::Image::ConstSharedPtr depth;
    sensor_msgs::msg::CameraInfo::ConstSharedPtr info;
    sensor_msgs::msg::Imu::ConstSharedPtr imu;
    {std::lock_guard<std::mutex> lock(input_mutex_); depth=depth_; info=info_; imu=imu_;}
    const int64_t now=get_clock()->now().nanoseconds();
    if(!depth) return;
    const int64_t captured=stamp(depth->header.stamp);
    if(captured<=0 || captured>now) return;
    {std::lock_guard<std::mutex> lock(state_mutex_); if(captured<=last_stamp_) return;}
    std::string dh="unavailable",ph="unavailable";
    hazards::Geometry geometry;
    if(calibrated_ && coverage_verified_ && imu && imu->header.frame_id==imu_frame_ &&
       fresh(now,stamp(imu->header.stamp)) && std::abs(stamp(imu->header.stamp)-captured)<=100000000 &&
       imu->linear_acceleration_covariance[0]>=0 && imu->angular_velocity_covariance[0]>=0) {
      const auto& a=imu->linear_acceleration;
      const auto g=imu_body_.rotate({a.x,a.y,a.z});
      const double magnitude=std::sqrt(g.x*g.x+g.y*g.y+g.z*g.z);
      const auto& w=imu->angular_velocity;
      const double rotation=std::sqrt(w.x*w.x+w.y*w.y+w.z*w.z);
      // Fixed measured mount is supported only near calibration posture.
      // Motion/tilt outside this envelope disables geometry, never fakes body height.
      if(std::isfinite(magnitude) && std::abs(magnitude-9.81)<=2.0 &&
         std::acos(std::clamp(g.z/magnitude,-1.0,1.0))<=max_tilt_ && rotation<=max_rotation_) ph="ok";
    }
    if(fresh(now,captured) && info && depth->header.frame_id==camera_frame_ &&
       info->header.frame_id==camera_frame_ && info->width==depth->width && info->height==depth->height &&
       fresh(now,stamp(info->header.stamp),1000000000) && info->binning_x<=1 && info->binning_y<=1 &&
       info->roi.x_offset==0 && info->roi.y_offset==0 &&
       (info->distortion_model=="plumb_bob" || info->distortion_model.empty())) {
      try {
        hazards::Intrinsics k; k.width=info->width; k.height=info->height;
        k.fx=info->k[0]; k.fy=info->k[4]; k.cx=info->k[2]; k.cy=info->k[5];
        if(!(std::isfinite(k.fx) && std::isfinite(k.fy) && k.fx>0 && k.fy>0 &&
            std::isfinite(k.cx) && std::isfinite(k.cy)) || (!info->d.empty() && info->d.size()!=5))
          throw std::invalid_argument("unsupported calibration");
        for(size_t i=0;i<info->d.size();++i) {
          if(!std::isfinite(info->d[i]) || (info->distortion_model.empty() && info->d[i]!=0))
            throw std::invalid_argument("invalid distortion");
          k.distortion[i]=info->d[i];
        }
        auto decoded=hazards::decode_depth(depth->data,depth->width,depth->height,depth->step,
          depth->encoding,depth->is_bigendian,scale16_);
        if(ph=="ok") {
          geometry=hazards::detect(decoded,k,camera_body_,config_);
          dh=geometry.coverage_ok?"ok":"degraded";
        }
      } catch(const std::invalid_argument&) {dh="unavailable";}
    }
    {std::lock_guard<std::mutex> lock(state_mutex_);
      last_stamp_=captured; last_processed_=Steady::now(); depth_health_=dh; pose_health_=ph;
      if(ph=="ok" && dh!="unavailable" && fresh(get_clock()->now().nanoseconds(),captured))
        policy_.update(geometry,captured,config_);
      else policy_.reset();
    }
    publish(); // Important changes do not wait for the heartbeat timer.
  }
  void publish() {
    std::lock_guard<std::mutex> lock(state_mutex_);
    const int64_t now=get_clock()->now().nanoseconds(); const auto steady=Steady::now();
    if(last_clock_ && std::abs(double(now-last_clock_)/1e9-
        std::chrono::duration<double>(steady-last_clock_steady_).count())>0.1) {
      policy_.reset(); last_stamp_=0; depth_health_=pose_health_="unavailable";
    }
    last_clock_=now; last_clock_steady_=steady;
    const bool current=fresh(now,last_stamp_) && steady-last_processed_<=150ms;
    const std::string dh=current?depth_health_:"unavailable",ph=current?pose_health_:"unavailable";
    const auto events=policy_.events(now);
    std::ostringstream json;
    json<<"{\"schema_version\":1,\"source_session\":\""<<session_<<"\",\"sequence\":"<<++sequence_
      <<",\"published_stamp_ns\":"<<now<<",\"health\":{\"depth\":\""<<dh<<"\",\"body_pose\":\""<<ph
      <<"\",\"floor\":\"unavailable\",\"labels\":\"unavailable\"},\"events\":[";
    bool first=true;
    for(const auto& e:events) {
      if(!first) json<<",";
      first=false;
      const char* directions[]={"left","center","right"};
      json<<"{\"id\":\""<<e.id<<"\",\"observed_stamp_ns\":"<<e.observed
        <<",\"ttl_ms\":250,\"kind\":\"upper_body_obstacle\",\"severity\":\""<<(e.urgent?"urgent":"caution")
        <<"\",\"direction\":\""<<directions[e.zone%3]<<"\",\"height_band\":\""<<(e.zone>=3?"head":"torso")
        <<"\",\"distance_m\":"<<std::setprecision(5)<<e.distance<<",\"label\":null,\"evidence\":\"depth_cluster\"}";
    }
    json<<"]}"; std_msgs::msg::String message; message.data=json.str(); pub_->publish(message);
  }
};
int main(int argc,char** argv) {
  rclcpp::init(argc,argv);
  rclcpp::executors::MultiThreadedExecutor executor(rclcpp::ExecutorOptions(),3);
  auto node=std::make_shared<HazardWarnings>();
  executor.add_node(node); executor.spin(); rclcpp::shutdown();
}
