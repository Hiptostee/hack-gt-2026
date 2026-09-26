#pragma once

#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>

#include "tactile_protocol.h"

namespace tactile {

inline bool parse_port(const std::string& text, uint16_t* port) {
  if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos) return false;
  errno = 0;
  char* end = nullptr;
  const unsigned long value = std::strtoul(text.c_str(), &end, 10);
  if (errno || *end || value == 0 || value > 65535) return false;
  *port = static_cast<uint16_t>(value);
  return true;
}

inline bool parse_rate(const std::string& text, double* hz) {
  errno = 0;
  char* end = nullptr;
  const double value = std::strtod(text.c_str(), &end);
  // A period >= 500 ms would trigger the receiver's failsafe between ticks.
  if (end == text.c_str() || *end || errno || !std::isfinite(value) ||
      value <= 1000.0 / kFailsafeTimeoutMs || value > 1000.0) return false;
  *hz = value;
  return true;
}

}  // namespace tactile
