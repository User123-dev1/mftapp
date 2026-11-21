using Avalonia;
using Avalonia.Headless;
using Avalonia.Headless.XUnit;
using Avalonia.Threading;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Studio.Services;
using Whmi.Studio.ViewModels;
using Whmi.Studio.Views;

[assembly: AvaloniaTestApplication(typeof(Whmi.Tests.TestAppBuilder))]

namespace Whmi.Tests;

public static class TestAppBuilder
{
    public static AppBuilder BuildAvaloniaApp() =>
        AppBuilder.Configure<Whmi.Studio.App>()
            .UseSkia()
            .WithInterFont()
            .UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false });
}

/// <summary>Scripted dialogs for tests.</summary>
internal sealed class FakeDialogs : IDialogs
{
    public Queue<string?> Files { get; } = new();
    public bool ConfirmResult { get; set; } = true;
    public List<string> Confirmations { get; } = [];

    public Task<string?> OpenFileAsync(string title, string filterName, params string[] patterns) =>
        Task.FromResult(Files.Count > 0 ? Files.Dequeue() : null);

    public Task<string?> SaveFileAsync(string title, string suggestedName, string filterName, string extension) =>
        Task.FromResult(Files.Count > 0 ? Files.Dequeue() : null);

    public Task<bool> ConfirmAsync(string title, string message)
    {
        Confirmations.Add(message);
        return Task.FromResult(ConfirmResult);
    }
}

public class StudioViewModelTests
{
    private static MainWindowViewModel Open(FakeDialogs? dialogs = null)
    {
        var vm = new MainWindowViewModel(dialogs ?? new FakeDialogs());
        vm.OpenPath(TestPaths.Example);
        Assert.False(vm.IsDirty);
        return vm;
    }

    [AvaloniaFact]
    public void OpeningTheDemoHasNoProblems()
    {
        var vm = Open();
        Assert.Equal("DemoLine", vm.Project.Name);
        Assert.Equal("No problems", vm.IssueSummary);
        Assert.Equal(13, vm.Tags.Rows.Count);
    }

    [AvaloniaFact]
    public void EditingMarksDirtyAndRevalidates()
    {
        var vm = Open();
        vm.Tags.AddCommand.Execute(null);
        Assert.True(vm.IsDirty);
        vm.Tags.SelectedRow!.Name = "Tank.Level"; // duplicate name
        vm.ValidateNow();
        Assert.Contains(vm.Issues, i => i.Message.Contains("duplicate tag name"));
        Assert.EndsWith("*", vm.Title.Split("  [")[0]);
    }

    [AvaloniaFact]
    public void ProblemNavigationSelectsTheTag()
    {
        var vm = Open();
        vm.Project.FindTag("Tank.Level")!.Address = "Slot9.AI.0";
        vm.ValidateNow();
        var issue = Assert.Single(vm.Issues, i => i.Path == "tags.Tank.Level");
        vm.Navigate(issue);
        Assert.Same(vm.Tags, vm.CurrentSection);
        Assert.Equal("Tank.Level", vm.Tags.SelectedRow!.Name);
    }

    [AvaloniaFact]
    public void TagRowTextColumnsParse()
    {
        var vm = Open();
        var row = vm.Tags.RowFor(vm.Project.FindTag("Conveyor.Speed_SP")!);
        row.MaxText = "120.5";
        Assert.Equal(120.5, row.Tag.Max);
        row.MinText = "";
        Assert.Null(row.Tag.Min);
        Assert.Throws<FormatException>(() => row.MaxText = "abc");
        row.ScalingText = "0,32000,0,100";
        Assert.Equal(new Scaling(0, 32000, 0, 100), row.Tag.Scaling);
        row.FailsafeText = "0";
        Assert.Equal(0L, row.Tag.FailsafeValue);
    }

    [AvaloniaFact]
    public void FilterAndDeleteTags()
    {
        var vm = Open();
        vm.Tags.Filter = "tank.";
        Assert.Equal(4, vm.Tags.Rows.Count);
        vm.Tags.SelectedRow = vm.Tags.Rows[0];
        vm.Tags.DeleteCommand.Execute(null);
        Assert.Equal(3, vm.Tags.Rows.Count);
        Assert.Equal(12, vm.Project.Tags.Count);
    }

