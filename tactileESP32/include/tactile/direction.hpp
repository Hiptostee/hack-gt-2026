// Direction commands and the validation step that must happen before a
// command can be packed into a packet.

#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace tactile {

// What callers ask for. Any combination can be expressed here; only
// validate() decides whether it can be sent.
struct Direction {
  bool front = false;
  bool back = false;
  bool left = false;
  bool right = false;
};

enum class Rejection {
  kFrontAndBack,
  kLeftAndRight,
};

const char* to_string(Rejection rejection);

// A Direction that passed validate(). TactileLink only accepts this type, so
// an invalid combination can never be packed or sent.
class ValidDirection {
 public:
  static ValidDirection neutral() { return ValidDirection(0); }

  uint8_t flags() const { return flags_; }
  Direction direction() const;
  bool is_neutral() const { return flags_ == 0; }

  bool operator==(const ValidDirection& other) const { return flags_ == other.flags_; }
  bool operator!=(const ValidDirection& other) const { return flags_ != other.flags_; }

 private:
  explicit ValidDirection(uint8_t flags) : flags_(flags) {}

  uint8_t flags_;

  friend std::optional<ValidDirection> validate(const Direction& direction, Rejection* why);
};

// Rejects front+back and left+right. On rejection returns nullopt and, when
// `why` is non-null, stores the reason.
std::optional<ValidDirection> validate(const Direction& direction, Rejection* why = nullptr);

// "neutral", "front", "front+left", ...
std::string to_string(const Direction& direction);
std::string flags_to_string(uint8_t flags);

}  // namespace tactile
