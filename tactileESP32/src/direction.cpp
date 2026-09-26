#include "tactile/direction.hpp"

#include "tactile_protocol.h"

namespace tactile {

const char* to_string(Rejection rejection) {
  switch (rejection) {
    case Rejection::kMultipleDirections:
      return "only one direction at a time";
  }
  return "unknown rejection";
}

Direction ValidDirection::direction() const {
  Direction direction;
  direction.front = flags_ & kFront;
  direction.left = flags_ & kLeft;
  direction.right = flags_ & kRight;
  return direction;
}

std::optional<ValidDirection> validate(const Direction& direction, Rejection* why) {
  if (direction.front + direction.left + direction.right > 1) {
    if (why) *why = Rejection::kMultipleDirections;
    return std::nullopt;
  }

  uint8_t flags = 0;
  if (direction.front) flags |= kFront;
  if (direction.left) flags |= kLeft;
  if (direction.right) flags |= kRight;
  return ValidDirection(flags);
}

std::string flags_to_string(uint8_t flags) {
  if ((flags & kDirectionMask) == 0) return "neutral";
  std::string text;
  auto append = [&text](const char* name) {
    if (!text.empty()) text += '+';
    text += name;
  };
  if (flags & kFront) append("front");
  if (flags & kLeft) append("left");
  if (flags & kRight) append("right");
  return text;
}

std::string to_string(const Direction& direction) {
  uint8_t flags = 0;
  if (direction.front) flags |= kFront;
  if (direction.left) flags |= kLeft;
  if (direction.right) flags |= kRight;
  return flags_to_string(flags);
}

}  // namespace tactile
