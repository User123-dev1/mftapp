using System.Diagnostics;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Link;
using Whmi.Runtime;

namespace Whmi.Tests;

public class PyFormatTests
{
    [Theory]
    [InlineData("{:.1f} %", 62.54, "62.5 %")]
    [InlineData("{:.0f}", 2.5, "2")]
    [InlineData("{:.0f}", 3.5, "4")]
    [InlineData("{:.2f}", 2.675, "2.67")]
    [InlineData("{:.2f}", 0.125, "0.12")]
    [InlineData("{:,.0f}", -1234567.5, "-1,234,568")]
    [InlineData("{:d} pcs", 42L, "42 pcs")]
    [InlineData("{:,.2f}", 1234567.891, "1,234,567.89")]
    [InlineData("{:08.3f}", -3.14159, "-003.142")]
    [InlineData("{:>6}", 42L, "    42")]
    [InlineData("{:.1%}", 0.256, "25.6%")]
    [InlineData("{:x}", 255L, "ff")]
    [InlineData("{}", true, "true")]
    [InlineData("Level: {:.2f} m", 1.5, "Level: 1.50 m")]
    [InlineData("{{literal}} {:.1f}", 1.0, "{literal} 1.0")]
    [InlineData("{:+.1f}", 3.0, "+3.0")]
    public void FormatsLikePython(string format, object value, string expected) =>
        Assert.Equal(expected, PyFormat.Format(format, value));

    [Theory]
    [InlineData("{:.1q}")]
    [InlineData("{:.1f")]
    public void BadFormatFallsBackToPlainValue(string format) => Assert.Equal("12.5", PyFormat.Format(format, 12.5));
}

/// <summary>The C# panel runtime against a real Python receiver process (own instance: tests stop/restart it).</summary>
public sealed class RuntimeTests : IDisposable
{
    private readonly PythonReceiver _rx = new();
    private readonly string _cache = TestPaths.NewTempDir();
    private static readonly CompiledProject Demo = Project.Load(TestPaths.Example).Build();

    static RuntimeTests()
    {
        HmiRuntime.ReconnectMin = TimeSpan.FromMilliseconds(100);
        HmiRuntime.ReconnectMax = TimeSpan.FromMilliseconds(400);
    }

    public void Dispose() => _rx.Dispose();

    private async Task DownloadDemoAsync()
    {
        await using var studio = _rx.Client("studio");
        await studio.ConnectAsync();
        if (!studio.Session!.ProjectHash.AsSpan().SequenceEqual(Demo.Hash)) await studio.DownloadProjectAsync(Demo.Data);
    }

    private HmiRuntime NewRuntime(string client = "panel1") =>
        new(new PanelConnection("127.0.0.1", _rx.Port, client, _rx.Key(client)), _cache, 20);

    private static async Task WaitFor(Func<bool> cond, int timeoutMs = 8000)
    {
        var sw = Stopwatch.StartNew();
        while (!cond())
        {
            if (sw.ElapsedMilliseconds > timeoutMs) throw new TimeoutException("condition not met");
            await Task.Delay(20);
        }
    }

    private async Task<object> ReadOnReceiverAsync(string tag)
    {
        await using var c = _rx.Client("viewer");
        await c.ConnectAsync();
        return (await c.ReadAsync([Demo.Tag(tag)!.TagId]))[0].Value;
    }

    [Fact]
    public async Task SyncsProjectCachesItAndTracksValues()
    {
        if (!_rx.Available) return;
        await DownloadDemoAsync();
        await using var rt = NewRuntime();
        var states = new List<LinkState>();
        rt.StateChanged += states.Add;
        rt.Start();
        await WaitFor(() => rt.State == LinkState.Online);
        Assert.Equal(Demo.HashHex, rt.Project!.HashHex);
        Assert.True(File.Exists(Path.Combine(_cache, "project.whc")));
        Assert.Contains(LinkState.Syncing, states);
        Assert.Equal(Role.OPERATOR, rt.Role);
        Assert.True(rt.CanWrite);

        Assert.Equal(Status.OK, await rt.WriteAsync("Batch.Count", 99L));
        await WaitFor(() => rt.Get("Batch.Count") is { Good: true, Value: 99L });
        Assert.Equal(Status.OUT_OF_RANGE, await rt.WriteAsync("Conveyor.Speed_SP", 500.0));
        Assert.Equal(Status.BAD_TAG, await rt.WriteAsync("No.Such.Tag", 1L));
    }

