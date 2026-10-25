# WHMI: Wireless Industrial HMI System Architecture

## 1. System overview

WHMI has three products that ship together:

| Product | What it is | Runs on | Language |
|---|---|---|---|
| **WHMI Studio** | Engineering software. Used to create projects, tags, I/O configuration, PLC connections and HMI screens, and to download projects. | Windows engineering PC | C# (.NET 8, WPF/Avalonia) |
| **WHMI Panel** | Wireless touchscreen operator panel. Runs the HMI runtime. | Linux panel (Raspberry Pi CM5-class industrial panel) | Python runtime engine; C#/Avalonia UI planned |
| **WHMI Receiver** | Wireless I/O receiver and gateway. Has local I/O module slots, a WiFi radio for the panels and an Ethernet port for PLCs (Rockwell ControlLogix/CompactLogix, etc.). | Linux SoM with Ethernet + WiFi (CM5-class); MCU firmware port later | Python service |

```
                         Plant Ethernet (EtherNet/IP)
   ┌──────────────┐   ┌──────────────────────────────┐
   │ Rockwell PLC │◄──┤ ETH0                         │
   │ (Logix)      │   │        WHMI RECEIVER         │
   └──────────────┘   │  ┌────────┐  ┌────────────┐  │
                      │  │Tag DB  │◄─┤ I/O scanner │◄─┼── I/O slots (DI/DO/AI/AO)
   ┌──────────────┐   │  └───┬────┘  └────────────┘  │
   │ WHMI Studio  │──►│      │  Link server (TCP)    │
   │ (download)   │   │      ▼                       │
   └──────────────┘   │  WLAN0 (AP or client)        │
                      └──────────────┬───────────────┘
                                     │ WiFi, WPA2/WPA3 + WHMI-Link (AES-256-GCM)
                      ┌──────────────┴───────────────┐
                      │  WHMI PANEL(S)  (1..N)       │
                      │  runtime engine + screens    │
                      └──────────────────────────────┘
```

### Key design decisions

1. **The receiver is the source of truth.** It holds the compiled project and the
   live tag database. Panels hold no configuration of their own. When a panel
   connects it compares project hashes and pulls the project if the hash differs. Several panels can
   attach to one receiver, and replacing a panel needs no reconfiguration.
2. **Every tag resolves to exactly one source** on the receiver:
   * `memory`: an internal tag held on the receiver (retentive optional)
   * `io`: a channel on a local I/O module (`Slot1.DI.3`)
   * `plc`: a tag inside a PLC reached over Ethernet (`Line1_Motor.Run`)
   * `system`: a diagnostic tag the receiver itself produces (`$Link.Clients`)
3. **The wireless link is untrusted.** WPA2/WPA3 protects the radio layer. On top of that, every
   WHMI-Link session is mutually authenticated with per-client keys and encrypted
   with AES-256-GCM, with strict sequence numbers for replay protection. See [PROTOCOL.md](PROTOCOL.md).
4. **Loss of WiFi must leave the machine in a safe state:**
   * **Heartbeat/watchdog:** both ends send heartbeats. When the watchdog expires, the session is torn down.
   * **Momentary tags:** for push-buttons (jog, start). The panel re-asserts the
     value while the button is held. The receiver releases it automatically
     when the refreshes stop or the link drops. A button cannot get stuck on.
   * **Fail-safe values:** each output tag can declare the value it goes to when no
     controlling session remains.
   * **Quality:** every value carries a quality code. The panel shows stale or bad data
     as stale (hatched/greyed) and never as a valid live value.
   * **PLC heartbeat:** the receiver increments a DINT tag in the PLC while a panel is connected.
     The PLC program detects HMI loss itself and owns the machine's reaction.
5. **Safety functions are out of scope by design.** E-stops, guard interlocks and
   any SIL/PL-rated function must be hardwired according to IEC 60204-1 / ISO 13850 /
   ISO 13849. The WHMI may *display* the state of safety devices but must never *implement* a safety function.
   Studio will refuse to build a project that binds a tag named or flagged as safety to a write widget.

## 2. Software components (this repository)

