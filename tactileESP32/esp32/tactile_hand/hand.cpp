// One hand: Wi-Fi station on the laptop's network, UDP receiver, servo driver.
//
// Receive rules match tools/tactile_listen.cpp on the Pi side: decode, drop
// packets whose seq is not newer, return the servo to rest after
// kFailsafeTimeoutMs without a packet, and accept any seq after a failsafe.

#include "hand.h"

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <math.h>

#include "config.h"
#include "tactile_protocol.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy secrets.example.h to secrets.h and set your Wi-Fi SSID and password"
#endif

#define LOG(format, ...) Serial.printf("[%9.3f] " format "\n", millis() / 1000.0, ##__VA_ARGS__)

namespace {

const uint8_t kOwnSide = TACTILE_HAND_RIGHT ? tactile::kRight : tactile::kLeft;
const char* const kHandName = TACTILE_HAND_RIGHT ? "right" : "left";

const char* const kHostname = TACTILE_HAND_RIGHT ? "tactile-right" : "tactile-left";

// If auto-reconnect has not brought Wi-Fi back after this long, start over.
const uint32_t kReconnectRetryMs = 10000;
const uint32_t kReportPeriodMs = 5000;
const uint32_t kUdpRetryMs = 1000;
// Bound receive work so a busy socket cannot starve PWM and the failsafe.
const int kMaxPacketsPerLoop = 64;

// 180-degree servo: the pulse width sets the arm angle. While a direction is
// active the arm sweeps back and forth around the rest angle along a sine, so
// it slows down at the turning points instead of jerking. Otherwise it goes
// back to rest, and once there the pulses stop so it does not hum when idle.
const uint32_t kServoHz = 50;
const uint32_t kServoFrameMs = 1000 / kServoHz;
const uint8_t kServoResolutionBits = 16;
const uint32_t kServoPeriodUs = 1000000 / kServoHz;
#if ESP_ARDUINO_VERSION_MAJOR < 3
const uint8_t kServoChannel = 0;
#endif

struct Stats {
  uint32_t accepted = 0;
  uint32_t lost = 0;
  uint32_t stale = 0;
  uint32_t malformed = 0;
  uint32_t max_gap_ms = 0;
};

WiFiUDP udp;
bool wifi_up = false;
uint32_t wifi_attempt_ms = 0;
bool udp_ready = false;
bool mdns_up = false;
uint32_t udp_attempt_ms = 0;

bool receiving = false;  // a packet arrived since the last failsafe
bool have_packet = false;  // retain gap timing across failsafes and reconnects
uint8_t last_seq = 0;
uint32_t last_packet_ms = 0;
uint8_t current_flags = 0;

bool sweeping = false;
uint32_t sweep_start_ms = 0;
uint32_t rest_since_ms = 0;
bool released = false;  // at rest with pulses stopped
uint32_t last_servo_frame_ms = 0;

Stats stats;
uint32_t next_report_ms = 0;

const char* flags_name(uint8_t flags) {
  switch (flags) {
    case 0: return "neutral";
    case tactile::kFront: return "front";
    case tactile::kLeft: return "left";
    case tactile::kRight: return "right";
  }
  return "invalid";
}

// 0 stops the pulses: the servo goes limp where it is.
void servo_pulse(uint32_t pulse_us) {
  const uint32_t duty =
      static_cast<uint64_t>(pulse_us) * ((1u << kServoResolutionBits) - 1) / kServoPeriodUs;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(TACTILE_SERVO_PIN, duty);
#else
  ledcWrite(kServoChannel, duty);
#endif
}

uint32_t pulse_for_degrees(float degrees) {
  degrees = constrain(degrees, 0.0f, 180.0f);
  return TACTILE_PULSE_0_US +
         static_cast<uint32_t>((TACTILE_PULSE_180_US - TACTILE_PULSE_0_US) * degrees / 180.0f);
}

// Called once per 20 ms PWM frame; the servo cannot use updates any faster.
void update_servo(uint32_t now) {
  if (now - last_servo_frame_ms < kServoFrameMs) return;
  last_servo_frame_ms = now;

  if (sweeping) {
    const float phase =
        static_cast<float>((now - sweep_start_ms) % TACTILE_SWEEP_PERIOD_MS) / TACTILE_SWEEP_PERIOD_MS;
    const float degrees = TACTILE_REST_DEG + TACTILE_SWEEP_DEG * sinf(2.0f * PI * phase);
    servo_pulse(pulse_for_degrees(degrees));
  } else if (!released) {
    if (TACTILE_RELEASE_AFTER_MS > 0 && now - rest_since_ms >= TACTILE_RELEASE_AFTER_MS) {
      servo_pulse(0);
      released = true;
    } else {
      servo_pulse(pulse_for_degrees(TACTILE_REST_DEG));
    }
  }
}

void set_sweeping(bool on, uint32_t now) {
  if (on == sweeping) return;
  sweeping = on;
  if (on) {
    sweep_start_ms = now;  // the sine starts at the rest angle, so no jump
    released = false;
  } else {
    rest_since_ms = now;
  }
  LOG("servo -> %s", on ? "sweep" : "rest");
}

// front sweeps both hands. A side direction sweeps only that side's hand;
// the other hand stays at rest.
void apply(uint8_t flags, uint32_t now) {
  current_flags = flags;
  set_sweeping((flags & tactile::kFront) || (flags & kOwnSide), now);
}

void start_wifi(uint32_t now) {
  WiFi.disconnect();
  WiFi.setHostname(kHostname);
  WiFi.begin(TACTILE_AP_SSID, TACTILE_AP_PSK);
  wifi_attempt_ms = now;
}

void maintain_wifi(uint32_t now) {
  const bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifi_up) {
    // Modem sleep adds 100-300 ms of receive latency; keep the radio awake.
    WiFi.setSleep(false);
    mdns_up = MDNS.begin(kHostname);
    if (!mdns_up) LOG("mDNS failed; use the logged IP address on the laptop");
    udp.stop();
    udp_ready = false;
    LOG("wifi up: %s, channel %d, rssi %d dBm", WiFi.localIP().toString().c_str(),
        static_cast<int>(WiFi.channel()), WiFi.RSSI());
  } else if (!up && wifi_up) {
    LOG("wifi lost -> rest");
    receiving = false;
    apply(0, now);
    udp.stop();
    if (mdns_up) MDNS.end();
    mdns_up = false;
    udp_ready = false;
    wifi_attempt_ms = now;
  } else if (!up && now - wifi_attempt_ms >= kReconnectRetryMs) {
    LOG("wifi still down, retrying");
    start_wifi(now);
  }
  if (up && !udp_ready && (!wifi_up || now - udp_attempt_ms >= kUdpRetryMs)) {
    udp_attempt_ms = now;
    udp_ready = udp.begin(tactile::kUdpPort) != 0;
    if (!udp_ready) LOG("UDP bind failed, retrying in 1 s");
  }
  wifi_up = up;
}

