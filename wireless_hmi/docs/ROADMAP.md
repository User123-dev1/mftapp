# WHMI Roadmap

Each phase ends with something that runs and is covered by automated tests.

## Phase 1: Core platform ✅ (this commit)

- [x] Architecture and WHMI-Link v1 protocol specification
- [x] Project model: Logix-compatible data types, tags (memory / local I/O / PLC / system),
      I/O module catalog, PLC connections, screens and widgets
- [x] Validation and build: tag ids, canonical JSON, SHA-256 project identity
- [x] WHMI-Link: mutual HMAC auth, AES-256-GCM, replay protection, heartbeat/watchdog
- [x] Receiver: tag DB, 10 ms I/O scan, simulated I/O, analog scaling, retentive memory,
      system diagnostics tags
- [x] Rockwell Logix driver (EtherNet/IP explicit messaging, by tag name) with reconnect/backoff
- [x] Link-loss safety: momentary tags, fail-safe values, quality propagation, PLC heartbeat tag
- [x] Project download (engineer role) with staged, validated, atomic activation
- [x] Panel runtime engine: sync, subscriptions, writes, momentary press/release, reconnect, offline cache
- [x] `whmi` CLI: new / validate / build / tags / keygen / receiver init+run / download / read / write / monitor / info
- [x] Test suite (91 tests): codec, crypto attacks, end-to-end receiver ↔ panel, CLI against a real process

## Phase 2: WHMI Studio (C#)

**Needs:** `dot.net`, `builds.dotnet.microsoft.com`, `api.nuget.org` allowed in the dev environment.

- `Whmi.Core` (.NET 8 class library): C# port of the project model and builder. Must produce
  byte-identical compiled projects (shared golden test vectors with the Python suite).
- `Whmi.Link`: C# WHMI-Link client (System.Security.Cryptography `AesGcm`, `HMACSHA256`, `HKDF`)
- `Whmi.Studio` (WPF on Windows): project explorer, tag database grid (import/export CSV,
  import tags from an L5X export or directly from the online controller), I/O slot configurator,
  PLC connection editor with "Test connection", and a drag-and-drop screen designer with a
  property grid and live validation
- Download / upload / compare-with-receiver, online monitoring of tags
- Simulation mode: runs the runtime against an in-process simulated receiver

## Phase 3: Panel UI

- Screen renderer for all widget types; touch-optimised; numeric keypad; screen navigation
- Per-screen subscriptions (subscribe only to what is visible)
- Status bar: link quality (RSSI), receiver, PLC state; full-screen "COMMUNICATION LOST" overlay
- Security: operator login with access levels per widget; audit log of writes (who/when/old/new)
- Alarms: digital/analog alarm definitions in the project, alarm banner, history, acknowledgement
- Trends: receiver-side ring buffer, historical trend widget

## Phase 4: Rockwell I/O adapter mode + more drivers

- Receiver as an EtherNet/IP **adapter** (Class 1 implicit I/O, Generic Ethernet Module, then EDS file):
  assemblies for local I/O + HMI data at the configured RPI
- Logix: read UDTs/arrays as structured tags; browse controller tags from Studio
- Micro800 / SLC / MicroLogix (PCCC) and Modbus TCP drivers

## Phase 5: Hardware productisation

- Receiver I/O backplane driver (SPI to slot microcontrollers), module hot-swap detection,
  wire-break detection on 4-20 mA
- Hardware watchdog: outputs drop to fail-safe if the receiver service stops
- Secure boot, signed updates, firmware update from Studio
- Optional MCU port of the receiver (ESP32-S3/STM32 + W5500), using the same protocol spec
- EMC / environmental testing; FCC/CE radio certification of the chosen WiFi module
