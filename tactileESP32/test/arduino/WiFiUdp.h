#pragma once
#include "Arduino.h"
#include <deque>

struct WiFiUDP {
  std::deque<std::vector<uint8_t>> queued;
  std::deque<uint8_t> unread;
  bool bound = false;
  int bind_failures = 0;
  int bind_calls = 0;
  bool short_read = false;
  int begin(uint16_t) {
    ++bind_calls;
    if (bind_failures > 0) { --bind_failures; return 0; }
    bound = true;
    return 1;
  }
  void stop() { bound = false; queued.clear(); unread.clear(); }
  int parsePacket() {
    // Match Arduino-ESP32: unread bytes prevent parsing the next datagram.
    if (!bound || !unread.empty() || queued.empty()) return 0;
    const auto packet = queued.front();
    queued.pop_front();
    unread.assign(packet.begin(), packet.end());
    return static_cast<int>(unread.size());
  }
  int available() const { return static_cast<int>(unread.size()); }
  int read(uint8_t* out, size_t size) {
    if (short_read) { short_read = false; unread.clear(); return 0; }
    size_t count = 0;
    while (count < size && !unread.empty()) {
      out[count++] = unread.front();
      unread.pop_front();
    }
    return static_cast<int>(count);
  }
};
