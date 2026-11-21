using Avalonia;
using Avalonia.Headless;
using Avalonia.Headless.XUnit;
using Avalonia.Input;
using Avalonia.Threading;
using Avalonia.VisualTree;
using Whmi.Core.Model;
using Whmi.Panel.Controls;
using Whmi.Panel.Platform;
using Whmi.Panel.ViewModels;
using Whmi.Panel.Views;
using Whmi.Runtime;

[assembly: AvaloniaTestApplication(typeof(Whmi.Tests.PanelTestApp))]

namespace Whmi.Tests;

public static class PanelTestApp
{
    public static AppBuilder BuildAvaloniaApp() =>
        AppBuilder.Configure<Whmi.Panel.App>()
            .UseSkia()
            .WithInterFont()
            .UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false });
}

internal sealed class FakeDevice : IDeviceStatus
{
    public int Battery { get; set; } = 64;
    public DeviceReading Read() => new(Battery, false, 72, "WHMI-RX1");
}

/// <summary>A panel window connected to a real Python receiver running the demo project.</summary>
internal sealed class PanelRig : IAsyncDisposable
{
    public static readonly CompiledProject Demo = Project.Load(TestPaths.Example).Build();

    private PanelRig(PythonReceiver rx, string dir, PanelViewModel vm, MainWindow window)
    {
        Rx = rx;
        Dir = dir;
        Vm = vm;
        Window = window;
    }

    public PythonReceiver Rx { get; }
    public string Dir { get; }
    public PanelViewModel Vm { get; }
    public MainWindow Window { get; }
    public FakeDevice Device { get; } = new();

    public static string ScreenshotDir { get; } =
        Environment.GetEnvironmentVariable("WHMI_SCREENSHOT_DIR") is { Length: > 0 } d ? d : Path.Combine(Path.GetTempPath(), "whmi-screens");

    public static async Task<PanelRig?> StartAsync(bool paired = true, string pin = "")
    {
        if (!TestPaths.PythonAvailable) return null;
        HmiRuntime.ReconnectMin = TimeSpan.FromMilliseconds(100);
        HmiRuntime.ReconnectMax = TimeSpan.FromMilliseconds(400);
        var rx = new PythonReceiver();
        await using (var studio = rx.Client("studio"))
        {
            await studio.ConnectAsync();
            await studio.DownloadProjectAsync(Demo.Data);
            // The receiver reconnects to the PLC after activation, so retry until the writes are accepted.
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (true)
            {
                var results = await studio.WriteAsync([
                    (Demo.Tag("Conveyor.Speed_SP")!.TagId, Core.DataType.REAL, 62.5),
                    (Demo.Tag("Batch.Count")!.TagId, Core.DataType.DINT, 1287L),
                ]);
                if (results.All(r => r.Status == Core.Status.OK)) break;
                if (DateTime.UtcNow > deadline) throw new TimeoutException($"setup writes failed: {string.Join(", ", results)}");
                await Task.Delay(100);
            }
        }
        var dir = TestPaths.NewTempDir();
        if (paired)
        {
            var s = new PanelSettings { Host = "127.0.0.1", Port = rx.Port, ClientId = "panel1", Pin = pin };
            s.SetKey(rx.Key("panel1"));
            s.Save(dir);
        }
        var device = new FakeDevice();
        var vm = new PanelViewModel(dir, device);
        var window = new MainWindow { DataContext = vm, Width = 1280, Height = 800 };
        window.Show();
        vm.Start();
        var rig = new PanelRig(rx, dir, vm, window);
        if (paired) await rig.WaitFor(() => vm.StateText == "Online" && !vm.ShowCommLost && vm.HasScreen);
        return rig;
    }

    public async Task WaitFor(Func<bool> cond, int timeoutMs = 10000)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        while (!cond())
        {
            if (DateTime.UtcNow > deadline) throw new TimeoutException("condition not met");
            Dispatcher.UIThread.RunJobs();
            await Task.Delay(20);
        }
        Dispatcher.UIThread.RunJobs();
    }

    public async Task<object> ReadAsync(string tag)
    {
        await using var c = Rx.Client("viewer");
        await c.ConnectAsync();
        return (await c.ReadAsync([Demo.Tag(tag)!.TagId]))[0].Value;
    }

    /// <summary>Centre of the touch area whose view model matches, in window coordinates.</summary>
    public Point Center(Func<object?, bool> match)
    {
        Dispatcher.UIThread.RunJobs();
        var area = Window.GetVisualDescendants().OfType<TouchArea>().First(a => match(a.DataContext));
        return area.TranslatePoint(new Point(area.Bounds.Width / 2, area.Bounds.Height / 2), Window)!.Value;
    }

    public Point ButtonCenter(string text) => Center(dc => dc is ButtonWidgetViewModel b && b.Text == text);

    public void Tap(Point p)
    {
        Window.MouseDown(p, MouseButton.Left);
        Window.MouseUp(p, MouseButton.Left);
        Dispatcher.UIThread.RunJobs();
    }

    public void Snap(string name)
    {
        Vm.Tick();
        Dispatcher.UIThread.RunJobs();
        AvaloniaHeadlessPlatform.ForceRenderTimerTick(); // otherwise the previous frame is returned
        var frame = Window.CaptureRenderedFrame();
        Assert.NotNull(frame);
        Directory.CreateDirectory(ScreenshotDir);
        frame!.Save(Path.Combine(ScreenshotDir, name + ".png"));
    }

    public async ValueTask DisposeAsync()
    {
        await Vm.DisposeAsync();
        Window.Close();
        Rx.Dispose();
    }
}

