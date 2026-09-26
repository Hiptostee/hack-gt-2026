// Manual and demo sender for testing the hands without the ROS stack.
//
//   tactile_send                         interactive, default hand addresses
//   tactile_send --demo                  cycle through every direction
//   tactile_send --left 127.0.0.1:4210 --right 127.0.0.1:4211 --trace
//
// Interactive commands, one per line:
//   f, l or r       set direction (one at a time; "fl" etc. are rejected)
//   n or 0          neutral
//   s               print stats
//   q               quit

#include <signal.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "tactile/udp_link.hpp"
#include "tactile_protocol.h"

namespace {

using Clock = std::chrono::steady_clock;

std::atomic<bool> g_interrupted{false};
const Clock::time_point g_start = Clock::now();
std::mutex g_print_mutex;

void on_signal(int) { g_interrupted = true; }

double seconds_since_start(Clock::time_point time) {
  return std::chrono::duration<double>(time - g_start).count();
}

void log_line(Clock::time_point time, const std::string& message) {
  std::lock_guard<std::mutex> lock(g_print_mutex);
  std::fprintf(stderr, "[%9.3f] %s\n", seconds_since_start(time), message.c_str());
}

void print_event(const tactile::Event& event) {
  using Type = tactile::Event::Type;
  const std::string seq = "seq=" + std::to_string(event.seq);
  switch (event.type) {
    case Type::kStarted:
      log_line(event.time, "link started");
      break;
    case Type::kStateChanged:
      log_line(event.time, "state -> " + tactile::flags_to_string(event.flags) + " (" + seq + ")");
      break;
    case Type::kPacketSent:
      log_line(event.time, "sent " + tactile::flags_to_string(event.flags) + " to " + event.target +
                               " (" + seq + ")");
      break;
    case Type::kSendFailed:
      log_line(event.time, "send to " + event.target + " failing: " + std::strerror(event.error));
      break;
    case Type::kSendRecovered:
      log_line(event.time, "send to " + event.target + " recovered (" + seq + ")");
      break;
    case Type::kStopped:
      log_line(event.time, "link stopped, neutral sent (" + seq + ")");
      break;
  }
}

bool parse_target(const std::string& name, const std::string& text, tactile::Target* target) {
  target->name = name;
  target->port = tactile::kUdpPort;
  const std::string::size_type colon = text.find(':');
  target->ip = text.substr(0, colon);
  if (colon == std::string::npos) return true;
  const int port = std::atoi(text.c_str() + colon + 1);
  if (port <= 0 || port > 65535) return false;
  target->port = static_cast<uint16_t>(port);
  return true;
}

// Returns false for characters other than f, l, r.
bool parse_direction(const std::string& text, tactile::Direction* direction) {
  *direction = tactile::Direction{};
  for (char c : text) {
    switch (c) {
      case 'f': direction->front = true; break;
      case 'l': direction->left = true; break;
      case 'r': direction->right = true; break;
      default: return false;
    }
  }
  return true;
}

void print_stats(const tactile::TactileLink& link) {
  for (const tactile::TargetStats& stats : link.stats()) {
    std::string line = stats.name + ": sent " + std::to_string(stats.sent) + ", failed " +
                       std::to_string(stats.failed);
    if (stats.last_error) line += std::string(" (last error: ") + std::strerror(stats.last_error) + ")";
    log_line(Clock::now(), line);
  }
}

void usage(const char* program) {
  std::fprintf(stderr,
               "usage: %s [--left IP[:PORT]] [--right IP[:PORT]] [--rate HZ] [--demo] [--trace]\n"
               "  defaults: left 10.42.0.2:%u, right 10.42.0.3:%u, rate 20 Hz\n",
               program, tactile::kUdpPort, tactile::kUdpPort);
}

void run_demo(tactile::TactileLink& link) {
  const char* steps[] = {"f", "", "l", "", "r", ""};
  while (!g_interrupted) {
    for (const char* step : steps) {
      tactile::Direction direction;
      parse_direction(step, &direction);
      link.try_set(direction);
      for (int i = 0; i < 10 && !g_interrupted; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      if (g_interrupted) return;
    }
  }
}

void run_interactive(tactile::TactileLink& link) {
  std::string line;
  while (!g_interrupted && std::getline(std::cin, line)) {
    if (line.empty()) continue;
    if (line == "q") break;
    if (line == "s") {
      print_stats(link);
      continue;
    }
    if (line == "n" || line == "0") line.clear();

    tactile::Direction direction;
    if (!parse_direction(line, &direction)) {
      log_line(Clock::now(), "unknown command '" + line + "' (use f/l/r, n, s, q)");
      continue;
    }
    tactile::Rejection why;
    if (!link.try_set(direction, &why)) {
      log_line(Clock::now(), "rejected " + tactile::to_string(direction) + ": " + tactile::to_string(why));
    }
  }
}

}  // namespace

int main(int argc, char** argv) {
  tactile::LinkConfig config;
  bool demo = false;

  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    const bool has_value = i + 1 < argc;
    if ((arg == "--left" || arg == "--right") && has_value) {
      const std::string name = arg.substr(2);
      tactile::Target target;
      if (!parse_target(name, argv[++i], &target)) {
        usage(argv[0]);
        return 2;
      }
      config.targets[name == "left" ? 0 : 1] = target;
    } else if (arg == "--rate" && has_value) {
      const double hz = std::atof(argv[++i]);
      if (hz <= 0 || hz > 1000) {
        usage(argv[0]);
        return 2;
      }
      config.period = std::chrono::milliseconds(static_cast<int>(1000.0 / hz));
    } else if (arg == "--demo") {
      demo = true;
    } else if (arg == "--trace") {
      config.trace = true;
    } else {
      usage(argv[0]);
      return 2;
    }
  }

  // No SA_RESTART, so Ctrl-C also interrupts a blocking read of stdin.
  struct sigaction action {};
  action.sa_handler = on_signal;
  sigaction(SIGINT, &action, nullptr);
  sigaction(SIGTERM, &action, nullptr);

  try {
    tactile::TactileLink link(config, print_event);
    for (const tactile::Target& target : config.targets) {
      log_line(Clock::now(), target.name + " -> " + target.ip + ":" + std::to_string(target.port));
    }
    link.start();
    if (demo) {
      run_demo(link);
    } else {
      run_interactive(link);
    }
    link.stop();
    print_stats(link);
  } catch (const std::exception& error) {
    std::fprintf(stderr, "error: %s\n", error.what());
    return 1;
  }
  return 0;
}
