using System.Net.Sockets;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Link;

namespace Whmi.Runtime;

public enum LinkState { Disconnected, Connecting, Syncing, Online, AuthFailed, NoProject }

public sealed record CachedValue(object Value, Quality Quality, long TimestampMs)
{
    public bool Good => Quality == Quality.GOOD;
}

public sealed record PanelConnection(string Host, int Port, string ClientId, byte[] Key);

/// <summary>
/// The panel's engine (C# counterpart of whmi.runtime.engine.HmiRuntime): keeps a link to the receiver,
/// syncs the project, maintains a quality-aware tag cache, and performs writes and momentary presses.
/// <para>
/// State changes and events are delivered through the <c>dispatch</c> callback given to the
/// constructor (the UI thread in the panel app). Without one, they run inline under a lock.
/// </para>
/// </summary>
public sealed class HmiRuntime : IAsyncDisposable
{
    public static TimeSpan ReconnectMin { get; set; } = TimeSpan.FromMilliseconds(500);
    public static TimeSpan ReconnectMax { get; set; } = TimeSpan.FromSeconds(5);
    public static TimeSpan AuthRetry { get; set; } = TimeSpan.FromSeconds(10);

    private readonly PanelConnection _conn;
    private readonly string? _cacheDir;
    private readonly int _intervalMs;
    private readonly Action<Action>? _dispatch;
    private readonly object _gate = new();
    private readonly Dictionary<int, CancellationTokenSource> _holds = [];
    private readonly CancellationTokenSource _stop = new();
    private Task? _runTask;
    private LinkClient? _client;
    private TaskCompletionSource _projectChanged = NewSignal();

    public HmiRuntime(PanelConnection connection, string? cacheDir = null, int intervalMs = 100,
        Action<Action>? dispatch = null)
    {
        _conn = connection;
        _cacheDir = cacheDir;
        _intervalMs = intervalMs;
        _dispatch = dispatch;
        LoadCachedProject();
    }

    public LinkState State { get; private set; } = LinkState.Disconnected;
    public string LastError { get; private set; } = "";
    public string ReceiverId { get; private set; } = "";
    public Role? Role { get; private set; }
    public CompiledProject? Project { get; private set; }
    public Dictionary<int, CachedValue> Values { get; } = [];
    public bool CanWrite => State == LinkState.Online && Role is { } r && r.CanWrite();

    public event Action<LinkState>? StateChanged;
    public event Action<Tag, CachedValue>? ValueChanged;
    public event Action<CompiledProject>? ProjectLoaded;

    private static TaskCompletionSource NewSignal() => new(TaskCreationOptions.RunContinuationsAsynchronously);

    private void Post(Action action)
    {
        if (_dispatch is null)
        {
            lock (_gate) action();
        }
        else
        {
            _dispatch(action);
        }
    }

    // ------------------------------------------------------------------ public API

    public void Start() => _runTask ??= Task.Run(() => RunAsync(_stop.Token));

    public async ValueTask DisposeAsync()
    {
        _stop.Cancel();
        CancelHolds();
        if (_client is { } c) await c.DisposeAsync();
        if (_runTask is not null)
        {
            try { await _runTask; } catch (OperationCanceledException) { }
        }
        _stop.Dispose();
    }

    public Tag? FindTag(string? name) => name is null ? null : Project?.Tag(name);

    public CachedValue Get(Tag tag)
    {
        lock (_gate)
            return Values.TryGetValue(tag.TagId, out var v) ? v : new CachedValue(tag.DataType.Zero(), Quality.BAD_NOT_CONNECTED, 0);
    }

    public CachedValue? Get(string name) => FindTag(name) is { } t ? Get(t) : null;

    public async Task<Status> WriteAsync(string name, object value)
    {
        if (FindTag(name) is not { } tag) return Status.BAD_TAG;
        if (OnlineClient() is not { } client) return Status.COMM_FAILURE;
        try
        {
            var result = await client.WriteAsync([(tag.TagId, tag.DataType, value)]);
            return result.Count > 0 ? result[0].Status : Status.INTERNAL;
        }
        catch (CoercionException e)
        {
            return e.OutOfRange ? Status.OUT_OF_RANGE : Status.TYPE_MISMATCH;
        }
        catch (LinkException)
        {
            return Status.COMM_FAILURE;
        }
    }

