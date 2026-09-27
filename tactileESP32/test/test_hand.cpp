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
  WiFi = FakeWiFi{};
  udp = WiFiUDP{};
  wifi_up = false;
  wifi_attempt_ms = 0;
  receiving = false;
  have_packet = false;
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

void test_calibrated_travel_limits() {
  reset_hand();
  CHECK(pulse_for_degrees(-90) == pulse_for_degrees(TACTILE_MIN_DEG));
  CHECK(pulse_for_degrees(270) == pulse_for_degrees(TACTILE_MAX_DEG));
  sweeping = true;
  sweep_start_ms = fake_ms;
  const uint32_t started = fake_ms;
  const auto duty_for = [](float degrees) {
    return uint64_t(pulse_for_degrees(degrees)) * ((1u << 16) - 1) / 20000;
  };
  for (uint32_t step = 20; step <= TACTILE_SWEEP_PERIOD_MS; step += 20) {
    fake_ms = started + step;
    update_servo(fake_ms);
    CHECK(pwm_writes.back() >= duty_for(TACTILE_MIN_DEG));
    CHECK(pwm_writes.back() <= duty_for(TACTILE_MAX_DEG));
  }
#if TACTILE_REST_DEG == 140 && TACTILE_MIN_DEG == 30 && TACTILE_MAX_DEG == 150
  // A shifted neutral must reduce the whole sweep, not flatten it at an end.
  last_servo_frame_ms = started;
  update_servo(started + TACTILE_SWEEP_PERIOD_MS / 4);
  CHECK(pwm_writes.back() == duty_for(150));
  update_servo(started + 3 * TACTILE_SWEEP_PERIOD_MS / 4);
  CHECK(pwm_writes.back() == duty_for(130));
#endif
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

void test_slow_wifi_startup() {
  reset_hand();
  pwm_writes.clear();
  WiFi.begin_delay_ms = 600;  // longer than the initial rest/release interval
  hand_setup();
  CHECK(!pwm_writes.empty());  // rest must already be driven during Wi-Fi setup
  if (!pwm_writes.empty()) CHECK(pwm_writes.front() > 0);
  hand_loop();
  CHECK(released && pwm_writes.back() == 0);
}

void test_outage_gap_reporting() {
  reset_hand();
  packet(100, tactile::kFront);
  hand_loop();
  const uint32_t arrival = last_packet_ms;
  fake_ms = arrival + 500;
  hand_loop();
  CHECK(stats.max_gap_ms == 500);
  // A report resets window statistics, but must not hide ongoing silence.
  stats = Stats{};
  fake_ms = arrival + 1200;
  hand_loop();
  CHECK(stats.max_gap_ms == 1200);
  packet(0, tactile::kFront);
  hand_loop();
  CHECK(receiving && last_seq == 0 && stats.lost == 0);
  CHECK(stats.max_gap_ms >= 1200);
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
  test_calibrated_travel_limits();
  test_slow_wifi_startup();
  test_malformed_recovery();
  test_outage_gap_reporting();
  test_failsafe_and_sequence_reset();
  test_motion_and_wrap();
  test_busy_socket_and_reconnect();
  return failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
