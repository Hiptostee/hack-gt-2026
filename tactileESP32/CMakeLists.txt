cmake_minimum_required(VERSION 3.16)
project(tactile_esp32 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Threads REQUIRED)

add_library(tactile_link
  src/direction.cpp
  src/udp_link.cpp
)
target_include_directories(tactile_link PUBLIC include protocol)
target_link_libraries(tactile_link PUBLIC Threads::Threads)
target_compile_options(tactile_link PRIVATE -Wall -Wextra -Wpedantic)

add_executable(tactile_send tools/tactile_send.cpp)
target_link_libraries(tactile_send PRIVATE tactile_link)
target_compile_options(tactile_send PRIVATE -Wall -Wextra -Wpedantic)

add_executable(tactile_listen tools/tactile_listen.cpp)
target_include_directories(tactile_listen PRIVATE protocol)
target_compile_options(tactile_listen PRIVATE -Wall -Wextra -Wpedantic)

enable_testing()
add_executable(test_tactile test/test_tactile.cpp)
target_link_libraries(test_tactile PRIVATE tactile_link)
add_test(NAME test_tactile COMMAND test_tactile)
