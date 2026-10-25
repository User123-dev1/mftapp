# Reference Hardware and Network Setup

> These are recommendations for the prototype. Final component choices are open.

## Receiver (gateway + I/O)

| Item | Prototype choice | Why |
|---|---|---|
| Compute | Raspberry Pi CM5 (or CM4) on an industrial carrier board, 24 VDC input | Linux, Ethernet + WiFi on board, long-term availability programme |
| Ethernet | On-board Gigabit (plant / PLC side) | EtherNet/IP to Logix |
| WiFi | On-board 2.4/5 GHz, external antenna (RP-SMA) through the enclosure | Panels; the enclosure is often metal |
| I/O slots | SPI bus to one small MCU per slot module (DI8, DO8, AI4, AO2) | Isolation per module, hot-swap, simple backplane |
| Watchdog | External hardware watchdog that cuts output power if the service stops toggling it | Fail-safe independent of software |
| Mounting | DIN rail | Panel installs |

## Panel

| Item | Prototype choice |
|---|---|
| Compute | Raspberry Pi CM5 + 7"/10.1" industrial capacitive touch display (sunlight readable optional) |
| Power | 24 VDC, or battery for a handheld (pendant) variant |
| Radio | Same WiFi as the receiver |

## Network configuration

### Receiver as access point (default)

`/etc/hostapd/hostapd.conf` (WPA3-SAE with WPA2 fallback, 5 GHz preferred):

```ini
interface=wlan0
ssid=WHMI-RX1
country_code=US
hw_mode=a
channel=36
ieee80211n=1
ieee80211ac=1
wpa=2
wpa_key_mgmt=SAE WPA-PSK
rsn_pairwise=CCMP
ieee80211w=1          # protected management frames (deauth-attack resistance)
sae_require_mfp=1
wpa_passphrase=<at least 16 random characters>
ignore_broadcast_ssid=0
max_num_sta=8
```

### Isolation between WiFi and the plant network

The receiver must **never route** between `wlan0` and `eth0`. Panels reach only the
WHMI-Link service. `/etc/nftables.conf`:

```
table inet whmi {
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state established,related accept
    iifname "wlan0" tcp dport 47800 accept          # WHMI-Link from panels
    iifname "wlan0" udp dport { 67, 68 } accept      # DHCP for panels
    iifname "eth0"  tcp dport { 47800, 22 } accept   # Studio download, maintenance SSH
  }
  chain forward {
    type filter hook forward priority 0; policy drop; # no WLAN <-> plant routing, ever
  }
}
```

Also set `net.ipv4.ip_forward=0`.

### Running the receiver as a service

`/etc/systemd/system/whmi-receiver.service`:

```ini
[Unit]
Description=WHMI Receiver
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/whmi-receiver --config /var/lib/whmi/receiver.json
Restart=always
RestartSec=1
User=whmi
WatchdogSec=5

[Install]
WantedBy=multi-user.target
```

## Rockwell PLC side

* **Tag client mode** (implemented): no PLC changes required. Set the PLC connection to the controller's
  IP address and path (`1,0` = backplane, slot 0; for CompactLogix the path can be empty or `1,0`).
* Recommended practice: write HMI commands to dedicated `HMI_*` tags and let the PLC program decide what to do.
  Set `heartbeat_tag` on the PLC connection (e.g. `HMI_Heartbeat`, DINT). In the PLC, use a TON timer that
  is reset whenever the value changes. When it times out (e.g. 3 × heartbeat_ms), treat that as HMI loss and stop motion.
* E-stops and guard interlocks must be hardwired into the safety circuit / GuardLogix safety task.
