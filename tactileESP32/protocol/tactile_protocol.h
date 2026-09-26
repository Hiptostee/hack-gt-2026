// Wire protocol shared by the Raspberry Pi 5 sender and the ESP32 receivers.
//
// This header is the single source of truth for the packet layout. It is
// deliberately C++11, header-only and free of the standard library so the
// Arduino-ESP32 toolchain can include it unchanged. See ../SPEC.md.
//
// Packet (3 bytes, sent by unicast UDP to every hand):
//
//   byte 0  magic   0xA5
//   byte 1  seq     sender counter, +1 per packet, wraps 255 -> 0
//   byte 2  flags   bit0 front, bit1 left, bit2 right,
//                   bits 3..7 reserved and must be 0
//
// At most one flag is set: one direction at a time. All flags clear means
// "no direction" and every servo stops.

#pragma once

#include <stddef.h>
#include <stdint.h>

namespace tactile {

const uint16_t kUdpPort = 4210;

const uint8_t kMagic = 0xA5;
const size_t kPacketSize = 3;

const uint8_t kFront = 1u << 0;
const uint8_t kLeft = 1u << 1;
const uint8_t kRight = 1u << 2;
const uint8_t kDirectionMask = kFront | kLeft | kRight;

// The sender repeats the current state at this period, so a lost packet is
// replaced by the next one.
const uint32_t kSendPeriodMs = 50;

// A receiver that hears nothing for this long stops its servo. 10 missed
// packets: long enough to ride out a Wi-Fi hiccup without stuttering.
const uint32_t kFailsafeTimeoutMs = 500;

inline bool flags_valid(uint8_t flags) {
  if (flags & static_cast<uint8_t>(~kDirectionMask)) return false;
  return (flags & (flags - 1)) == 0;  // zero or one bit set
}

// Callers must pass flags that satisfy flags_valid(). The Pi side guarantees
// this through tactile::validate() before anything reaches the encoder.
inline void encode_packet(uint8_t seq, uint8_t flags, uint8_t out[kPacketSize]) {
  out[0] = kMagic;
  out[1] = seq;
  out[2] = flags;
}

// Returns false for anything that is not a well-formed packet: wrong length,
// wrong magic, reserved bits set or more than one direction.
inline bool decode_packet(const uint8_t* data, size_t len, uint8_t* seq, uint8_t* flags) {
  if (data == 0 || len != kPacketSize) return false;
  if (data[0] != kMagic) return false;
  if (!flags_valid(data[2])) return false;
  *seq = data[1];
  *flags = data[2];
  return true;
}

// True when `seq` comes after `last` in 8-bit serial-number order, i.e. it is
// at most 127 packets ahead. Receivers drop packets that are not newer, which
// discards late, reordered or duplicated datagrams. A receiver must accept any
// seq once kFailsafeTimeoutMs has passed without a packet, because the sender
// may have restarted its counter.
inline bool seq_is_newer(uint8_t seq, uint8_t last) {
  return static_cast<int8_t>(static_cast<uint8_t>(seq - last)) > 0;
}

}  // namespace tactile