public class PanelTests
{
    [AvaloniaFact]
    public async Task FirstRunShowsPairingAndPairs()
    {
        await using var rig = await PanelRig.StartAsync(paired: false);
        if (rig is null) return;
        var settings = Assert.IsType<SettingsViewModel>(rig.Vm.SettingsPage);
        Assert.False(settings.CanCancel);
        settings.Host = "127.0.0.1";
        settings.Port = rig.Rx.Port;
        settings.ClientId = "panel1";
        rig.Snap("panel-1-pairing");
        settings.SaveCommand.Execute(null);
        Assert.Contains("Enter the panel key", settings.Error);
        settings.KeyHex = Convert.ToHexString(rig.Rx.Key("panel1"));
        settings.SaveCommand.Execute(null);
        Assert.Null(rig.Vm.SettingsPage);
        await rig.WaitFor(() => rig.Vm.StateText == "Online" && rig.Vm.HasScreen);
        Assert.True(PanelSettings.Load(rig.Dir).IsPaired);
    }

    [AvaloniaFact]
    public async Task RendersStartScreenWithLiveValues()
    {
        await using var rig = await PanelRig.StartAsync();
        if (rig is null) return;
        Assert.Equal("Main", rig.Vm.ScreenName);
        var speed = rig.Vm.CurrentScreen!.Widgets.OfType<EntryWidgetViewModel>().Single(w => w.Tag!.Name == "Conveyor.Speed_SP");
        await rig.WaitFor(() => speed.Text == "62.5 %");
        var count = rig.Vm.CurrentScreen.Widgets.OfType<DisplayWidgetViewModel>().Single(w => w.Tag!.Name == "Batch.Count");
        Assert.Equal("1287", count.Text);
        Assert.Equal("RX-NET", rig.Vm.ReceiverText);
        Assert.Equal("64%", rig.Vm.BatteryText);
        Assert.Equal(3, rig.Vm.WifiBars);
        rig.Snap("panel-2-main-online");
    }

    [AvaloniaFact]
    public async Task MomentaryButtonHoldsWhilePressedAndReleasesOnLift()
    {
        await using var rig = await PanelRig.StartAsync();
        if (rig is null) return;
        var start = rig.ButtonCenter("START");
        rig.Window.MouseDown(start, MouseButton.Left);
        await rig.WaitFor(() => rig.Vm.Runtime!.IsHeld(PanelRig.Demo.Tag("Line.Start_PB")!));
        await Task.Delay(1200); // longer than one hold period: the panel keeps re-asserting
        Assert.Equal(true, await rig.ReadAsync("Line.Start_PB"));
        rig.Snap("panel-3-start-pressed");
        rig.Window.MouseUp(start, MouseButton.Left);
        await rig.WaitFor(() => !rig.Vm.Runtime!.IsHeld(PanelRig.Demo.Tag("Line.Start_PB")!));
        Assert.Equal(false, await rig.ReadAsync("Line.Start_PB"));
    }

    [AvaloniaFact]
    public async Task SlidingOffAMomentaryButtonReleasesIt()
    {
        await using var rig = await PanelRig.StartAsync();
        if (rig is null) return;
        var jog = rig.ButtonCenter("JOG");
        rig.Window.MouseDown(jog, MouseButton.Left);
        await rig.WaitFor(() => rig.Vm.Runtime!.IsHeld(PanelRig.Demo.Tag("Tank.Jog_Fwd")!));
        Assert.Equal(true, await rig.ReadAsync("Tank.Jog_Fwd"));
        rig.Window.MouseMove(new Point(jog.X + 400, jog.Y + 300)); // finger slides away, still touching
        await rig.WaitFor(() => !rig.Vm.Runtime!.IsHeld(PanelRig.Demo.Tag("Tank.Jog_Fwd")!));
        Assert.Equal(false, await rig.ReadAsync("Tank.Jog_Fwd"));
        rig.Window.MouseUp(new Point(jog.X + 400, jog.Y + 300), MouseButton.Left);
    }

