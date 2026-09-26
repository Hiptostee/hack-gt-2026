#pragma once

#include <cstdint>

namespace realsense_mapper
{

constexpr std::uint32_t kRgbdMagic = 0x31444252U;  // "RBD1" on little-endian hosts
constexpr std::uint32_t kRgbdVersion = 1U;

#pragma pack(push, 1)
struct RgbdFrameHeader
{
  std::uint32_t magic;
  std::uint32_t version;
  std::uint32_t width;
  std::uint32_t height;
  std::uint32_t rgb_bytes;
  std::uint32_t depth_bytes;
  std::uint32_t sequence;
  std::uint64_t timestamp_ns;
  float fx;
  float fy;
  float ppx;
  float ppy;
  float depth_scale;
  float distortion[5];
};
#pragma pack(pop)

static_assert(sizeof(RgbdFrameHeader) == 76, "Unexpected RGB-D protocol layout");

}  // namespace realsense_mapper
