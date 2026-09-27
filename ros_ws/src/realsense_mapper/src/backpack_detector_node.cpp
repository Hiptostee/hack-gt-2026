#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/dnn.hpp>
#include <opencv2/imgproc.hpp>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/image_encodings.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "std_msgs/msg/string.hpp"

using std::placeholders::_1;

class BackpackDetector final : public rclcpp::Node
{
public:
  BackpackDetector()
  : Node("backpack_detector")
  {
    model_path_ = declare_parameter<std::string>("model_path", "/opt/models/yolox.onnx");
    confidence_threshold_ = declare_parameter<double>("confidence_threshold", 0.30);
    nms_threshold_ = declare_parameter<double>("nms_threshold", 0.45);
    max_inference_fps_ = declare_parameter<double>("max_inference_fps", 4.0);
    require_dark_ = declare_parameter<bool>("require_dark", true);
    dark_value_threshold_ = declare_parameter<int>("dark_value_threshold", 105);
    minimum_dark_ratio_ = declare_parameter<double>("minimum_dark_ratio", 0.18);

    // Keep inference from occupying multiple Pi cores while odometry runs.
    cv::setNumThreads(1);
    net_ = cv::dnn::readNetFromONNX(model_path_);
    net_.setPreferableBackend(cv::dnn::DNN_BACKEND_OPENCV);
    net_.setPreferableTarget(cv::dnn::DNN_TARGET_CPU);

    const auto qos = rclcpp::SensorDataQoS();
    annotated_publisher_ = create_publisher<sensor_msgs::msg::Image>(
      "/yolo/annotated_image", qos);
    detection_publisher_ = create_publisher<std_msgs::msg::String>(
      "/yolo/black_backpack", 10);
    image_subscription_ = create_subscription<sensor_msgs::msg::Image>(
      "/camera/color/image_raw", qos,
      std::bind(&BackpackDetector::image_callback, this, _1));

    RCLCPP_INFO(
      get_logger(), "YOLOX loaded; looking for dark COCO backpacks at %.1f FPS",
      max_inference_fps_);
  }

private:
  struct Detection
  {
    cv::Rect box;
    float confidence;
    double dark_ratio;
    double mean_value;
  };

  static constexpr int kInputSize = 640;
  static constexpr int kBackpackClass = 24;  // COCO's zero-indexed backpack class.

  void image_callback(const sensor_msgs::msg::Image::ConstSharedPtr message)
  {
    const auto now = std::chrono::steady_clock::now();
    const double minimum_period = 1.0 / std::max(0.1, max_inference_fps_);
    if (last_inference_.time_since_epoch().count() != 0 &&
      std::chrono::duration<double>(now - last_inference_).count() < minimum_period)
    {
      return;
    }
    if (message->encoding != sensor_msgs::image_encodings::RGB8 ||
      message->step < message->width * 3U || message->data.empty())
    {
      RCLCPP_ERROR_THROTTLE(
        get_logger(), *get_clock(), 5000, "Expected a non-empty RGB8 camera image");
      return;
    }
    const cv::Mat rgb(
      static_cast<int>(message->height), static_cast<int>(message->width), CV_8UC3,
      const_cast<unsigned char *>(message->data.data()), message->step);

    cv::Mat annotated = rgb.clone();
    std::vector<Detection> detections;
    try {
      detections = detect(rgb);
    } catch (const std::exception & error) {
      last_inference_ = std::chrono::steady_clock::now();
      RCLCPP_ERROR_THROTTLE(
        get_logger(), *get_clock(), 5000, "YOLO inference failed: %s", error.what());
      return;
    }

    for (const auto & detection : detections) {
      cv::rectangle(annotated, detection.box, cv::Scalar(30, 255, 80), 3);
      std::ostringstream label;
      label << "black backpack " << std::fixed << std::setprecision(0)
            << detection.confidence * 100.0F << "%";
      int baseline = 0;
      const auto size = cv::getTextSize(label.str(), cv::FONT_HERSHEY_SIMPLEX, 0.65, 2,
        &baseline);
      const int label_y = std::max(size.height + 8, detection.box.y);
      cv::rectangle(
        annotated,
        cv::Rect(detection.box.x, label_y - size.height - 8, size.width + 10, size.height + 8),
        cv::Scalar(30, 255, 80), cv::FILLED);
      cv::putText(
        annotated, label.str(), cv::Point(detection.box.x + 5, label_y - 5),
        cv::FONT_HERSHEY_SIMPLEX, 0.65, cv::Scalar(0, 0, 0), 2, cv::LINE_AA);
    }

    if (detections.empty()) {
      cv::putText(
        annotated, "Looking for a black backpack...", cv::Point(18, 34),
        cv::FONT_HERSHEY_SIMPLEX, 0.75, cv::Scalar(255, 210, 30), 2, cv::LINE_AA);
    }

    sensor_msgs::msg::Image output;
    output.header = message->header;
    output.height = static_cast<std::uint32_t>(annotated.rows);
    output.width = static_cast<std::uint32_t>(annotated.cols);
    output.encoding = sensor_msgs::image_encodings::RGB8;
    output.is_bigendian = false;
    output.step = static_cast<sensor_msgs::msg::Image::_step_type>(annotated.cols * 3);
    output.data.assign(annotated.datastart, annotated.dataend);
    annotated_publisher_->publish(std::move(output));
    publish_detection_message(message, detections);
    // Start the cooldown after inference, including when inference is slower
    // than the configured period.
    last_inference_ = std::chrono::steady_clock::now();
  }