    /// <summary>Assert a momentary tag. It is re-asserted every heartbeat until <see cref="ReleaseAsync"/> or link loss.</summary>
    public async Task<Status> PressAsync(string name, object? value = null)
    {
        if (FindTag(name) is not { } tag) return Status.BAD_TAG;
        if (!tag.Momentary) return Status.INVALID;
        if (OnlineClient() is not { } client || client.Session is not { } session) return Status.COMM_FAILURE;
        value ??= tag.DataType == DataType.BOOL ? true : 1L;
        int hb = session.HeartbeatMs;
        int holdMs = Math.Min(Core.Model.Project.MomentaryMaxHoldMs, Math.Max(3 * hb, 300));
        Status status;
        try
        {
            status = await client.MomentaryAsync(tag.TagId, tag.DataType, value, holdMs);
        }
        catch (Exception e) when (e is LinkException or CoercionException)
        {
            return Status.COMM_FAILURE;
        }
        if (status != Status.OK) return status;
        var cts = CancellationTokenSource.CreateLinkedTokenSource(_stop.Token);
        lock (_holds)
        {
            if (_holds.Remove(tag.TagId, out var old)) old.Cancel();
            _holds[tag.TagId] = cts;
        }
        _ = RefreshHoldAsync(client, tag, value, holdMs, TimeSpan.FromMilliseconds(hb), cts);
        return Status.OK;
    }

    public async Task<Status> ReleaseAsync(string name)
    {
        if (FindTag(name) is not { } tag) return Status.BAD_TAG;
        lock (_holds)
        {
            if (_holds.Remove(tag.TagId, out var cts)) cts.Cancel();
        }
        if (_client is not { Connected: true } client)
            return Status.COMM_FAILURE; // the receiver releases on its own when the hold expires
        try
        {
            return await client.MomentaryAsync(tag.TagId, tag.DataType, ReleaseValue(tag), 0);
        }
        catch (Exception e) when (e is LinkException or CoercionException)
        {
            return Status.COMM_FAILURE;
        }
    }

    /// <summary>Release every held button (window lost focus, app going to sleep, screen change).</summary>
    public async Task ReleaseAllAsync()
    {
        List<int> held;
        lock (_holds) held = [.. _holds.Keys];
        foreach (var id in held)
        {
            if (Project?.TagsById.GetValueOrDefault(id) is { } tag) await ReleaseAsync(tag.Name);
        }
    }

    public bool IsHeld(Tag tag)
    {
        lock (_holds) return _holds.ContainsKey(tag.TagId);
    }

    private static object ReleaseValue(Tag tag) =>
        tag.ReleaseValue is { } v ? DataTypes.Coerce(tag.DataType, v) : tag.DataType.Zero();

    private LinkClient? OnlineClient() =>
        State == LinkState.Online && _client is { Connected: true } c ? c : null;

    private async Task RefreshHoldAsync(LinkClient client, Tag tag, object value, int holdMs, TimeSpan period,
        CancellationTokenSource cts)
    {
        try
        {
            while (!cts.IsCancellationRequested)
            {
                await Task.Delay(period, cts.Token);
                if (!client.Connected) return;
                var status = await client.MomentaryAsync(tag.TagId, tag.DataType, value, holdMs);
                if (status != Status.OK) return;
            }
        }
        catch (Exception e) when (e is OperationCanceledException or LinkException) { }
        finally
        {
            lock (_holds)
            {
                if (_holds.TryGetValue(tag.TagId, out var current) && current == cts) _holds.Remove(tag.TagId);
            }
            cts.Dispose();
        }
    }

    private void CancelHolds()
    {
        lock (_holds)
        {
            foreach (var cts in _holds.Values) cts.Cancel();
            _holds.Clear();
        }
    }

    // ------------------------------------------------------------------ state

    private void SetState(LinkState state, string error = "")
    {
        Post(() =>
        {
            LastError = error;
            if (State == state) return;
            State = state;
            StateChanged?.Invoke(state);
        });
    }

    private void LoadCachedProject()
    {
        if (_cacheDir is null) return;
        var path = Path.Combine(_cacheDir, "project.whc");
        if (!File.Exists(path)) return;
        try
        {
            SetProject(CompiledProject.Load(path));
        }
        catch (Exception e) when (e is IOException or FormatException or ProjectException or System.Text.Json.JsonException)
        {
            // a damaged cache is simply ignored; the receiver will send the project again
        }
    }

