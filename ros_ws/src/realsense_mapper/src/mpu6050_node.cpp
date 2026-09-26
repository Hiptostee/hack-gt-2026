#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>
#include <thread>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/imu.hpp"

namespace
{
constexpr double kGravity = 9.80665;
constexpr double kDegreesToRadians = 3.14159265358979323846 / 180.0;

int16_t signed_word(uint8_t high, uint8_t low)
{
  return static_cast<int16_t>((static_cast<uint16_t>(high) << 8) | low);
}
}  // namespace

class Mpu6050Node : public rclcpp::Node
{
public:
  Mpu6050Node()
  : Node("mpu6050")
  {
    const int bus = declare_parameter("i2c_bus", 1);
    address_ = declare_parameter("i2c_address", 0x68);
    frame_id_ = declare_parameter("frame_id", "imu_link");
    rate_hz_ = std::clamp(declare_parameter("rate_hz", 100.0), 10.0, 200.0);
    const int calibration_samples = static_cast<int>(std::max<int64_t>(
      0, declare_parameter("calibration_samples", 500)));

    const std::string device = "/dev/i2c-" + std::to_string(bus);
    fd_ = open(device.c_str(), O_RDWR);
    if (fd_ < 0) {
      throw std::runtime_error("Cannot open " + device + "; check the i2c group");
    }
    if (ioctl(fd_, I2C_SLAVE, address_) < 0) {
      throw std::runtime_error("Cannot select MPU6050 I2C address");
    }

    const uint8_t who_am_i = read_register(0x75);
    if (who_am_i != 0x68 && who_am_i != 0x69) {
      throw std::runtime_error(
              "Unexpected WHO_AM_I value 0x" + hex_byte(who_am_i));
    }

    // Wake the device, use the X gyro PLL, 44 Hz DLPF, +/-4 g and +/-500 deg/s.
    write_register(0x6B, 0x01);
    write_register(0x1A, 0x03);
    const int divider = std::clamp(
      static_cast<int>(std::lround(1000.0 / rate_hz_)) - 1, 0, 255);
    write_register(0x19, static_cast<uint8_t>(divider));
    write_register(0x1C, 0x08);
    write_register(0x1B, 0x08);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    publisher_ = create_publisher<sensor_msgs::msg::Imu>(
      "/imu/data_raw", rclcpp::SensorDataQoS());

    calibrate(calibration_samples);
    timer_ = create_wall_timer(
      std::chrono::duration<double>(1.0 / rate_hz_),
      std::bind(&Mpu6050Node::publish_sample, this));
  }

  ~Mpu6050Node() override
  {
    if (fd_ >= 0) {
      close(fd_);
    }
  }

private:
  void write_register(uint8_t reg, uint8_t value)
  {
    const std::array<uint8_t, 2> data{reg, value};
    if (write(fd_, data.data(), data.size()) != static_cast<ssize_t>(data.size())) {
      throw std::runtime_error("MPU6050 register write failed");
    }
  }

  uint8_t read_register(uint8_t reg)
  {
    uint8_t value = 0;
    if (write(fd_, &reg, 1) != 1 || read(fd_, &value, 1) != 1) {
      throw std::runtime_error("MPU6050 register read failed");
    }
    return value;
  }

  std::array<int16_t, 7> read_sample()
  {
    uint8_t reg = 0x3B;
    std::array<uint8_t, 14> raw{};
    if (write(fd_, &reg, 1) != 1 ||
      read(fd_, raw.data(), raw.size()) != static_cast<ssize_t>(raw.size()))
    {
      throw std::runtime_error("MPU6050 sample read failed");
    }
    return {
      signed_word(raw[0], raw[1]), signed_word(raw[2], raw[3]),
      signed_word(raw[4], raw[5]), signed_word(raw[6], raw[7]),
      signed_word(raw[8], raw[9]), signed_word(raw[10], raw[11]),
      signed_word(raw[12], raw[13])};
  }

  void calibrate(int samples)
  {
    if (samples == 0) {
      return;
    }
    RCLCPP_INFO(
      get_logger(), "Keep the camera still: calibrating gyro with %d samples", samples);
    std::array<double, 3> total{};
    for (int i = 0; i < samples; ++i) {
      const auto sample = read_sample();
      total[0] += sample[4];
      total[1] += sample[5];
      total[2] += sample[6];
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    for (size_t i = 0; i < gyro_bias_.size(); ++i) {
      gyro_bias_[i] = total[i] / samples;
    }
    RCLCPP_INFO(
      get_logger(), "Gyro calibration complete: bias=[%.2f, %.2f, %.2f] LSB",
      gyro_bias_[0], gyro_bias_[1], gyro_bias_[2]);
  }

  void publish_sample()
  {
    try {
      const auto sample = read_sample();
      sensor_msgs::msg::Imu message;
      message.header.stamp = now();
      message.header.frame_id = frame_id_;

      constexpr double acceleration_scale = kGravity / 8192.0;
      constexpr double angular_scale = kDegreesToRadians / 65.5;
      message.linear_acceleration.x = sample[0] * acceleration_scale;
      message.linear_acceleration.y = sample[1] * acceleration_scale;
      message.linear_acceleration.z = sample[2] * acceleration_scale;
      message.angular_velocity.x = (sample[4] - gyro_bias_[0]) * angular_scale;
      message.angular_velocity.y = (sample[5] - gyro_bias_[1]) * angular_scale;
      message.angular_velocity.z = (sample[6] - gyro_bias_[2]) * angular_scale;

      // The raw sensor has no orientation estimate.
      message.orientation_covariance[0] = -1.0;
      message.angular_velocity_covariance = {
        0.0004, 0.0, 0.0, 0.0, 0.0004, 0.0, 0.0, 0.0, 0.0004};
      message.linear_acceleration_covariance = {
        0.0225, 0.0, 0.0, 0.0, 0.0225, 0.0, 0.0, 0.0, 0.0225};
      publisher_->publish(message);
    } catch (const std::exception & error) {
      RCLCPP_ERROR_THROTTLE(get_logger(), *get_clock(), 2000, "%s", error.what());
    }
  }

  static std::string hex_byte(uint8_t value)
  {
    constexpr char digits[] = "0123456789abcdef";
    return {digits[value >> 4], digits[value & 0x0f]};
  }

  int fd_{-1};
  int address_{0x68};
  double rate_hz_{100.0};
  std::string frame_id_;
  std::array<double, 3> gyro_bias_{};
  rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr publisher_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<Mpu6050Node>());
  } catch (const std::exception & error) {
    RCLCPP_FATAL(rclcpp::get_logger("mpu6050"), "%s", error.what());
    rclcpp::shutdown();
    return 1;
  }
  rclcpp::shutdown();
  return 0;
}
