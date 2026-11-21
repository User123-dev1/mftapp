# WHMI Studio (C# / .NET 8 / Avalonia)

Engineering software for WHMI projects. It runs on Windows, Linux and macOS.

| Project | What it is |
|---|---|
| `src/Whmi.Core` | Project model, validation and build. Produces **byte-identical** compiled projects to the Python tools (tested). |
| `src/Whmi.Link` | WHMI-Link client: mutual HMAC auth, AES-256-GCM, download/upload/read/write/subscribe |
| `src/Whmi.Studio` | Avalonia desktop app (MVVM with CommunityToolkit.Mvvm) |
| `tests/Whmi.Tests` | xUnit: core rules, Python parity, C# client ↔ real Python receiver, headless UI tests with screenshots |

## What Studio does

* **Project & I/O:** project name, receiver timing (scan / heartbeat / watchdog), and the 16 I/O slots.
* **PLC Connections:** Logix controllers (host, CIP path, poll rate, timeout, heartbeat tag).
* **Tags:** an editable tag database grid with filter, add, duplicate, delete, and CSV import/export (Excel friendly).
* **Screens:** a drag-and-drop screen designer with a toolbox, snap-to-grid, resize handle, and a properties panel.
* **Online:** connect to a receiver, then download, upload, live-monitor tags in the grid, and do test writes.
* **Problems panel:** validates live as you edit. Double-click a problem to jump to the tag or widget.

## Open it in Visual Studio (Windows)

1. Install **Visual Studio 2022** (17.8 or newer) with the **.NET desktop development** workload. That includes the .NET 8 SDK.
2. Optional: install the **Avalonia for Visual Studio** extension to get the XAML previewer.
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
