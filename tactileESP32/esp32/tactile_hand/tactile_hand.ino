// Firmware for one hand: receives direction packets from the Pi 5 over Wi-Fi
// UDP and drives a 360-degree SG90 servo. Logic is in hand.cpp; see
// ../../SPEC.md, section 5.

#include "hand.h"

void setup() { hand_setup(); }

void loop() { hand_loop(); }
