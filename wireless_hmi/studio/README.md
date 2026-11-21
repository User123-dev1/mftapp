# WHMI Studio (C# / .NET 10 / Avalonia)

Engineering software for WHMI projects. It runs on Windows, Linux and macOS.

| Project | What it is |
|---|---|
| `src/Whmi.Core` | Project model, validation and build. Produces **byte-identical** compiled projects to the Python tools (tested). |
| `src/Whmi.Link` | WHMI-Link client: mutual HMAC auth, AES-256-GCM, download/upload/read/write/subscribe |
| `src/Whmi.Studio` | Avalonia desktop app (MVVM with CommunityToolkit.Mvvm) |
| `src/Whmi.Runtime` | Panel engine: connect, project sync, live tag cache, writes, momentary push-buttons, reconnect |
| `src/Whmi.Panel` | **WHMI Panel**: the operator app for Windows tablets and panel PCs |
| `tests/Whmi.Tests` | xUnit: core rules, Python parity, C# client and runtime ↔ real Python receiver, Studio UI tests |
| `tests/Whmi.Panel.Tests` | Panel UI tests: touch input, keypad, comm loss and recovery, PIN, screenshots |

## What Studio does

* **Project & I/O:** project name, receiver timing (scan / heartbeat / watchdog), and the 16 I/O slots.
* **PLC Connections:** Logix controllers (host, CIP path, poll rate, timeout, heartbeat tag).
* **Tags:** an editable tag database grid with filter, add, duplicate, delete, and CSV import/export (Excel friendly).
* **Screens:** a drag-and-drop screen designer with a toolbox, snap-to-grid, resize handle, and a properties panel.
* **Online:** connect to a receiver, then download, upload, live-monitor tags in the grid, and do test writes.
* **Problems panel:** validates live as you edit. Double-click a problem to jump to the tag or widget.

## Open it in Visual Studio (Windows)

1. Install **Visual Studio 2026** with the **.NET desktop development** workload. That includes the .NET 10 SDK.
2. Optional: install the **Avalonia for Visual Studio** extension (Extensions → Manage Extensions) to get the XAML
   previewer. Studio builds and runs without it.
3. Open `wireless_hmi/studio/Whmi.sln`, set **Whmi.Studio** as the startup project, and press **F5**.
4. **File → Open** → `wireless_hmi/examples/demo_line.whp`.

## Command line

```bash
cd wireless_hmi/studio
dotnet run --project src/Whmi.Studio -- ../examples/demo_line.whp     # run
dotnet test                                                            # 59 tests (Python tests need the whmi package)
dotnet publish src/Whmi.Studio -c Release -r win-x64 --self-contained \
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o publish/win-x64   # one WhmiStudio.exe
```

Headless UI tests write screenshots to `$WHMI_SCREENSHOT_DIR` (default: `<temp>/whmi-screens`).

## Try it against a receiver

```bash
cd wireless_hmi
whmi receiver init --data-dir ./rx-data --id RX1
whmi receiver run --config ./rx-data/receiver.json
```

In Studio, go to **Online** and set Host `127.0.0.1`, Client ID `studio` and Key file `rx-data/studio.key`. Then
**Connect**, **Download to receiver** and **Monitor tags**.

## WHMI Panel (Windows tablet / panel PC)

The operator app. It renders the screens designed in Studio as working touch controls.

* **Status bar:** receiver and link state, current screen, WiFi signal, battery, clock and settings.
* **Push-buttons:** momentary buttons are re-sent while held and released on lift, when the finger slides off,
  when the window loses focus, or when the screen changes. If WiFi drops, the receiver releases them itself.
* **Communication lost:** a full-screen overlay blocks input. Values show `---` and are never shown as live.
  The panel reconnects automatically.
* **On-screen keypad** for setpoints, checked against the tag's limits before writing (the receiver checks again).
* **Low-battery warning**, and the last project is cached so screens appear instantly at start-up.

### Install on the tablet
1. Copy `WhmiPanel.exe` to the tablet (it's a self-contained build, so no .NET install is needed).
2. Join the tablet to the receiver's WiFi.
3. Start WHMI Panel. On first run it opens **Settings**. Enter the receiver address and port, and **Import key file…**
   (e.g. `panel1.key` from `whmi receiver init`; the panel ID is taken from the file name).
   On Windows the key is stored encrypted with DPAPI.
4. Optional: set a **PIN**, and turn on **Kiosk mode**. Kiosk mode is full screen and always on top; the app can then
   only be closed from Settings.
5. Auto-start: put a shortcut to `WhmiPanel.exe` in `shell:startup`. For full lock-down on Windows 11 Pro / IoT Enterprise,
   use **Settings → Accounts → Other users → Set up a kiosk** (Assigned Access) with WhmiPanel.exe.

```bash
dotnet run --project src/Whmi.Panel          # on a development PC
dotnet publish src/Whmi.Panel -c Release -r win-x64 --self-contained -p:PublishSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true -o publish/panel-win-x64
```

Settings live in `%LOCALAPPDATA%\WhmiPanel` (override with `WHMI_PANEL_HOME`).
