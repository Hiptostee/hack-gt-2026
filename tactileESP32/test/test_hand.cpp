#include <cstdio>
#include <cstdlib>

// Compile the production receiver with Arduino doubles, including its private
// state so time and network failures can be injected deterministically.
#include "../esp32/tactile_hand/hand.cpp"

namespace {
int failures = 0;
#define CHECK(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "%d: %s\n", __LINE__, #condition); ++failures; } } while (0)

void reset_hand() {
  fake_ms = 100;
  pwm_writes.clear();
  WiFi.connected = true;
  udp = WiFiUDP{};
  wifi_up = false;
  wifi_attempt_ms = 0;
  receiving = false;
  last_seq = 0;
  last_packet_ms = 0;
  current_flags = 0;
  sweeping = false;
  released = false;
  last_servo_frame_ms = 0;
  stats = Stats{};
  hand_setup();
  hand_loop();
}

void packet(uint8_t seq, uint8_t flags) {
  udp.queued.push_back({tactile::kMagic, seq, flags});
}

void test_malformed_recovery() {
  reset_hand();
  udp.queued.push_back({tactile::kMagic, 0, tactile::kFront, 0});
  packet(1, tactile::kFront);
  hand_loop();
  CHECK(stats.malformed == 1);
  CHECK(stats.accepted == 1);
  CHECK(sweeping);
  CHECK(last_seq == 1);
  CHECK(udp.available() == 0);

  udp.short_read = true;
  packet(2, tactile::kLeft);
  hand_loop();
  CHECK(stats.malformed == 2);
  CHECK(last_seq == 1);
}

void test_failsafe_and_sequence_reset() {
  reset_hand();
  packet(100, tactile::kFront);
  hand_loop();
  const uint32_t arrival = last_packet_ms;
  fake_ms = arrival + 499;
  packet(100, 0);  // duplicates must not refresh the timer
  hand_loop();
  CHECK(sweeping);
  CHECK(last_packet_ms == arrival);
  fake_ms = arrival + 500;
  packet(0, tactile::kLeft);  // sender restart exactly at the timeout
  hand_loop();
  CHECK(last_seq == 0);
  CHECK(current_flags == tactile::kLeft);
  CHECK(sweeping == !TACTILE_HAND_RIGHT);
  fake_ms = last_packet_ms + 500;
  hand_loop();
  CHECK(!receiving && !sweeping && current_flags == 0);
  fake_ms += 500;
  hand_loop();
  CHECK(released && pwm_writes.back() == 0);
}

void test_motion_and_wrap() {
  reset_hand();
  packet(254, tactile::kFront);
  hand_loop();
  fake_ms += 250;
  hand_loop();
  const auto peak = pwm_writes.back();
  CHECK(peak > 6000 && peak < 7000);  // ~150 degrees on default calibration
  packet(255, tactile::kLeft);
  packet(0, tactile::kRight);
  packet(255, tactile::kFront);  // reordered, ignore
  hand_loop();
  CHECK(last_seq == 0 && current_flags == tactile::kRight);
  CHECK(sweeping == bool(TACTILE_HAND_RIGHT));

  reset_hand();
  fake_ms = UINT32_MAX - 250;
  packet(100, tactile::kFront);
  hand_loop();
  const auto arrival = last_packet_ms;
  fake_ms = arrival + 499;
  hand_loop();
  CHECK(receiving);
  fake_ms = arrival + 500;
  hand_loop();
  CHECK(!receiving && !sweeping);
}

void test_busy_socket_and_reconnect() {
  reset_hand();
  for (int i = 0; i < 100; ++i) packet(static_cast<uint8_t>(i), tactile::kFront);
  fake_ms += 20;
  pwm_writes.clear();
  hand_loop();
  CHECK(!udp.queued.empty());  // bounded drain leaves time for a servo update
  CHECK(!pwm_writes.empty());
  hand_loop();
  CHECK(udp.queued.empty() && last_seq == 99);

  WiFi.connected = false;
  hand_loop();
  CHECK(!receiving && !sweeping);
  WiFi.connected = true;
  udp.bind_failures = 1;
  hand_loop();
  const int calls = udp.bind_calls;
  fake_ms += 1000;
  hand_loop();
  CHECK(udp.bind_calls == calls + 1);
  packet(0, tactile::kFront);
  hand_loop();
  CHECK(receiving && sweeping && last_seq == 0);
}
}  // namespace

int main() {
  test_malformed_recovery();
  test_failsafe_and_sequence_reset();
  test_motion_and_wrap();
  test_busy_socket_and_reconnect();
  return failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