    [AvaloniaFact]
    public void CsvRoundTrip()
    {
        var vm = Open();
        var csv = TagCsv.Export(vm.Project.Tags);
        var tags = TagCsv.Import(csv);
        Assert.Equal(vm.Project.Tags.Select(t => Serialization.TagToJson(t).ToJsonString()),
                     tags.Select(t => Serialization.TagToJson(t).ToJsonString()));
        vm.Tags.ImportCsv("name,data_type,source,access\nNew.Thing,REAL,memory,read_write\nBatch.Count,LINT,memory,read\n");
        Assert.Equal(DataType.REAL, vm.Project.FindTag("New.Thing")!.DataType);
        Assert.Equal(DataType.LINT, vm.Project.FindTag("Batch.Count")!.DataType);
        Assert.Contains("1 added, 1 updated", vm.StatusText);
    }

    [AvaloniaFact]
    public void ScreenDesignerAddsMovesAndSnaps()
    {
        var vm = Open();
        var screens = vm.Screens;
        Assert.Equal("Main", screens.SelectedScreen!.Name);
        int before = screens.SelectedScreen.Widgets.Count;
        screens.AddWidgetCommand.Execute("gauge");
        var w = screens.SelectedWidget!;
        Assert.Equal(before + 1, screens.SelectedScreen.Widgets.Count);
        Assert.True(w.IsSelected);
        screens.MoveTo(w, 103, 47);
        Assert.Equal((105.0, 45.0), (w.X, w.Y));
        screens.MoveTo(w, 5000, -20); // kept on screen
        Assert.Equal(1024 - w.W, w.X);
        Assert.Equal(0, w.Y);
        vm.ValidateNow();
        Assert.Contains(vm.Issues, i => i.Path == $"screens.Main.{w.Id}" && i.Message.Contains("bound tag"));
        w.Tag = "Tank.Level";
        vm.ValidateNow();
        Assert.Equal("No problems", vm.IssueSummary);
    }

    [AvaloniaFact]
    public void SaveAndReopenIsLossless()
    {
        var dir = TestPaths.NewTempDir();
        var path = Path.Combine(dir, "copy.whp");
        var vm = Open();
        vm.SaveTo(path);
        Assert.False(vm.IsDirty);
        var original = Project.Load(TestPaths.Example).Build();
        Assert.Equal(original.HashHex, Project.Load(path).Build().HashHex);
    }

    [AvaloniaFact]
    public async Task NewProjectAsksBeforeDiscarding()
    {
        var dialogs = new FakeDialogs { ConfirmResult = false };
        var vm = Open(dialogs);
        vm.Project.Description = "changed";
        await vm.NewCommand.ExecuteAsync(null);
        Assert.Equal("DemoLine", vm.Project.Name); // user said no
        dialogs.ConfirmResult = true;
        await vm.NewCommand.ExecuteAsync(null);
        Assert.Equal("NewProject", vm.Project.Name);
    }
}

public class StudioWindowTests
{
    public static string ScreenshotDir { get; } =
        Environment.GetEnvironmentVariable("WHMI_SCREENSHOT_DIR") is { Length: > 0 } d ? d : Path.Combine(Path.GetTempPath(), "whmi-screens");

    internal static void Snap(MainWindow window, string name)
    {
        Dispatcher.UIThread.RunJobs();
        var frame = window.CaptureRenderedFrame();
        Assert.NotNull(frame);
        Directory.CreateDirectory(ScreenshotDir);
        frame!.Save(Path.Combine(ScreenshotDir, name + ".png"));
    }

    [AvaloniaFact]
    public void EverySectionRenders()
    {
        var vm = new MainWindowViewModel(new FakeDialogs());
        vm.OpenPath(TestPaths.Example);
        var window = new MainWindow { DataContext = vm, Width = 1440, Height = 900 };
        window.Show();
        vm.Project.FindTag("Tank.Level")!.Address = "Slot9.AI.0"; // show a problem in the list
        vm.ValidateNow();
        foreach (var (section, name) in new (SectionViewModel, string)[]
                 {
                     (vm.Receiver, "1-project-io"), (vm.Connections, "2-plc-connections"), (vm.Tags, "3-tags"),
                     (vm.Screens, "4-screen-designer"), (vm.Online, "5-online"),
                 })
        {
            vm.CurrentSection = section;
            if (section == vm.Screens) vm.Screens.SelectedWidget = vm.Screens.SelectedScreen!.Widgets.First(w => w.Id == "start");
            Snap(window, name);
        }
        vm.IsDirty = false; // otherwise closing waits for the unsaved-changes prompt
        window.Close();
    }
}

