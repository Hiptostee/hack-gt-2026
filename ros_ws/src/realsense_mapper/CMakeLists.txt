cmake_minimum_required(VERSION 3.8)
project(realsense_mapper)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(OpenCV REQUIRED COMPONENTS core dnn imgproc)
find_package(geometry_msgs REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(tf2_geometry_msgs REQUIRED)

add_executable(mapping_status_node src/mapping_status_node.cpp)
target_compile_features(mapping_status_node PUBLIC cxx_std_17)
ament_target_dependencies(mapping_status_node rclcpp sensor_msgs nav_msgs)

add_executable(rgbd_tcp_receiver_node src/rgbd_tcp_receiver_node.cpp)
target_compile_features(rgbd_tcp_receiver_node PUBLIC cxx_std_17)
target_include_directories(rgbd_tcp_receiver_node PUBLIC include)
ament_target_dependencies(rgbd_tcp_receiver_node rclcpp sensor_msgs)

add_executable(mpu6050_node src/mpu6050_node.cpp)
target_compile_features(mpu6050_node PUBLIC cxx_std_17)
ament_target_dependencies(mpu6050_node rclcpp sensor_msgs)

add_executable(valid_visual_odom_node src/valid_visual_odom_node.cpp)
target_compile_features(valid_visual_odom_node PUBLIC cxx_std_17)
ament_target_dependencies(valid_visual_odom_node rclcpp nav_msgs tf2 tf2_geometry_msgs)

add_executable(stable_odometry_node src/stable_odometry_node.cpp)
target_compile_features(stable_odometry_node PUBLIC cxx_std_17)
ament_target_dependencies(stable_odometry_node
  rclcpp nav_msgs geometry_msgs tf2 tf2_ros tf2_geometry_msgs)

add_executable(backpack_detector_node src/backpack_detector_node.cpp)
target_compile_features(backpack_detector_node PUBLIC cxx_std_17)
target_include_directories(backpack_detector_node PRIVATE ${OpenCV_INCLUDE_DIRS})
ament_target_dependencies(backpack_detector_node rclcpp sensor_msgs std_msgs)
target_link_libraries(backpack_detector_node ${OpenCV_LIBRARIES})

add_executable(backpack_path_planner_node src/backpack_path_planner_node.cpp)
target_compile_features(backpack_path_planner_node PUBLIC cxx_std_17)
target_include_directories(backpack_path_planner_node PRIVATE ${OpenCV_INCLUDE_DIRS})
ament_target_dependencies(backpack_path_planner_node
  rclcpp sensor_msgs nav_msgs std_msgs geometry_msgs tf2 tf2_ros tf2_geometry_msgs)
target_link_libraries(backpack_path_planner_node ${OpenCV_LIBRARIES})

add_executable(backpack_direction_node src/backpack_direction_node.cpp)
target_compile_features(backpack_direction_node PUBLIC cxx_std_17)
ament_target_dependencies(backpack_direction_node rclcpp nav_msgs std_msgs tf2 tf2_ros tf2_geometry_msgs)

install(TARGETS mapping_status_node rgbd_tcp_receiver_node mpu6050_node backpack_detector_node
  backpack_path_planner_node backpack_direction_node valid_visual_odom_node stable_odometry_node
  DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../../../companion/
  DESTINATION share/${PROJECT_NAME}/companion
  FILES_MATCHING PATTERN "*.py" PATTERN "tests" EXCLUDE)

ament_package()
