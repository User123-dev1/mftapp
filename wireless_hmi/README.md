# WHMI: Wireless Industrial HMI

A wireless touchscreen HMI system made of three parts:

* **WHMI Panel**: the operator touchscreen. It connects over WiFi.
* **WHMI Receiver**: a DIN-rail I/O receiver with local I/O slots and a WiFi radio. Its **Ethernet port talks to
  Rockwell ControlLogix/CompactLogix PLCs** over EtherNet/IP.
* **WHMI Studio**: engineering software for building HMI projects (tags, I/O, PLC connections, screens) and
  downloading them. The `whmi` CLI in this repository is Studio's backend. The C# GUI is Phase 2.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first, then [docs/PROTOCOL.md](docs/PROTOCOL.md),
[docs/HARDWARE.md](docs/HARDWARE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

> **Safety:** WHMI is not a safety device. E-stops, guard interlocks and other safety functions must be
> hardwired (IEC 60204-1 / ISO 13850 / ISO 13849). The project builder rejects write bindings to tags flagged `safety`.

## Quick start (simulated hardware)

```bash
cd wireless_hmi
pip install -e '.[dev]'

# 1. Create a receiver with two clients: studio (engineer) and panel1 (operator)
whmi receiver init --data-dir ./rx-data --id RX1
whmi receiver run --config ./rx-data/receiver.json        # leave running

# 2. In a second terminal: build and download the demo project
whmi validate examples/demo_line.whp
whmi download examples/demo_line.whp --host 127.0.0.1 --client-id studio --key-file rx-data/studio.key

# 3. Talk to it like a panel would
export WHMI_KEY=$(cat rx-data/panel1.key)
whmi info    --host 127.0.0.1 --client-id panel1
whmi write   --host 127.0.0.1 --client-id panel1 Conveyor.Speed_SP 62.5
whmi read    --host 127.0.0.1 --client-id panel1 Conveyor.Speed_SP Tank.Level _System.PLC.PLC1.Connected
whmi monitor --host 127.0.0.1 --client-id panel1
```

To talk to a real controller, edit the demo's `PLC1` connection: set `"driver": "logix"` and `"host"`
to the controller IP, and set `"path"` (`"1,0"` = backplane, slot 0). Each tag's `address` is the
Logix tag name (`Motor1_Speed`, `Program:MainProgram.Level`, `Recipe.Steps[2].Temp`).

## Tests

```bash
python -m pytest            # 91 tests: model, codec, crypto attacks, receiver end-to-end, runtime, CLI
ruff check src tests
```