  std::vector<Detection> detect(const cv::Mat & rgb)
  {
    const float scale = std::min(
      static_cast<float>(kInputSize) / rgb.cols,
      static_cast<float>(kInputSize) / rgb.rows);
    const int resized_width = std::lround(rgb.cols * scale);
    const int resized_height = std::lround(rgb.rows * scale);
    // OpenCV Zoo's YOLOX export uses top-left letterboxing.
    const int pad_x = 0;
    const int pad_y = 0;

    cv::Mat resized;
    cv::resize(rgb, resized, cv::Size(resized_width, resized_height));
    cv::Mat letterboxed(kInputSize, kInputSize, CV_8UC3, cv::Scalar(114, 114, 114));
    resized.copyTo(letterboxed(cv::Rect(pad_x, pad_y, resized_width, resized_height)));

    // The source is already RGB, which is the channel order expected by YOLO.
    const cv::Mat blob = cv::dnn::blobFromImage(
      letterboxed, 1.0, cv::Size(kInputSize, kInputSize), cv::Scalar(), false, false);
    net_.setInput(blob);
    std::vector<cv::Mat> outputs;
    net_.forward(outputs, net_.getUnconnectedOutLayersNames());
    if (outputs.empty() || outputs.front().dims != 3) {
      throw std::runtime_error("Unexpected YOLO output shape");
    }

    cv::Mat predictions(outputs.front().size[1], outputs.front().size[2], CV_32F,
      outputs.front().ptr<float>());
    if (predictions.rows < predictions.cols) {
      cv::transpose(predictions, predictions);
    }

    std::vector<cv::Rect> candidate_boxes;
    std::vector<float> candidate_scores;
    for (int row_index = 0; row_index < predictions.rows; ++row_index) {
      const float * row = predictions.ptr<float>(row_index);
      if (predictions.cols <= 5 + kBackpackClass || row_index >= 8400) {
        continue;
      }
      const float confidence = row[4] * row[5 + kBackpackClass];
      if (confidence < confidence_threshold_) {
        continue;
      }

      int grid_index = row_index;
      int stride = 8;
      int grid_width = 80;
      if (grid_index >= 6400) {
        grid_index -= 6400;
        stride = 16;
        grid_width = 40;
      }
      if (grid_index >= 1600) {
        grid_index -= 1600;
        stride = 32;
        grid_width = 20;
      }
      const int grid_x = grid_index % grid_width;
      const int grid_y = grid_index / grid_width;
      const float center_x = (row[0] + grid_x) * stride;
      const float center_y = (row[1] + grid_y) * stride;
      const float model_width = std::exp(row[2]) * stride;
      const float model_height = std::exp(row[3]) * stride;
      const float x = (center_x - model_width / 2.0F - pad_x) / scale;
      const float y = (center_y - model_height / 2.0F - pad_y) / scale;
      const float width = model_width / scale;
      const float height = model_height / scale;
      cv::Rect box(
        std::lround(x), std::lround(y), std::lround(width), std::lround(height));
      box &= cv::Rect(0, 0, rgb.cols, rgb.rows);
      if (box.area() > 0) {
        candidate_boxes.push_back(box);
        candidate_scores.push_back(confidence);
      }
    }

    std::vector<int> kept_indices;
    cv::dnn::NMSBoxes(
      candidate_boxes, candidate_scores, confidence_threshold_, nms_threshold_, kept_indices);

    std::vector<Detection> detections;
    for (const int index : kept_indices) {
      const auto [dark_ratio, mean_value] = darkness(rgb, candidate_boxes[index]);
      if (!require_dark_ || dark_ratio >= minimum_dark_ratio_ || mean_value < dark_value_threshold_) {
        detections.push_back(
          {candidate_boxes[index], candidate_scores[index], dark_ratio, mean_value});
      }
    }
    return detections;
  }

