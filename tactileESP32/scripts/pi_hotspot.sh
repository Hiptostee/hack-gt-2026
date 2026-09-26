#!/usr/bin/env bash
# Manages the Pi 5 Wi-Fi hotspot that both ESP32 hands join (SPEC.md, section 2).
#
#   ./scripts/pi_hotspot.sh scan     list nearby 2.4 GHz networks to pick a channel
#                                    (run before `up`: wlan0 cannot scan while it is an AP)
#   TACTILE_AP_PSK=secret123 sudo -E ./scripts/pi_hotspot.sh up
#   sudo ./scripts/pi_hotspot.sh down
#   ./scripts/pi_hotspot.sh status
#
# Optional environment: TACTILE_AP_SSID (TactileNet), TACTILE_AP_CHANNEL (6),
# TACTILE_AP_IFACE (wlan0).

set -euo pipefail

CONNECTION="tactile-ap"
SSID="${TACTILE_AP_SSID:-TactileNet}"
CHANNEL="${TACTILE_AP_CHANNEL:-6}"
IFACE="${TACTILE_AP_IFACE:-wlan0}"

case "${1:-}" in
  scan)
    nmcli device wifi rescan ifname "$IFACE" 2>/dev/null || true
    nmcli --fields CHAN,SIGNAL,SSID device wifi list ifname "$IFACE" | sort -n -k1
    ;;

  up)
    PSK="${TACTILE_AP_PSK:?set TACTILE_AP_PSK to a WPA2 password of 8-63 characters}"
    # Validate before replacing a working connection.
    if [ "${#PSK}" -lt 8 ] || [ "${#PSK}" -gt 63 ]; then
      echo "TACTILE_AP_PSK must contain 8-63 characters" >&2
      exit 2
    fi
    case "$CHANNEL" in
      1|6|11) ;;
      *) echo "TACTILE_AP_CHANNEL must be 1, 6 or 11" >&2; exit 2 ;;
    esac
    if nmcli -t -f NAME connection show | grep -qx "$CONNECTION"; then
      nmcli connection delete "$CONNECTION" >/dev/null
    fi
    # band bg: the ESP32-WROOM-32 is 2.4 GHz only.
    # ipv4.method shared: NetworkManager serves DHCP on 10.42.0.10-254, so the
    #   hands' static 10.42.0.2 and 10.42.0.3 can never be handed to a laptop.
    # proto rsn + ccmp + pmf disable: plain WPA2-AES, which the Pi's brcmfmac
    #   AP mode and the ESP32 both handle reliably.
    # powersave 2: disable Wi-Fi power saving on the Pi side.
    nmcli connection add type wifi ifname "$IFACE" con-name "$CONNECTION" \
      autoconnect yes ssid "$SSID" \
      802-11-wireless.mode ap \
      802-11-wireless.band bg \
      802-11-wireless.channel "$CHANNEL" \
      802-11-wireless.powersave 2 \
      ipv4.method shared \
      ipv4.addresses 10.42.0.1/24 \
      ipv6.method disabled \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "$PSK" \
      wifi-sec.proto rsn \
      wifi-sec.pairwise ccmp \
      wifi-sec.group ccmp \
      wifi-sec.pmf disable >/dev/null
    nmcli connection up "$CONNECTION"
    echo "hotspot '$SSID' up on channel $CHANNEL, Pi at 10.42.0.1"
    ;;

  down)
    nmcli connection down "$CONNECTION"
    ;;

  status)
    nmcli -f GENERAL.STATE,IP4.ADDRESS connection show "$CONNECTION" || true
    echo "hands reachable:"
    for ip in 10.42.0.2 10.42.0.3; do
      if ping -c 1 -W 1 "$ip" >/dev/null 2>&1; then echo "  $ip up"; else echo "  $ip DOWN"; fi
    done
    ;;

  *)
    sed -n '2,12p' "$0"
    exit 2
    ;;
esac
