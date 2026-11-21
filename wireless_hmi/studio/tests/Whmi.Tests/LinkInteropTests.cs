using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Link;

namespace Whmi.Tests;

[CollectionDefinition("python-receiver")]
public sealed class PythonReceiverCollection : ICollectionFixture<PythonReceiver>;

[Collection("python-receiver")]
public class LinkInteropTests(PythonReceiver rx)
{
    private static readonly CompiledProject Demo = Project.Load(TestPaths.Example).Build();

    private async Task<LinkClient> Connected(string id)
    {
        var c = rx.Client(id);
        await c.ConnectAsync();
        return c;
    }

    private async Task EnsureProjectAsync()
    {
        await using var studio = await Connected("studio");
        if (!studio.Session!.ProjectHash.AsSpan().SequenceEqual(Demo.Hash))
            await studio.DownloadProjectAsync(Demo.Data);
    }

    private static int Id(string name) => Demo.Tag(name)!.TagId;

    [Fact]
    public async Task HandshakeGivesSessionInfo()
    {
        if (!rx.Available) return;
        await using var c = await Connected("studio");
        Assert.Equal("RX-NET", c.ReceiverId);
        Assert.Equal(Role.ENGINEER, c.Session!.Role);
        Assert.True(c.Session.HeartbeatMs > 0);
    }

    [Fact]
    public async Task WrongKeyIsRejected()
    {
        if (!rx.Available) return;
        await using var c = rx.Client("panel1", new byte[32]);
        await Assert.ThrowsAsync<AuthenticationException>(() => c.ConnectAsync(TimeSpan.FromSeconds(5)));
    }

    [Fact]
    public async Task DownloadThenFetchIsIdentical()
    {
        if (!rx.Available) return;
        await using (var studio = await Connected("studio"))
        {
            var progress = new List<double>();
            var msg = await studio.DownloadProjectAsync(Demo.Data, new Progress<double>(progress.Add));
            Assert.Contains("DemoLine", msg);
        }
        await using var panel = await Connected("panel1");
        Assert.Equal(Demo.Hash, panel.Session!.ProjectHash);
        Assert.Equal(Demo.Data, await panel.FetchProjectAsync());
    }

    [Fact]
    public async Task OperatorCannotDownload()
    {
        if (!rx.Available) return;
        await using var panel = await Connected("panel1");
        var e = await Assert.ThrowsAsync<LinkException>(() => panel.DownloadProjectAsync(Demo.Data));
        Assert.Contains("NOT_PERMITTED", e.Message);
    }

    [Fact]
    public async Task ReadWriteAllValueKinds()
    {
        if (!rx.Available) return;
        await EnsureProjectAsync();
        await using var c = await Connected("panel1");
        var results = await c.WriteAsync([
            (Id("Batch.Count"), DataType.DINT, 1234L),
            (Id("Batch.Operator_Note"), DataType.STRING, "Grüße"),
            (Id("Conveyor.Speed_SP"), DataType.REAL, 62.5),
            (Id("Conveyor.Speed_SP"), DataType.REAL, 500.0),
            (Id("Safety.EStop_OK"), DataType.BOOL, true),
        ]);
        Assert.Equal([Status.OK, Status.OK, Status.OK, Status.OUT_OF_RANGE, Status.READ_ONLY], results.Select(r => r.Status));
        var values = await c.ReadAsync([Id("Batch.Count"), Id("Batch.Operator_Note"), Id("Conveyor.Speed_SP")]);
        Assert.Equal(1234L, values[0].Value);
        Assert.Equal("Grüße", values[1].Value);
        Assert.Equal(62.5, values[2].Value);
        Assert.Equal(Quality.GOOD, values[0].Quality);
    }

    [Fact]
    public async Task ViewerCannotWrite()
    {
        if (!rx.Available) return;
        await EnsureProjectAsync();
        await using var c = await Connected("viewer");
        var r = await c.WriteAsync([(Id("Batch.Count"), DataType.DINT, 1L)]);
        Assert.Equal(Status.NOT_PERMITTED, r[0].Status);
    }

    [Fact]
    public async Task SubscribeDeliversSnapshotAndChanges()
    {
        if (!rx.Available) return;
        await EnsureProjectAsync();
        await using var c = await Connected("panel1");
        var seen = new System.Collections.Concurrent.ConcurrentQueue<TagValue>();
        c.DataReceived += vs => { foreach (var v in vs) seen.Enqueue(v); };
        var subs = await c.SubscribeAsync([Id("Batch.Count"), 9999], 20);
        Assert.Equal([Status.OK, Status.BAD_TAG], subs.Select(s => s.Status));
        await WaitFor(() => seen.Any(v => v.TagId == Id("Batch.Count")));
        await c.WriteAsync([(Id("Batch.Count"), DataType.DINT, 777L)]);
        await WaitFor(() => seen.Any(v => v.TagId == Id("Batch.Count") && Equals(v.Value, 777L)));
    }

    [Fact]
    public async Task MomentaryPressAndRelease()
    {
        if (!rx.Available) return;
        await EnsureProjectAsync();
        await using var c = await Connected("panel1");
        var jog = Demo.Tag("Tank.Jog_Fwd")!;
        Assert.Equal(Status.INVALID, (await c.WriteAsync([(jog.TagId, DataType.BOOL, true)]))[0].Status);
        Assert.Equal(Status.OK, await c.MomentaryAsync(jog.TagId, DataType.BOOL, true, 500));
        Assert.Equal(true, (await c.ReadAsync([jog.TagId]))[0].Value);
        Assert.Equal(Status.OK, await c.MomentaryAsync(jog.TagId, DataType.BOOL, false, 0));
        Assert.Equal(false, (await c.ReadAsync([jog.TagId]))[0].Value);
    }

    [Fact]
    public async Task HeartbeatsKeepIdleSessionAlive()
    {
        if (!rx.Available) return;
        await using var c = await Connected("viewer");
        await Task.Delay(2500);
        Assert.True(c.Connected);
        await c.ProjectInfoAsync();
    }

    private static async Task WaitFor(Func<bool> cond, int timeoutMs = 3000)
    {
        var sw = Stopwatch.StartNew();
        while (!cond())
        {
            if (sw.ElapsedMilliseconds > timeoutMs) throw new TimeoutException("condition not met");
            await Task.Delay(10);
        }
    }
}
