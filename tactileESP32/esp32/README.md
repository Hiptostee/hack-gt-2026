# ESP32 firmware (not started)

The firmware for the two hands goes in this folder. The requirements are in
[../SPEC.md](../SPEC.md), section 5. The ones that are easy to miss:

- **Turn Wi-Fi power saving off.** Use `WiFi.setSleep(false)`, or `esp_wifi_set_ps(WIFI_PS_NONE)`
  in ESP-IDF, right after connecting. It is on by default and adds 100–300 ms of latency.
- **Use a static IP:** left `10.42.0.2`, right `10.42.0.3`, gateway `10.42.0.1`, mask `255.255.255.0`.
- **Include the shared protocol header:** add `-I../protocol` and use
  `tactile_protocol.h` for decoding, seq checks and timing constants, so both sides agree.
- **Failsafe:** if no packet arrives for 300 ms, return the servo to neutral. After
  a failsafe, accept any seq.
- **Power the SG90 from its own 5 V supply** (common ground) with a bulk capacitor
  (e.g. 470 µF). Servo current spikes on the ESP32's supply cause brownout resets,
  and a reset looks like a Wi-Fi dropout.