    [AvaloniaFact]
    public async Task KeypadWritesSetpointAndRejectsOutOfRange()
    {
        await using var rig = await PanelRig.StartAsync();
        if (rig is null) return;
        rig.Tap(rig.Center(dc => dc is EntryWidgetViewModel e && e.Tag!.Name == "Conveyor.Speed_SP"));
        var keypad = Assert.IsType<KeypadViewModel>(rig.Vm.Keypad);
        Assert.Equal("62.5", keypad.Entry);
        Assert.Contains("0 to 100 %", keypad.Hint);
        keypad.ClearCommand.Execute(null);
        foreach (var k in "150") keypad.KeyCommand.Execute(k.ToString());
        await keypad.OkCommand.ExecuteAsync(null);
        Assert.Equal("Maximum is 100", keypad.Error);
        keypad.ClearCommand.Execute(null);
        foreach (var k in "75.5") keypad.KeyCommand.Execute(k.ToString());
        rig.Snap("panel-4-keypad");
        await keypad.OkCommand.ExecuteAsync(null);
        Assert.Null(rig.Vm.Keypad);
        Assert.Equal(75.5, await rig.ReadAsync("Conveyor.Speed_SP"));
    }

    [AvaloniaFact]
    public async Task CommunicationLossBlocksTheScreenAndRecovers()
    {
        await using var rig = await PanelRig.StartAsync();
        if (rig is null) return;
        rig.Rx.StopProcess();
        await rig.WaitFor(() => rig.Vm.ShowCommLost && rig.Vm.CommLostTitle == "COMMUNICATION LOST");
        Assert.False(rig.Vm.CanWrite);
        var speed = rig.Vm.CurrentScreen!.Widgets.OfType<EntryWidgetViewModel>().Single(w => w.Tag!.Name == "Conveyor.Speed_SP");
        await rig.WaitFor(() => speed.Text == "---"); // stale values are never shown as live
        rig.Snap("panel-5-comm-lost");
        rig.Rx.StartProcess();
        await rig.WaitFor(() => !rig.Vm.ShowCommLost && rig.Vm.StateText == "Online", 15000);
        // the simulated PLC restarts empty, so just require a live (non-stale) value again
        await rig.WaitFor(() => speed.Text != "---");
    }

    [AvaloniaFact]
    public async Task GotoNavigatesBetweenScreens()
    {
        await using var rig = await PanelRig.StartAsync();
        if (rig is null) return;
        rig.Tap(rig.Center(dc => dc is GotoWidgetViewModel g && g.Text == "DIAGNOSTICS"));
        Assert.Equal("Diagnostics", rig.Vm.ScreenName);
        var plc = rig.Vm.CurrentScreen!.Widgets.OfType<IndicatorWidgetViewModel>().Single();
        await rig.WaitFor(() => plc.Text == "PLC1 ONLINE");
        rig.Snap("panel-6-diagnostics");
        rig.Tap(rig.Center(dc => dc is GotoWidgetViewModel g && g.Text == "BACK"));
        Assert.Equal("Main", rig.Vm.ScreenName);
    }

    [AvaloniaFact]
    public async Task SettingsAreProtectedByPin()
    {
        await using var rig = await PanelRig.StartAsync(pin: "2468");
        if (rig is null) return;
        rig.Vm.OpenSettingsCommand.Execute(null);
        var pin = Assert.IsType<KeypadViewModel>(rig.Vm.Keypad);
        Assert.True(pin.IsPin);
        foreach (var k in "1111") pin.KeyCommand.Execute(k.ToString());
        Assert.Equal("●●●●", pin.Display);
        await pin.OkCommand.ExecuteAsync(null);
        Assert.Equal("Wrong PIN", pin.Error);
        Assert.Null(rig.Vm.SettingsPage);
        pin.ClearCommand.Execute(null);
        foreach (var k in "2468") pin.KeyCommand.Execute(k.ToString());
        await pin.OkCommand.ExecuteAsync(null);
        Assert.NotNull(rig.Vm.SettingsPage);
    }

    [AvaloniaFact]
    public async Task LowBatteryWarns()
    {
        await using var rig = await PanelRig.StartAsync();
        if (rig is null) return;
        var device = (FakeDevice)typeof(PanelViewModel)
            .GetField("_device", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)!
            .GetValue(rig.Vm)!;
        device.Battery = 9;
        rig.Vm.Tick();
        Assert.True(rig.Vm.LowBattery);
    }
}
