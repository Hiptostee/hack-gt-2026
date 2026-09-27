// Host doubles for exercising the actual hand.cpp control loop without a board.
#pragma once
#include <algorithm>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>

#define ESP_ARDUINO_VERSION_MAJOR 3
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define PI 3.14159265358979323846
using std::max;
template <class T> T constrain(T value, T low, T high) {
  return std::max(low, std::min(value, high));
}
inline uint32_t fake_ms = 100;
inline std::vector<uint32_t> pwm_writes;
inline uint32_t millis() { return fake_ms; }
inline void delay(uint32_t ms) { fake_ms += ms; }
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline bool ledcAttach(int, int, int) { return true; }
inline bool ledcWrite(int, uint32_t duty) { pwm_writes.push_back(duty); return true; }
struct FakeSerial {
  std::deque<uint8_t> incoming;
  void begin(int) {}
  int available() const { return static_cast<int>(incoming.size()); }
  int read() {
    if (incoming.empty()) return -1;
    const int byte = incoming.front();
    incoming.pop_front();
    return byte;
  }
  template <class... Args> void printf(const char*, Args...) {}
};
inline FakeSerial Serial;