    private void SetProject(CompiledProject project)
    {
        Project = project;
        Values.Clear();
        foreach (var t in project.Project.Tags)
            Values[t.TagId] = new CachedValue(t.DataType.Zero(), Quality.BAD_NOT_CONNECTED, 0);
        ProjectLoaded?.Invoke(project);
    }

    private void ApplyValues(IReadOnlyList<TagValue> values)
    {
        if (Project is not { } project) return;
        foreach (var v in values)
        {
            if (!project.TagsById.TryGetValue(v.TagId, out var tag)) continue; // update from an older project
            var cv = new CachedValue(v.Value, v.Quality, v.TimestampMs);
            Values[v.TagId] = cv;
            ValueChanged?.Invoke(tag, cv);
        }
    }

    private void MarkDisconnected()
    {
        CancelHolds();
        Post(() =>
        {
            if (Project is not { } project) return;
            foreach (var (id, cv) in Values.ToList())
            {
                if (cv.Quality == Quality.BAD_NOT_CONNECTED) continue;
                var stale = cv with { Quality = Quality.BAD_NOT_CONNECTED };
                Values[id] = stale;
                if (project.TagsById.TryGetValue(id, out var tag)) ValueChanged?.Invoke(tag, stale);
            }
        });
    }

    // ------------------------------------------------------------------ connection loop

    private async Task RunAsync(CancellationToken ct)
    {
        var delay = ReconnectMin;
        while (!ct.IsCancellationRequested)
        {
            SetState(LinkState.Connecting);
            var client = new LinkClient(_conn.Host, _conn.ClientId, _conn.Key, _conn.Port, ClientKind.PANEL);
            var closed = new TaskCompletionSource<Exception?>(TaskCreationOptions.RunContinuationsAsynchronously);
            client.Disconnected += reason => closed.TrySetResult(reason);
            client.DataReceived += values => Post(() => ApplyValues(values));
            client.ProjectChanged += _ => _projectChanged.TrySetResult();
            try
            {
                await client.ConnectAsync(TimeSpan.FromSeconds(5), ct);
                _client = client;
                ReceiverId = client.ReceiverId;
                Role = client.Session!.Role;
                delay = ReconnectMin;
                while (!ct.IsCancellationRequested)
                {
                    _projectChanged = NewSignal();
                    var changed = _projectChanged.Task;
                    if (await SyncProjectAsync(client)) SetState(LinkState.Online);
                    var done = await Task.WhenAny(closed.Task, changed, Task.Delay(Timeout.Infinite, ct));
                    ct.ThrowIfCancellationRequested();
                    if (done == closed.Task) throw new LinkException($"link lost: {closed.Task.Result?.Message ?? "closed"}");
                    MarkDisconnected(); // values are stale until the new project is subscribed
                }
            }
            catch (AuthenticationException e)
            {
                SetState(LinkState.AuthFailed, e.Message);
                delay = AuthRetry;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                break;
            }
            catch (Exception e) when (e is LinkException or ProtocolException or IOException or SocketException
                                          or FormatException or ProjectException or System.Text.Json.JsonException
                                          or OperationCanceledException)
            {
                SetState(LinkState.Disconnected, e.Message);
            }
            finally
            {
                _client = null;
                MarkDisconnected();
                await client.DisposeAsync();
            }
            try
            {
                await Task.Delay(delay, ct);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            if (delay < AuthRetry) delay = TimeSpan.FromTicks(Math.Min(delay.Ticks * 2, ReconnectMax.Ticks));
        }
        SetState(LinkState.Disconnected);
    }

    private async Task<bool> SyncProjectAsync(LinkClient client)
    {
        SetState(LinkState.Syncing);
        var info = await client.ProjectInfoAsync();
        if (info.Size == 0)
        {
            SetState(LinkState.NoProject, "receiver has no project");
            return false;
        }
        var project = Project;
        if (project is null || !project.Hash.AsSpan().SequenceEqual(info.Hash))
        {
            project = CompiledProject.FromBytes(await client.FetchProjectAsync());
            if (_cacheDir is not null)
            {
                Directory.CreateDirectory(_cacheDir);
                project.Save(Path.Combine(_cacheDir, "project.whc"));
            }
            var loaded = project;
            Post(() => SetProject(loaded));
        }
        await client.SubscribeAsync(project.Project.Tags.Select(t => t.TagId).ToList(), _intervalMs);
        return true;
    }
}
