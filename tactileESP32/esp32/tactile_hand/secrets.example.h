// Copy to secrets.h (gitignored) and enter the GTother Wi-Fi key supplied by GT.
// Register each board's Wi-Fi MAC (printed at boot) as required by GT.
// The ESP32 uses DHCP. Give its logged IP to the Pi/laptop sender; no Pi IP
// belongs here. TACTILE_AP_* are legacy names, not an ESP32 hotspot setting.

#pragma once

#define TACTILE_AP_SSID "GTother"
#define TACTILE_AP_PSK "replace-with-GTother-wifi-key"