public class StudioOnlineTests
{
    private static async Task WaitFor(Func<bool> cond, int timeoutMs = 8000)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        while (!cond())
        {
            if (DateTime.UtcNow > deadline) throw new TimeoutException("condition not met");
            Dispatcher.UIThread.RunJobs();
            await Task.Delay(20);
        }
    }

    [AvaloniaFact]
    public async Task ConnectDownloadMonitorUploadAgainstPythonReceiver()
    {
        if (!TestPaths.PythonAvailable) return;
        using var rx = new PythonReceiver();
        var dialogs = new FakeDialogs();
        var vm = new MainWindowViewModel(dialogs);
        vm.OpenPath(TestPaths.Example);
        var online = vm.Online;
        online.Host = "127.0.0.1";
        online.Port = rx.Port;
        online.ClientId = "studio";
        online.KeyFile = Path.Combine(rx.DataDir, "studio.key");

        await online.ConnectCommand.ExecuteAsync(null);
        Assert.True(online.IsConnected, online.Status);
        Assert.Equal("RX-NET", online.ReceiverId);
        Assert.Equal("engineer", online.Role);

        await online.DownloadCommand.ExecuteAsync(null);
        Assert.StartsWith("Download OK", online.Status);
        Assert.Contains(dialogs.Confirmations, m => m.Contains("outputs switch OFF"));
        Assert.StartsWith("✔", online.SyncText);

        await online.ToggleMonitorCommand.ExecuteAsync(null);
        Assert.True(online.IsMonitoring, online.Status);
        Assert.True(vm.Tags.ShowLive);
        online.WriteTag = "Batch.Count";
        online.WriteValue = "4242";
        await online.WriteCommand.ExecuteAsync(null);
        Assert.EndsWith("OK", online.Status);
        var row = vm.Tags.RowFor(vm.Project.FindTag("Batch.Count")!);
        await WaitFor(() => row.LiveValue == "4242");
        Assert.Equal("good", row.LiveQuality);

        var window = new MainWindow { DataContext = vm, Width = 1440, Height = 900 };
        window.Show();
        vm.CurrentSection = vm.Tags;
        StudioWindowTests.Snap(window, "6-tags-monitoring");
        vm.CurrentSection = vm.Online;
        StudioWindowTests.Snap(window, "7-online-connected");
        vm.IsDirty = false;
        window.Close();

        // upload replaces the open project with what the receiver runs
        vm.Project.Name = "Edited";
        await online.UploadCommand.ExecuteAsync(null);
        Assert.Equal("DemoLine", vm.Project.Name);
        Assert.DoesNotContain(vm.Project.Tags, t => t.Source == TagSource.System);

        await online.DisconnectCommand.ExecuteAsync(null);
        Assert.False(online.IsConnected);
    }

    [AvaloniaFact]
    public async Task OperatorCannotDownload()
    {
        if (!TestPaths.PythonAvailable) return;
        using var rx = new PythonReceiver();
        var vm = new MainWindowViewModel(new FakeDialogs());
        vm.OpenPath(TestPaths.Example);
        vm.Online.Host = "127.0.0.1";
        vm.Online.Port = rx.Port;
        vm.Online.ClientId = "panel1";
        vm.Online.KeyFile = Path.Combine(rx.DataDir, "panel1.key");
        await vm.Online.ConnectCommand.ExecuteAsync(null);
        await vm.Online.DownloadCommand.ExecuteAsync(null);
        Assert.Contains("engineer role", vm.Online.Status);
    }

    [AvaloniaFact]
    public async Task BadKeyReportsFailure()
    {
        if (!TestPaths.PythonAvailable) return;
        using var rx = new PythonReceiver();
        var vm = new MainWindowViewModel(new FakeDialogs());
        vm.Online.Host = "127.0.0.1";
        vm.Online.Port = rx.Port;
        vm.Online.ClientId = "studio";
        vm.Online.KeyHex = new string('0', 64);
        await vm.Online.ConnectCommand.ExecuteAsync(null);
        Assert.False(vm.Online.IsConnected);
        Assert.Contains("rejected", vm.Online.Status);
    }
}
