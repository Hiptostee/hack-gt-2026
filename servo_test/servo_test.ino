// Standalone ESP32 Arduino IDE servo test; runs once on every power-up/reset.
// Neutral -> left -> right -> neutral, then holds neutral indefinitely.
// Board: ESP32 Dev Module. Signal: GPIO 18. No computer or Serial input needed.
// Use a servo-rated supply and connect servo ground to ESP32 ground.
#include <Arduino.h>

constexpr int SERVO_PIN = 18;
constexpr int NEUTRAL_DEG = 90;
constexpr int LEFT_DEG = 60;
constexpr int RIGHT_DEG = 120;
constexpr uint32_t PULSE_0_US = 500;
constexpr uint32_t PULSE_180_US = 2400;
constexpr uint8_t PWM_BITS = 16;
constexpr uint32_t HOLD_MS = 2000;
int currentDegrees = NEUTRAL_DEG;

static_assert(LEFT_DEG >= 0 && LEFT_DEG < NEUTRAL_DEG &&
              NEUTRAL_DEG < RIGHT_DEG && RIGHT_DEG <= 180,
              "Positions must be ordered within 0..180 degrees");

void moveTo(int degrees) {
  const uint32_t us = PULSE_0_US + (PULSE_180_US - PULSE_0_US) * degrees / 180;
  const uint32_t duty = uint64_t(us) * ((1UL << PWM_BITS) - 1) / 20000;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(SERVO_PIN, duty);
#else
  ledcWrite(0, duty);
#endif
  currentDegrees = degrees;
}

void moveSlowlyTo(int target) {
  while (currentDegrees != target) {
    moveTo(currentDegrees + (target > currentDegrees ? 1 : -1));
    delay(20);
  }
}

void setup() {
  Serial.begin(115200);  // Optional progress output; never waits for a computer.
  delay(500);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  const bool ready = ledcAttach(SERVO_PIN, 50, PWM_BITS);
#else
  const bool ready = ledcSetup(0, 50, PWM_BITS) > 0;
  if (ready) ledcAttachPin(SERVO_PIN, 0);
#endif
  if (!ready) {
    Serial.println("PWM setup failed. Check board selection and reset.");
    return;
  }
  Serial.println("Neutral");
  moveTo(NEUTRAL_DEG);
  delay(HOLD_MS);
  Serial.println("Left");
  moveSlowlyTo(LEFT_DEG);
  delay(HOLD_MS);
  Serial.println("Right");
  moveSlowlyTo(RIGHT_DEG);
  delay(HOLD_MS);
  Serial.println("Returning to neutral; holding until power-off/reset.");
  moveSlowlyTo(NEUTRAL_DEG);
}

void loop() {
  // Hardware PWM keeps holding neutral. Do not repeat the sequence or detach.
  delay(1000);
}
