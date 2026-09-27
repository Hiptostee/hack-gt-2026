#pragma once
#include "Arduino.h"
#define WL_CONNECTED 3
#define WIFI_OFF 0
#define WIFI_STA 1
struct IPAddress {
  IPAddress(int, int, int, int) {}
  std::string toString() const { return "10.42.0.2"; }
};
struct FakeWiFi {
  bool connected = true;
  uint32_t begin_delay_ms = 0;
  std::vector<int> modes;
  int status() const { return connected ? WL_CONNECTED : 0; }
  void disconnect() {}
  void config(IPAddress, IPAddress, IPAddress) {}
  void setHostname(const char*) {}
  void begin(const char*, const char*) { delay(begin_delay_ms); }
  void setSleep(bool) {}
  void persistent(bool) {}
  bool mode(int value) { modes.push_back(value); return true; }
  void setAutoReconnect(bool) {}
  IPAddress localIP() const { return IPAddress(10, 42, 0, 2); }
  std::string macAddress() const { return "AA:BB:CC:DD:EE:FF"; }
  int channel() const { return 6; }
  int RSSI() const { return -50; }
};
inline FakeWiFi WiFi;
