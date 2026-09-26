// Unit tests for validation and the wire protocol, plus a loopback test of
// TactileLink. Plain asserts, no framework: build and run with ctest.

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "tactile/udp_link.hpp"
#include "tactile_protocol.h"

namespace {

int g_failures = 0;

#define CHECK(condition)                                                    \
  do {                                                                      \
    if (!(condition)) {                                                     \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, \
                   #condition);                                             \
      ++g_failures;                                                         \
    }                                                                       \
  } while (0)

using tactile::Direction;

Direction make(bool front, bool left, bool right) {
  Direction direction;
  direction.front = front;
  direction.left = left;
  direction.right = right;
  return direction;
}

void test_validate_all_combinations() {
  int accepted = 0;
  for (int bits = 0; bits < 8; ++bits) {
    const Direction direction = make(bits & 1, bits & 2, bits & 4);
    tactile::Rejection why;
    const auto valid = tactile::validate(direction, &why);
    const bool single_or_none = (bits & (bits - 1)) == 0;
    CHECK(valid.has_value() == single_or_none);
    CHECK(tactile::flags_valid(static_cast<uint8_t>(bits)) == single_or_none);
    if (valid) {
      ++accepted;
      CHECK(valid->flags() == bits);
    } else {
      CHECK(why == tactile::Rejection::kMultipleDirections);
    }
  }
  // Neutral plus the three single directions.
  CHECK(accepted == 4);
}

void test_packet_round_trip() {
  uint8_t packet[tactile::kPacketSize];
  tactile::encode_packet(200, tactile::kLeft, packet);
  CHECK(packet[0] == 0xA5);
  CHECK(packet[1] == 200);
  CHECK(packet[2] == 0x02);

  uint8_t seq = 0;
  uint8_t flags = 0;
  CHECK(tactile::decode_packet(packet, sizeof(packet), &seq, &flags));
  CHECK(seq == 200);
  CHECK(flags == tactile::kLeft);
}

void test_decode_rejects_bad_packets() {
  uint8_t seq = 0;
  uint8_t flags = 0;
  const uint8_t wrong_magic[] = {0x5A, 1, tactile::kFront};
  const uint8_t reserved_bit[] = {0xA5, 1, 0x08};
  const uint8_t high_bit[] = {0xA5, 1, 0x80};
  const uint8_t left_and_right[] = {0xA5, 1, tactile::kLeft | tactile::kRight};
  const uint8_t front_and_left[] = {0xA5, 1, tactile::kFront | tactile::kLeft};
  const uint8_t too_long[] = {0xA5, 1, tactile::kFront, 0};
  CHECK(!tactile::decode_packet(wrong_magic, sizeof(wrong_magic), &seq, &flags));
  CHECK(!tactile::decode_packet(reserved_bit, sizeof(reserved_bit), &seq, &flags));
  CHECK(!tactile::decode_packet(high_bit, sizeof(high_bit), &seq, &flags));
  CHECK(!tactile::decode_packet(left_and_right, sizeof(left_and_right), &seq, &flags));
  CHECK(!tactile::decode_packet(front_and_left, sizeof(front_and_left), &seq, &flags));
  CHECK(!tactile::decode_packet(too_long, sizeof(too_long), &seq, &flags));
  CHECK(!tactile::decode_packet(too_long, 2, &seq, &flags));
}

void test_seq_ordering() {
  CHECK(tactile::seq_is_newer(1, 0));
  CHECK(tactile::seq_is_newer(0, 255));   // wrap-around
  CHECK(tactile::seq_is_newer(10, 250));  // wrap-around with a gap
  CHECK(!tactile::seq_is_newer(5, 5));    // duplicate
  CHECK(!tactile::seq_is_newer(4, 5));    // late
  CHECK(!tactile::seq_is_newer(250, 10));
}

// Receives packets on a loopback socket for up to `timeout`.
std::vector<std::pair<uint8_t, std::chrono::steady_clock::time_point>> receive(
    int sock, std::chrono::milliseconds timeout) {
  std::vector<std::pair<uint8_t, std::chrono::steady_clock::time_point>> packets;
  const auto deadline = std::chrono::steady_clock::now() + timeout;
  while (true) {
    const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
        deadline - std::chrono::steady_clock::now());
    if (remaining.count() <= 0) break;
    pollfd descriptor{sock, POLLIN, 0};
    if (::poll(&descriptor, 1, static_cast<int>(remaining.count())) <= 0) continue;
    uint8_t buffer[16];
    const ssize_t length = ::recv(sock, buffer, sizeof(buffer), 0);
    uint8_t seq = 0;
    uint8_t flags = 0;
    if (length > 0 && tactile::decode_packet(buffer, static_cast<size_t>(length), &seq, &flags)) {
      packets.emplace_back(flags, std::chrono::steady_clock::now());
    }
  }
  return packets;
}

void test_link_over_loopback() {
  const int sock = ::socket(AF_INET, SOCK_DGRAM, 0);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  CHECK(::bind(sock, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
  socklen_t length = sizeof(address);
  ::getsockname(sock, reinterpret_cast<sockaddr*>(&address), &length);

  tactile::LinkConfig config;
  config.targets = {{"loopback", "127.0.0.1", ntohs(address.sin_port)}};
  tactile::TactileLink link(config);
  link.start();

  // Repeats neutral at ~20 Hz: expect about 10 packets in 500 ms.
  auto packets = receive(sock, std::chrono::milliseconds(500));
  CHECK(packets.size() >= 8 && packets.size() <= 12);
  for (const auto& packet : packets) CHECK(packet.first == 0);

  // A change is sent at once, not on the next tick.
  const auto before = std::chrono::steady_clock::now();
  CHECK(link.try_set(make(true, false, false)));
  packets = receive(sock, std::chrono::milliseconds(30));
  CHECK(!packets.empty());
  if (!packets.empty()) {
    CHECK(packets.front().first == tactile::kFront);
    CHECK(packets.front().second - before < std::chrono::milliseconds(20));
  }

  // A rejected command leaves the state alone.
  CHECK(!link.try_set(make(true, true, false)));
  CHECK(link.current().flags() == tactile::kFront);

  // stop() releases the servo with a neutral burst.
  link.stop();
  packets = receive(sock, std::chrono::milliseconds(100));
  CHECK(!packets.empty());
  if (!packets.empty()) CHECK(packets.back().first == 0);

  const auto stats = link.stats();
  CHECK(stats.size() == 1 && stats[0].failed == 0 && stats[0].sent > 0);
  ::close(sock);
}

}  // namespace

int main() {
  test_validate_all_combinations();
  test_packet_round_trip();
  test_decode_rejects_bad_packets();
  test_seq_ordering();
  test_link_over_loopback();

  if (g_failures) {
    std::fprintf(stderr, "%d check(s) failed\n", g_failures);
    return EXIT_FAILURE;
  }
  std::printf("all tests passed\n");
  return EXIT_SUCCESS;
}
