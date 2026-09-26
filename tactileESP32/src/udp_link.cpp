#include "tactile/udp_link.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/ip.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <stdexcept>
#include <system_error>
#include <utility>

#include "tactile_protocol.h"

namespace tactile {

namespace {

Event make_event(Event::Type type, uint8_t seq, uint8_t flags) {
  Event event;
  event.type = type;
  event.time = std::chrono::steady_clock::now();
  event.seq = seq;
  event.flags = flags;
  return event;
}

}  // namespace

struct TactileLink::Endpoint {
  std::string name;
  sockaddr_in address{};
  TargetStats stats;
  bool failing = false;
};

std::vector<Target> default_targets() {
  return {
      {"left", "10.42.0.2", kUdpPort},
      {"right", "10.42.0.3", kUdpPort},
  };
}

TactileLink::TactileLink(LinkConfig config, EventHandler on_event)
    : config_(std::move(config)), on_event_(std::move(on_event)) {
  if (config_.targets.empty()) throw std::invalid_argument("no targets configured");
  if (config_.period.count() <= 0) throw std::invalid_argument("period must be positive");

  for (const Target& target : config_.targets) {
    Endpoint endpoint;
    endpoint.name = target.name;
    endpoint.stats.name = target.name;
    endpoint.address.sin_family = AF_INET;
    endpoint.address.sin_port = htons(target.port);
    if (inet_pton(AF_INET, target.ip.c_str(), &endpoint.address.sin_addr) != 1) {
      throw std::invalid_argument("invalid IPv4 address for " + target.name + ": " + target.ip);
    }
    endpoints_.push_back(endpoint);
  }

  socket_ = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (socket_ < 0) throw std::system_error(errno, std::generic_category(), "socket");

  // Best effort: ask for the Wi-Fi voice access category (WMM AC_VO) so our
  // tiny packets are queued ahead of bulk traffic such as VNC or SSH.
#ifdef SO_PRIORITY
  int priority = 6;
  setsockopt(socket_, SOL_SOCKET, SO_PRIORITY, &priority, sizeof(priority));
#endif
  int tos = 0xB8;  // DSCP EF
  setsockopt(socket_, IPPROTO_IP, IP_TOS, &tos, sizeof(tos));
}

TactileLink::~TactileLink() {
  stop();
  if (socket_ >= 0) ::close(socket_);
}

void TactileLink::start() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (thread_.joinable()) return;
  stopping_ = false;
  changed_ = true;  // send the current state right away
  thread_ = std::thread(&TactileLink::run, this);
}

void TactileLink::stop() {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!thread_.joinable()) return;
    stopping_ = true;
  }
  wake_.notify_all();
  thread_.join();
}

void TactileLink::set(const ValidDirection& direction) {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (direction == state_) return;
    state_ = direction;
    changed_ = true;
  }
  wake_.notify_all();
}

bool TactileLink::try_set(const Direction& direction, Rejection* why) {
  std::optional<ValidDirection> valid = validate(direction, why);
  if (!valid) return false;
  set(*valid);
  return true;
}

ValidDirection TactileLink::current() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return state_;
}

std::vector<TargetStats> TactileLink::stats() const {
  std::lock_guard<std::mutex> lock(stats_mutex_);
  std::vector<TargetStats> result;
  for (const Endpoint& endpoint : endpoints_) result.push_back(endpoint.stats);
  return result;
}

void TactileLink::run() {
  using Clock = std::chrono::steady_clock;

  emit(make_event(Event::Type::kStarted, seq_, 0));

  std::unique_lock<std::mutex> lock(mutex_);
  Clock::time_point next = Clock::now();
  uint8_t last_sent_flags = 0;
  bool sent_any = false;

  while (true) {
    const bool woken = wake_.wait_until(lock, next, [this] { return stopping_ || changed_; });
    if (stopping_) break;

    const uint8_t flags = state_.flags();
    changed_ = false;
    lock.unlock();

    send_packet(flags);
    if (!sent_any || flags != last_sent_flags) {
      emit(make_event(Event::Type::kStateChanged, static_cast<uint8_t>(seq_ - 1), flags));
      last_sent_flags = flags;
      sent_any = true;
    }

    lock.lock();
    // Ticks stay on a fixed grid so the rate does not drift. A change, or
    // falling behind, restarts the grid from the moment of sending.
    const Clock::time_point now = Clock::now();
    next += config_.period;
    if (woken || next <= now) next = now + config_.period;
  }
  lock.unlock();

  for (int i = 0; i < config_.neutral_packets_on_stop; ++i) send_packet(0);

  emit(make_event(Event::Type::kStopped, static_cast<uint8_t>(seq_ - 1), 0));
}

void TactileLink::send_packet(uint8_t flags) {
  uint8_t packet[kPacketSize];
  const uint8_t seq = seq_++;
  encode_packet(seq, flags, packet);

  for (Endpoint& endpoint : endpoints_) {
    const ssize_t written =
        ::sendto(socket_, packet, sizeof(packet), MSG_DONTWAIT,
                 reinterpret_cast<const sockaddr*>(&endpoint.address), sizeof(endpoint.address));
    const int error = written == static_cast<ssize_t>(sizeof(packet)) ? 0 : (written < 0 ? errno : EIO);

    bool report_failure = false;
    bool report_recovery = false;
    {
      std::lock_guard<std::mutex> lock(stats_mutex_);
      if (error == 0) {
        ++endpoint.stats.sent;
        report_recovery = endpoint.failing;
        endpoint.failing = false;
      } else {
        ++endpoint.stats.failed;
        endpoint.stats.last_error = error;
        report_failure = !endpoint.failing;
        endpoint.failing = true;
      }
    }

    // Report transitions only, so an unplugged hand does not flood the log.
    if (report_failure || report_recovery) {
      Event event = make_event(
          report_failure ? Event::Type::kSendFailed : Event::Type::kSendRecovered, seq, flags);
      event.target = endpoint.name;
      event.error = error;
      emit(event);
    }
    if (config_.trace && error == 0) {
      Event event = make_event(Event::Type::kPacketSent, seq, flags);
      event.target = endpoint.name;
      emit(event);
    }
  }
}

void TactileLink::emit(Event event) const {
  if (on_event_) on_event_(event);
}

}  // namespace tactile
