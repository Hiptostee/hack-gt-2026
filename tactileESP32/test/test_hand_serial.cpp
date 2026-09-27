#include <cstdio>
#include <cstdlib>

#include "../esp32/tactile_hand/hand.cpp"

namespace {
int failures = 0;
#define CHECK(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "%d: %s\n", __LINE__, #condition); ++failures; } } while (0)

void feed(uint8_t seq, uint8_t flags) {
  Serial.incoming.push_back(tactile::kMagic);
  Serial.incoming.push_back(seq);
  Serial.incoming.push_back(flags);
}
}

int main() {
  fake_ms = 100;
  WiFi = FakeWiFi{};
  Serial = FakeSerial{};
  hand_setup();
  CHECK(!wifi_up && !udp_ready);
  Serial.incoming.push_back(0);  // Skip bytes before the frame marker.
  Serial.incoming.push_back(tactile::kMagic);
  hand_loop();  // The frame can arrive across several loop iterations.
  CHECK(!receiving);
  Serial.incoming.push_back(10);
  Serial.incoming.push_back(tactile::kFront);
  hand_loop();
  CHECK(receiving && sweeping && last_seq == 10);
  CHECK(stats.accepted == 1);

  feed(10, tactile::kLeft);  // Duplicate must not extend motion.
  hand_loop();
  CHECK(stats.stale == 1 && last_seq == 10);
  feed(11, tactile::kRight);
  hand_loop();
  CHECK(current_flags == tactile::kRight);
  CHECK(sweeping == bool(TACTILE_HAND_RIGHT));

  feed(12, tactile::kLeft | tactile::kRight);  // Invalid frame.
  feed(13, 0);
  hand_loop();
  CHECK(stats.malformed == 1 && last_seq == 13 && !sweeping);

  feed(14, tactile::kFront);
  hand_loop();
  fake_ms = last_packet_ms + tactile::kFailsafeTimeoutMs;
  hand_loop();
  CHECK(!receiving && !sweeping && current_flags == 0);
  feed(0, tactile::kFront);  // New counter after the failsafe.
  hand_loop();
  CHECK(receiving && sweeping && last_seq == 0);
  return failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
