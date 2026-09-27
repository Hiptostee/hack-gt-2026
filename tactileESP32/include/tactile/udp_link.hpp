// Sends the current direction to every ESP32 hand over unicast UDP.
//
// A background thread resends the current state every period (default 50 ms,
// i.e. 20 Hz) so a dropped datagram is replaced by the next one. A state
// change is sent immediately instead of waiting for the next tick.

#pragma once

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "tactile/direction.hpp"

namespace tactile {

struct Target {
  std::string name;  // used in logs, e.g. "left"
  std::string ip;    // dotted IPv4
  uint16_t port;
};

// Legacy Pi-hotspot addresses only. On GTother (or any DHCP network), set
// LinkConfig::targets to the actual IPv4 addresses printed by the hands.
std::vector<Target> default_targets();

struct LinkConfig {
  std::vector<Target> targets = default_targets();
  std::chrono::milliseconds period{50};
  // Neutral packets sent on stop() so servos release at once instead of
  // waiting for their failsafe timeout.
  int neutral_packets_on_stop = 3;
  // Emit an Event for every packet sent, not only for state changes.
  bool trace = false;
};

struct Event {
  enum class Type {
    kStarted,
    kStateChanged,  // first packet carrying a new state went out
    kPacketSent,    // only when LinkConfig::trace is set
    kSendFailed,    // a target started failing; errno in `error`
    kSendRecovered, // a failing target accepted a packet again
    kStopped,
  };

  Type type;
  std::chrono::steady_clock::time_point time;
  uint8_t seq = 0;
  uint8_t flags = 0;
  std::string target;  // send events only
  int error = 0;
};

struct TargetStats {
  std::string name;
  uint64_t sent = 0;
  uint64_t failed = 0;
  int last_error = 0;
};

class TactileLink {
 public:
  // Events are delivered on the sender thread; the handler must be quick and
  // thread-safe with respect to the caller's own code. It must not throw or
  // call start()/stop()/destroy the link (those join this thread).
  using EventHandler = std::function<void(const Event&)>;

  // Throws std::invalid_argument for bad targets, a period outside 1..499 ms,
  // or a negative neutral burst count, and
  // std::system_error if the socket cannot be created.
  explicit TactileLink(LinkConfig config, EventHandler on_event = nullptr);
  ~TactileLink();

  TactileLink(const TactileLink&) = delete;
  TactileLink& operator=(const TactileLink&) = delete;

  void start();
  // Sends the neutral burst, then joins the sender thread. Safe to call twice.
  void stop();

  // Only validated directions can be sent.
  void set(const ValidDirection& direction);
  // Validates first. An invalid direction is rejected and leaves the current
  // state unchanged.
  bool try_set(const Direction& direction, Rejection* why = nullptr);

  ValidDirection current() const;
  std::vector<TargetStats> stats() const;

 private:
  struct Endpoint;

  void run();
  void send_packet(uint8_t flags);
  void emit(Event event) const;

  LinkConfig config_;
  EventHandler on_event_;
  int socket_ = -1;
  std::vector<Endpoint> endpoints_;

  mutable std::mutex mutex_;
  std::condition_variable wake_;
  ValidDirection state_ = ValidDirection::neutral();
  bool changed_ = false;
  bool stopping_ = false;
  std::thread thread_;
  // Serializes lifecycle calls, including the join outside mutex_.
  std::mutex lifecycle_mutex_;

  // Sender thread only, apart from stats() which takes stats_mutex_.
  uint8_t seq_ = 0;
  mutable std::mutex stats_mutex_;
};

}  // namespace tactile
