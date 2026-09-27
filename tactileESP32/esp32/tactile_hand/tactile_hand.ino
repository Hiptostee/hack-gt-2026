// Firmware for one hand: receives direction packets over UDP or USB serial
// and drives a 180-degree SG90 servo. Logic is in hand.cpp; see
// ../../SPEC.md, section 5.

#include "hand.h"

void setup() { hand_setup(); }

void loop() { hand_loop(); }