  std::pair<double, double> darkness(const cv::Mat & rgb, const cv::Rect & box) const
  {
    const int inset_x = std::lround(box.width * 0.15);
    const int inset_y = std::lround(box.height * 0.15);
    cv::Rect inner(
      box.x + inset_x, box.y + inset_y,
      std::max(1, box.width - 2 * inset_x), std::max(1, box.height - 2 * inset_y));
    inner &= cv::Rect(0, 0, rgb.cols, rgb.rows);

    cv::Mat hsv;
    cv::cvtColor(rgb(inner), hsv, cv::COLOR_RGB2HSV);
    std::vector<cv::Mat> channels;
    cv::split(hsv, channels);
    const cv::Mat dark_mask = channels[2] < dark_value_threshold_;
    const double dark_ratio = static_cast<double>(cv::countNonZero(dark_mask)) / inner.area();
    const double mean_value = cv::mean(channels[2])[0];
    return {dark_ratio, mean_value};
  }

  void publish_detection_message(
    const sensor_msgs::msg::Image::ConstSharedPtr & image,
    const std::vector<Detection> & detections)
  {
    std_msgs::msg::String message;
    std::ostringstream json;
    json << "{\"stamp\":" << image->header.stamp.sec << "."
         << std::setw(9) << std::setfill('0') << image->header.stamp.nanosec
         << ",\"detections\":[";
    for (std::size_t i = 0; i < detections.size(); ++i) {
      const auto & detection = detections[i];
      if (i != 0) {
        json << ',';
      }
      json << "{\"label\":\"black backpack\",\"confidence\":" << detection.confidence
           << ",\"x\":" << detection.box.x << ",\"y\":" << detection.box.y
           << ",\"width\":" << detection.box.width
           << ",\"height\":" << detection.box.height
           << ",\"dark_ratio\":" << detection.dark_ratio << '}';
    }
    json << "]}";
    message.data = json.str();
    detection_publisher_->publish(message);
  }

  std::string model_path_;
  double confidence_threshold_;
  double nms_threshold_;
  double max_inference_fps_;
  bool require_dark_;
  int dark_value_threshold_;
  double minimum_dark_ratio_;
  cv::dnn::Net net_;
  std::chrono::steady_clock::time_point last_inference_{};
  rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr image_subscription_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr annotated_publisher_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr detection_publisher_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<BackpackDetector>());
  } catch (const std::exception & error) {
    std::cerr << "Backpack detector failed: " << error.what() << '\n';
    rclcpp::shutdown();
    return 1;
  }
  rclcpp::shutdown();
  return 0;
}
