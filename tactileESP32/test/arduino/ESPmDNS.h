#pragma once
struct FakeMDNS {
  bool begin(const char*) { return true; }
  void end() {}
};
inline FakeMDNS MDNS;
