#pragma once
#include "Arduino.h"
#define WL_CONNECTED 3
#define WIFI_STA 1
struct IPAddress {
  IPAddress(int, int, int, int) {}
  std::string toString() const { return "10.42.0.2"; }
};
struct FakeWiFi {
  bool connected = true;
  uint32_t begin_delay_ms = 0;
  int status() const { return connected ? WL_CONNECTED : 0; }
  void disconnect() {}
  void config(IPAddress, IPAddress, IPAddress) {}
  void begin(const char*, const char*) { delay(begin_delay_ms); }
  void setSleep(bool) {}
  void persistent(bool) {}
  void mode(int) {}
  void setAutoReconnect(bool) {}
  IPAddress localIP() const { return IPAddress(10, 42, 0, 2); }
  int channel() const { return 6; }
  int RSSI() const { return -50; }
};
inline FakeWiFi WiFi;