```
wireless_hmi/
├── docs/                    Architecture, protocol spec, roadmap, hardware notes
├── src/whmi/
│   ├── common/              Shared project model (tags, data types, I/O, PLC, screens)
│   ├── link/                WHMI-Link protocol: framing, crypto, codec, client, server
│   ├── receiver/            Receiver service: tag DB, I/O scanner, PLC drivers, safety logic
│   │   ├── io/              Local I/O module drivers (simulated; GPIO/SPI planned)
│   │   └── plc/             PLC drivers (Rockwell Logix via EtherNet/IP CIP, simulator)
│   ├── runtime/             HMI runtime engine for the panel (tag cache, subscriptions, writes)
│   └── tools/               `whmi` CLI: new/validate/build/download/monitor (Studio's backend)
├── studio/                  (planned) C# WHMI Studio solution
└── tests/                   pytest suite incl. end-to-end receiver ↔ runtime tests
```

### 2.1 Project model (`whmi.common`)

A project (`*.whp`, JSON) contains:

* `receiver`: identity, network and link settings, I/O slot population
* `plc_connections`: e.g. `{name: "PLC1", driver: "logix", host: "192.168.1.10", path: "1,0", poll_ms: 100}`
* `tags`: name, data type, source, access, scaling, units, momentary/fail-safe behaviour
* `screens`: widgets with tag bindings (rendered by the runtime, edited in Studio)

**Build** turns the project into a **compiled project**:

* validates every tag, binding, I/O address and PLC reference
* assigns each tag a compact numeric `tag_id` (u16) that the wire protocol uses
* emits canonical JSON and its SHA-256 hash, which identifies the project version

Data types follow Rockwell Logix atomic types (`BOOL, SINT, INT, DINT, LINT, USINT,
UINT, UDINT, ULINT, REAL, LREAL, STRING`). PLC tags then map 1:1 without lossy conversion.

### 2.2 Receiver service (`whmi.receiver`)

* **Tag DB:** current value, quality and timestamp for every tag. Change notification feeds subscriptions.
* **I/O scanner:** a fixed-rate loop (default 10 ms). It reads inputs, writes outputs, and applies scaling and fail-safe values.
* **PLC manager:** one driver per connection. Tags are grouped per connection and read in
  multi-tag requests at `poll_ms`, and writes are forwarded. On connection loss, all of that connection's tags get quality `BAD_COMM`, and the driver
  reconnects with exponential backoff.
* **Link server:** authenticated sessions, subscriptions with per-session
  publish intervals and deadband, writes checked against access rights and role.
* **Safety supervisor:** momentary timeouts, fail-safe on link loss, write interlocks.

### 2.3 Rockwell integration

Two complementary modes:

| Mode | How it works | Status |
|---|---|---|
| **Tag client (explicit messaging)** | The receiver opens a CIP connection to the Logix controller and reads/writes controller- or program-scoped tags by name (`Program:Main.Speed`). Needs no PLC program changes. | **Implemented** (pycomm3 `LogixDriver`) |
| **I/O adapter (implicit, Class 1)** | The receiver appears in the Logix I/O tree as a *Generic Ethernet Module* (later with its own EDS/AOP). Input/output assemblies carry the receiver's local I/O and HMI data at an RPI. Gives deterministic I/O and lets the PLC own the outputs. | Planned (Phase 4) |

### 2.4 HMI runtime (`whmi.runtime`)

* connection state machine: discover → connect → handshake → sync project → subscribe → run, with automatic reconnect and backoff
* tag cache with quality; all values go stale on disconnect
* write API, plus `press()`/`release()` for momentary tags, which re-asserts the value at heartbeat rate
* the UI layer (rendering screens and widgets) sits on top of the engine

## 3. Networking

* **Receiver WiFi modes:** *Access Point* (default; a private, self-contained cell, no plant WiFi needed)
  or *Client* (joins the plant WLAN, WPA2-Enterprise supported). 2.4 GHz and 5 GHz.
* **Ethernet:** static IP or DHCP. WiFi and Ethernet are separate interfaces. **No
  IP forwarding** between WLAN and the plant network. Panels can reach only the
  receiver's link service, never the PLC network directly.
* **Ports:** WHMI-Link TCP 47800 (WLAN, and Ethernet for Studio downloads). EtherNet/IP uses TCP 44818
  and UDP 2222 (Ethernet only).

## 4. Phases

See [ROADMAP.md](ROADMAP.md).