void check_failsafe(uint32_t now);

void receive_packets() {
  if (!wifi_up || !udp_ready) return;
  for (int i = 0; i < kMaxPacketsPerLoop; ++i) {
    const int length = udp.parsePacket();
    if (length <= 0) break;
    uint8_t buffer[tactile::kPacketSize] = {};
    const int bytes_read = udp.read(buffer, sizeof(buffer));
    // parsePacket() refuses to advance while this datagram has unread bytes.
    // Drain the remainder even when the packet will be rejected.
    uint8_t discard[64];
    while (udp.available() > 0) udp.read(discard, sizeof(discard));

    const uint32_t now = millis();
    // Expire BEFORE comparing seq: a restarted sender may use any counter.
    check_failsafe(now);

    uint8_t seq = 0;
    uint8_t flags = 0;
    if (bytes_read != static_cast<int>(sizeof(buffer)) ||
        !tactile::decode_packet(buffer, static_cast<size_t>(length), &seq, &flags)) {
      ++stats.malformed;
      continue;
    }
    if (receiving && !tactile::seq_is_newer(seq, last_seq)) {
      ++stats.stale;
      continue;
    }

    if (receiving) {
      stats.lost += static_cast<uint8_t>(seq - last_seq) - 1;
      stats.max_gap_ms = max(stats.max_gap_ms, now - last_packet_ms);
    }
    ++stats.accepted;

    if (!receiving || flags != current_flags) {
      LOG("state -> %s (seq=%u)", flags_name(flags), seq);
    }
    receiving = true;
    have_packet = true;
    last_seq = seq;
    last_packet_ms = now;
    apply(flags, now);
  }
}

void check_failsafe(uint32_t now) {
  if (have_packet) stats.max_gap_ms = max(stats.max_gap_ms, now - last_packet_ms);
  if (!receiving || now - last_packet_ms < tactile::kFailsafeTimeoutMs) return;
  LOG("FAILSAFE: no packet for %lu ms -> rest", static_cast<unsigned long>(now - last_packet_ms));
  receiving = false;
  apply(0, now);
}

void report(uint32_t now) {
  if (static_cast<int32_t>(now - next_report_ms) < 0) return;
  next_report_ms = now + kReportPeriodMs;
  const uint32_t expected = stats.accepted + stats.lost;
  LOG("5 s: %lu ok, %lu lost (%.1f%%), %lu stale, %lu malformed, max gap %lu ms, rssi %d dBm",
      static_cast<unsigned long>(stats.accepted), static_cast<unsigned long>(stats.lost),
      expected ? 100.0 * stats.lost / expected : 0.0, static_cast<unsigned long>(stats.stale),
      static_cast<unsigned long>(stats.malformed), static_cast<unsigned long>(stats.max_gap_ms),
      wifi_up ? WiFi.RSSI() : 0);
  stats = Stats();
}

void update_led(uint32_t now) {
  bool on;
  if (receiving) {
    on = true;
  } else if (wifi_up) {
    on = (now / 500) % 2;
  } else {
    on = (now / 100) % 2;
  }
  digitalWrite(TACTILE_LED_PIN, on ? HIGH : LOW);
}

}  // namespace

void hand_setup() {
  Serial.begin(115200);
  pinMode(TACTILE_LED_PIN, OUTPUT);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(TACTILE_SERVO_PIN, kServoHz, kServoResolutionBits);
#else
  ledcSetup(kServoChannel, kServoHz, kServoResolutionBits);
  ledcAttachPin(TACTILE_SERVO_PIN, kServoChannel);
#endif
  // Drive rest before Wi-Fi setup, which can outlast the release timer.
  servo_pulse(pulse_for_degrees(TACTILE_REST_DEG));
  rest_since_ms = millis();

  LOG("tactile hand: %s, hostname %s.local, servo gpio %d", kHandName, kHostname,
      TACTILE_SERVO_PIN);

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  start_wifi(millis());
  next_report_ms = millis() + kReportPeriodMs;
}

void hand_loop() {
  maintain_wifi(millis());
  check_failsafe(millis());
  receive_packets();
  const uint32_t now = millis();
  check_failsafe(now);
  update_servo(now);
  report(now);
  update_led(now);
  delay(1);
}
