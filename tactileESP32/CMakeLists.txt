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

foreach(side IN ITEMS 0 1)
  add_executable(test_hand_${side} test/test_hand.cpp)
  target_include_directories(test_hand_${side} PRIVATE test/arduino protocol)
  target_compile_definitions(test_hand_${side} PRIVATE TACTILE_HAND_RIGHT=${side})
  target_compile_options(test_hand_${side} PRIVATE -Wall -Wextra)
  add_test(NAME test_hand_${side} COMMAND test_hand_${side})
  add_executable(test_hand_serial_${side} test/test_hand_serial.cpp)
  target_include_directories(test_hand_serial_${side} PRIVATE test/arduino protocol)
  target_compile_definitions(test_hand_serial_${side} PRIVATE TACTILE_HAND_RIGHT=${side} TACTILE_USB_ONLY=1)
  target_compile_options(test_hand_serial_${side} PRIVATE -Wall -Wextra)
  add_test(NAME test_hand_serial_${side} COMMAND test_hand_serial_${side})
endforeach()
add_executable(test_hand_calibrated test/test_hand.cpp)
target_include_directories(test_hand_calibrated PRIVATE test/arduino protocol)
target_compile_definitions(test_hand_calibrated PRIVATE TACTILE_HAND_RIGHT=0 TACTILE_REST_DEG=140)
add_test(NAME test_hand_calibrated COMMAND test_hand_calibrated)
set_tests_properties(test_hand_calibrated PROPERTIES TIMEOUT 10)
set_tests_properties(test_tactile test_hand_0 test_hand_1 test_hand_serial_0
  test_hand_serial_1 PROPERTIES TIMEOUT 10)

find_package(Python3 QUIET COMPONENTS Interpreter)
if(Python3_Interpreter_FOUND)
  add_test(NAME test_tools COMMAND ${Python3_EXECUTABLE}
    ${CMAKE_CURRENT_SOURCE_DIR}/test/test_tools.py
    $<TARGET_FILE:tactile_send> $<TARGET_FILE:tactile_listen>)
  set_tests_properties(test_tools PROPERTIES TIMEOUT 30)
endif()