    [Fact]
    public async Task MomentaryStaysHeldWhileRefreshedThenReleases()
    {
        if (!_rx.Available) return;
        await DownloadDemoAsync();
        await using var rt = NewRuntime();
        rt.Start();
        await WaitFor(() => rt.State == LinkState.Online);

        Assert.Equal(Status.INVALID, await rt.PressAsync("Batch.Count"));
        Assert.Equal(Status.OK, await rt.PressAsync("Tank.Jog_Fwd"));
        await Task.Delay(1500); // > 2 hold periods (hold = 3 x 250 ms heartbeat)
        Assert.Equal(true, await ReadOnReceiverAsync("Tank.Jog_Fwd"));
        Assert.True(rt.IsHeld(Demo.Tag("Tank.Jog_Fwd")!));
        Assert.Equal(Status.OK, await rt.ReleaseAsync("Tank.Jog_Fwd"));
        Assert.Equal(false, await ReadOnReceiverAsync("Tank.Jog_Fwd"));
    }

    [Fact]
    public async Task LinkLossMarksStaleAndReconnects()
    {
        if (!_rx.Available) return;
        await DownloadDemoAsync();
        await using var rt = NewRuntime();
        rt.Start();
        await WaitFor(() => rt.State == LinkState.Online);
        await WaitFor(() => rt.Get("Tank.Level")!.Good);
        Assert.Equal(Status.OK, await rt.PressAsync("Tank.Jog_Fwd"));

        _rx.StopProcess(); // receiver gone
        await WaitFor(() => rt.State != LinkState.Online);
        await WaitFor(() => rt.Get("Tank.Level")!.Quality == Quality.BAD_NOT_CONNECTED);
        Assert.False(rt.IsHeld(Demo.Tag("Tank.Jog_Fwd")!)); // never re-asserted after the link drops
        Assert.False(rt.CanWrite);
        Assert.Equal(Status.COMM_FAILURE, await rt.WriteAsync("Batch.Count", 1L));

        _rx.StartProcess(); // receiver back
        await WaitFor(() => rt.State == LinkState.Online, 10000);
        await WaitFor(() => rt.Get("Tank.Level")!.Good);
        Assert.Equal(false, await ReadOnReceiverAsync("Tank.Jog_Fwd"));
    }

    [Fact]
    public async Task ResyncsWhenProjectIsRedownloaded()
    {
        if (!_rx.Available) return;
        await DownloadDemoAsync();
        await using var rt = NewRuntime();
        rt.Start();
        await WaitFor(() => rt.State == LinkState.Online);
        var p = Project.Load(TestPaths.Example);
        p.Description = "v2";
        var v2 = p.Build();
        await using (var studio = _rx.Client("studio"))
        {
            await studio.ConnectAsync();
            await studio.DownloadProjectAsync(v2.Data);
        }
        await WaitFor(() => rt.Project!.HashHex == v2.HashHex && rt.State == LinkState.Online);
    }

    [Fact]
    public async Task WrongKeyGivesAuthFailed()
    {
        if (!_rx.Available) return;
        await using var rt = new HmiRuntime(new PanelConnection("127.0.0.1", _rx.Port, "panel1", new byte[32]));
        rt.Start();
        await WaitFor(() => rt.State == LinkState.AuthFailed);
        Assert.Contains("rejected", rt.LastError);
    }

    [Fact]
    public async Task UsesCachedProjectWhileOffline()
    {
        if (!_rx.Available) return;
        await DownloadDemoAsync();
        await using (var rt = NewRuntime())
        {
            rt.Start();
            await WaitFor(() => rt.State == LinkState.Online);
        }
        _rx.StopProcess();
        await using var offline = NewRuntime();
        Assert.Equal(Demo.HashHex, offline.Project!.HashHex); // screens can be drawn before the link is up
        Assert.Equal(Quality.BAD_NOT_CONNECTED, offline.Get("Tank.Level")!.Quality);
    }
}
