// Stand-in for one ESP32 hand. Receives packets the way the firmware will
// (decode, seq check, failsafe) and reports loss and timing, so the Wi-Fi
// link can be measured before the firmware exists.
//
//   tactile_listen                 listen on UDP 4210
//   tactile_listen --port 4211     second hand on the same machine
//   tactile_listen --verbose       print every packet
//
// A laptop joined to the Pi hotspot with a static IP of 10.42.0.2 or
// 10.42.0.3 can run this in place of a hand.

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "tactile_protocol.h"

namespace {

using Clock = std::chrono::steady_clock;

std::atomic<bool> g_interrupted{false};
const Clock::time_point g_start = Clock::now();

void on_signal(int) { g_interrupted = true; }

double since_start(Clock::time_point time) {
  return std::chrono::duration<double>(time - g_start).count();
}

double millis(Clock::duration duration) {
  return std::chrono::duration<double, std::milli>(duration).count();
}

std::string flags_to_string(uint8_t flags) {
  if (flags == 0) return "neutral";
  std::string text;
  const char* names[] = {"front", "back", "left", "right"};
  for (int bit = 0; bit < 4; ++bit) {
    if (!(flags & (1u << bit))) continue;
    if (!text.empty()) text += '+';
    text += names[bit];
  }
  return text;
}

struct Window {
  uint64_t accepted = 0;
  uint64_t lost = 0;       // seq gaps between accepted packets
  uint64_t stale = 0;      // duplicated or reordered, dropped
  uint64_t malformed = 0;
  double max_gap_ms = 0;
  double total_gap_ms = 0;
};

}  // namespace

int main(int argc, char** argv) {
  uint16_t port = tactile::kUdpPort;
  bool verbose = false;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--port" && i + 1 < argc) {
      port = static_cast<uint16_t>(std::atoi(argv[++i]));
    } else if (arg == "--verbose") {
      verbose = true;
    } else {
      std::fprintf(stderr, "usage: %s [--port N] [--verbose]\n", argv[0]);
      return 2;
    }
  }

  struct sigaction action {};
  action.sa_handler = on_signal;
  sigaction(SIGINT, &action, nullptr);
  sigaction(SIGTERM, &action, nullptr);

  const int sock = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (sock < 0) {
    std::perror("socket");
    return 1;
  }
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(port);
  address.sin_addr.s_addr = htonl(INADDR_ANY);
  if (::bind(sock, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
    std::perror("bind");
    return 1;
  }
  std::fprintf(stderr, "listening on UDP %u (failsafe %u ms)\n", port, tactile::kFailsafeTimeoutMs);

  const auto failsafe = std::chrono::milliseconds(tactile::kFailsafeTimeoutMs);
  Clock::time_point last_packet{};
  Clock::time_point next_report = Clock::now() + std::chrono::seconds(5);
  bool have_packet = false;
  bool in_failsafe = true;
  uint8_t last_seq = 0;
  uint8_t state = 0;
  Window window;

  while (!g_interrupted) {
    const Clock::time_point now = Clock::now();

    if (have_packet && !in_failsafe && now - last_packet >= failsafe) {
      in_failsafe = true;
      state = 0;
      std::fprintf(stderr, "[%9.3f] FAILSAFE: no packet for %.0f ms -> neutral\n", since_start(now),
                   millis(now - last_packet));
    }
    if (now >= next_report) {
      const uint64_t expected = window.accepted + window.lost;
      std::fprintf(stderr,
                   "[%9.3f] 5 s: %llu ok, %llu lost (%.1f%%), %llu stale, %llu malformed, "
                   "gap avg %.1f ms max %.1f ms\n",
                   since_start(now), static_cast<unsigned long long>(window.accepted),
                   static_cast<unsigned long long>(window.lost),
                   expected ? 100.0 * window.lost / expected : 0.0,
                   static_cast<unsigned long long>(window.stale),
                   static_cast<unsigned long long>(window.malformed),
                   window.accepted > 1 ? window.total_gap_ms / (window.accepted - 1) : 0.0,
                   window.max_gap_ms);
      window = Window{};
      next_report = now + std::chrono::seconds(5);
    }

    pollfd descriptor{sock, POLLIN, 0};
    const int ready = ::poll(&descriptor, 1, 10);
    if (ready < 0 && errno != EINTR) {
      std::perror("poll");
      return 1;
    }
    if (ready <= 0) continue;

    uint8_t buffer[64];
    sockaddr_in from{};
    socklen_t from_len = sizeof(from);
    const ssize_t length = ::recvfrom(sock, buffer, sizeof(buffer), 0,
                                      reinterpret_cast<sockaddr*>(&from), &from_len);
    if (length < 0) continue;
    const Clock::time_point arrival = Clock::now();

    uint8_t seq = 0;
    uint8_t flags = 0;
    if (!tactile::decode_packet(buffer, static_cast<size_t>(length), &seq, &flags)) {
      ++window.malformed;
      continue;
    }

    // After a failsafe the sender may have restarted, so any seq is accepted.
    if (have_packet && !in_failsafe && !tactile::seq_is_newer(seq, last_seq)) {
      ++window.stale;
      continue;
    }

    if (have_packet && !in_failsafe) {
      window.lost += static_cast<uint8_t>(seq - last_seq) - 1;
      const double gap = millis(arrival - last_packet);
      window.total_gap_ms += gap;
      window.max_gap_ms = std::max(window.max_gap_ms, gap);
    }
    ++window.accepted;

    if (verbose) {
      char sender[INET_ADDRSTRLEN] = {};
      inet_ntop(AF_INET, &from.sin_addr, sender, sizeof(sender));
      std::fprintf(stderr, "[%9.3f] %s seq=%3u %s\n", since_start(arrival), sender, seq,
                   flags_to_string(flags).c_str());
    }
    if (in_failsafe || flags != state) {
      std::fprintf(stderr, "[%9.3f] state -> %s (seq=%u)\n", since_start(arrival),
                   flags_to_string(flags).c_str(), seq);
    }

    state = flags;
    last_seq = seq;
    last_packet = arrival;
    have_packet = true;
    in_failsafe = false;
  }

  ::close(sock);
  return 0;
}
