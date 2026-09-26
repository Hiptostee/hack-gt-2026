cmake_minimum_required(VERSION 3.16)
project(realsense_host_streamer LANGUAGES CXX)

find_package(realsense2 REQUIRED)

add_executable(realsense_host_streamer main.cpp)
target_compile_features(realsense_host_streamer PRIVATE cxx_std_17)
target_compile_options(realsense_host_streamer PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(realsense_host_streamer PRIVATE
  ../ros_ws/src/realsense_mapper/include)
target_link_libraries(realsense_host_streamer PRIVATE realsense2::realsense2)
