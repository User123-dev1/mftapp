using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net.Sockets;
using System.Security.Cryptography;
using Whmi.Core;

namespace Whmi.Link;

public sealed class AuthenticationException(string message) : Exception(message);

/// <summary>A request failed, or the link went down while it was outstanding.</summary>
public sealed class LinkException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>
/// WHMI-Link client (the C# counterpart of whmi.link.client.LinkClient).
/// Events are raised on a background thread; UI code must marshal to its dispatcher.
/// </summary>
public sealed class LinkClient : IAsyncDisposable
{
    private readonly string _host;
    private readonly int _port;
    private readonly string _clientId;
    private readonly byte[] _key;
    private readonly ClientKind _kind;
    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private readonly ConcurrentDictionary<int, TaskCompletionSource<object>> _pending = new();
    private readonly CancellationTokenSource _cts = new();
    private TcpClient? _tcp;
    private NetworkStream? _stream;
    private FrameCipher? _tx;
    private FrameCipher? _rx;
    private uint _txSeq;
    private uint _rxSeq;
    private int _nextReqId;
    private long _lastRx;
    private long _lastTx;
    private Task? _readerTask;
    private Task? _keepaliveTask;
    private int _closed;

    public LinkClient(string host, string clientId, byte[] key, int port = Frames.DefaultPort,
        ClientKind kind = ClientKind.STUDIO)
    {
        _host = host;
        _port = port;
        _clientId = clientId;
        _key = key;
        _kind = kind;
    }

    public TimeSpan RequestTimeout { get; set; } = TimeSpan.FromSeconds(5);
    public SessionInfo? Session { get; private set; }
    public string ReceiverId { get; private set; } = "";
    public bool Connected => _stream is not null && Volatile.Read(ref _closed) == 0;

    public event Action<IReadOnlyList<TagValue>>? DataReceived;
    public event Action<byte[]>? ProjectChanged;
    public event Action<Exception?>? Disconnected;

    // ------------------------------------------------------------------ lifecycle

    public async Task<SessionInfo> ConnectAsync(TimeSpan? timeout = null, CancellationToken ct = default)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(timeout ?? TimeSpan.FromSeconds(5));
        _tcp = new TcpClient { NoDelay = true };
        try
        {
            await _tcp.ConnectAsync(_host, _port, cts.Token);
            _stream = _tcp.GetStream();
            await HandshakeAsync(cts.Token);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            Abort();
            throw new LinkException($"timed out connecting to {_host}:{_port}");
        }
        catch
        {
            Abort();
            throw;
        }
        _readerTask = Task.Run(ReaderLoopAsync);
        _keepaliveTask = Task.Run(KeepaliveLoopAsync);
        return Session!;
    }

    private async Task HandshakeAsync(CancellationToken ct)
    {
        var clientNonce = RandomNumberGenerator.GetBytes(LinkCrypto.NonceSize);
        await SendAsync(MsgType.HELLO, new PayloadWriter().U8(Frames.ProtocolVersion).U8((byte)_kind).Str8(_clientId)
            .Raw(clientNonce).ToArray(), ct);

        var (type, payload) = await ReceiveAsync(ct);
        if (type != MsgType.CHALLENGE) throw new ProtocolException("expected CHALLENGE");
        var r = new PayloadReader(payload);
        ReceiverId = r.Str8();
        var serverNonce = r.Raw(LinkCrypto.NonceSize);
        r.Done();

        var ctx = LinkCrypto.AuthContext(clientNonce, serverNonce, _clientId, ReceiverId);
        await SendAsync(MsgType.AUTH, LinkCrypto.ClientMac(_key, ctx), ct);
        (type, payload) = await ReceiveAsync(ct);
        if (type == MsgType.AUTH_FAIL) throw new AuthenticationException("receiver rejected the credentials");
        if (type != MsgType.AUTH_OK) throw new ProtocolException("expected AUTH_OK");
        if (!CryptographicOperations.FixedTimeEquals(payload, LinkCrypto.ServerMac(_key, ctx)))
            throw new AuthenticationException("receiver failed to prove knowledge of the key (possible impostor)");

        var (c2s, s2c) = LinkCrypto.DeriveKeys(_key, clientNonce, serverNonce);
        _tx = new FrameCipher(c2s, "C2S");
        _rx = new FrameCipher(s2c, "S2C");
        _txSeq = 1; // encrypted frames start at seq 1 in each direction
        _rxSeq = 1;

        (type, payload) = await ReceiveAsync(ct);
        if (type != MsgType.SESSION_INFO) throw new ProtocolException("expected SESSION_INFO");
        Session = SessionInfo.Decode(payload);
    }

    public async ValueTask DisposeAsync()
    {
        Close(null, raise: false);
        foreach (var t in new[] { _readerTask, _keepaliveTask })
        {
            if (t is null) continue;
            try { await t; } catch { /* already reported */ }
        }
        _sendLock.Dispose();
        _cts.Dispose();
    }

    private void Abort()
    {
        try { _tcp?.Close(); } catch { /* ignore */ }
    }

    private void Close(Exception? reason, bool raise = true)
    {
        if (Interlocked.Exchange(ref _closed, 1) != 0) return;
        try { _cts.Cancel(); } catch (ObjectDisposedException) { }
        Abort();
        foreach (var kv in _pending)
            kv.Value.TrySetException(new LinkException($"link lost: {reason?.Message ?? "closed"}", reason));
        _pending.Clear();
        _tx?.Dispose();
        _rx?.Dispose();
        if (raise) Disconnected?.Invoke(reason);
    }

    // ------------------------------------------------------------------ framing

    private static long Now => Stopwatch.GetTimestamp();

    private async Task SendAsync(MsgType type, byte[] payload, CancellationToken ct)
    {
        if (payload.Length > Frames.MaxPlaintext) throw new ArgumentException($"payload too large ({payload.Length} bytes)");
        await _sendLock.WaitAsync(ct);
        try
        {
            if (_stream is null || Volatile.Read(ref _closed) != 0) throw new LinkException("link closed");
            uint seq = _txSeq++;
            byte[] frame;
            if (_tx is null)
            {
                if (!Frames.IsHandshake(type)) throw new InvalidOperationException($"refusing to send {type} unencrypted");
                frame = [.. Frames.Header(type, 0, seq, payload.Length), .. payload];
            }
            else
            {
                var header = Frames.Header(type, Frames.FlagEncrypted, seq, payload.Length + Frames.GcmTagSize);
                frame = [.. header, .. _tx.Seal(seq, header, payload)];
            }
            await _stream.WriteAsync(frame, ct);
            Interlocked.Exchange(ref _lastTx, Now);
        }
        finally
        {
            _sendLock.Release();
        }
    }

    private async Task<(MsgType, byte[])> ReceiveAsync(CancellationToken ct)
    {
        var header = new byte[Frames.HeaderSize];
        await _stream!.ReadExactlyAsync(header, ct);
        var (type, flags, seq, length) = Frames.ParseHeader(header);
        var body = new byte[length];
        await _stream.ReadExactlyAsync(body, ct);
        if (seq != _rxSeq) throw new ProtocolException($"sequence error: expected {_rxSeq}, got {seq}");
        _rxSeq++;
        bool encrypted = (flags & Frames.FlagEncrypted) != 0;
        byte[] payload;
        if (_rx is null)
        {
            if (encrypted) throw new ProtocolException("unexpected encrypted frame during handshake");
            payload = body;
        }
        else
        {
            if (!encrypted) throw new ProtocolException("unencrypted frame in an encrypted session");
            payload = _rx.Open(seq, header, body);
        }
        if (!Enum.IsDefined(typeof(MsgType), type)) throw new ProtocolException($"unknown message type 0x{type:X2}");
        var mtype = (MsgType)type;
        if (_rx is null && !Frames.IsHandshake(mtype)) throw new ProtocolException($"{mtype} not allowed before authentication");
        Interlocked.Exchange(ref _lastRx, Now);
        return (mtype, payload);
    }

    // ------------------------------------------------------------------ background

    private async Task ReaderLoopAsync()
    {
        Exception? reason = null;
        try
        {
            while (!_cts.IsCancellationRequested)
            {
                var (type, payload) = await ReceiveAsync(_cts.Token);
                Dispatch(type, payload);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e) when (e is IOException or SocketException or EndOfStreamException or ProtocolException
                                      or ObjectDisposedException or InvalidOperationException)
        {
            reason = e;
        }
        Close(reason);
    }

    private void Dispatch(MsgType type, byte[] payload)
    {
        switch (type)
        {
            case MsgType.HEARTBEAT:
                return;
            case MsgType.DATA_UPDATE:
            {
                var r = new PayloadReader(payload);
                int n = r.U16();
                var values = new List<TagValue>(n);
                for (int i = 0; i < n; i++) values.Add(TagValue.Read(r));
                r.Done();
                DataReceived?.Invoke(values);
                return;
            }
            case MsgType.PROJECT_CHANGED:
                ProjectChanged?.Invoke(payload);
                return;
            case MsgType.ERROR:
            {
                var r = new PayloadReader(payload);
                Trace.TraceWarning($"receiver reported error {r.U16()}: {r.Str16()}");
                return;
            }
            case MsgType.READ_RSP:
            {
                var r = new PayloadReader(payload);
                int req = r.U16();
                int n = r.U16();
                var values = new List<TagValue>(n);
                for (int i = 0; i < n; i++) values.Add(TagValue.Read(r));
                r.Done();
                Resolve(req, values);
                return;
            }
            case MsgType.WRITE_RSP or MsgType.SUB_RSP:
            {
                var r = new PayloadReader(payload);
                int req = r.U16();
                int n = r.U16();
                var list = new List<(int, Status)>(n);
                for (int i = 0; i < n; i++) list.Add((r.U16(), ToStatus(r.U8())));
                r.Done();
                Resolve(req, list);
                return;
            }
            case MsgType.PROJECT_INFO:
            {
                var r = new PayloadReader(payload);
                int req = r.U16();
                var result = new ProjectInfo(r.Raw(32), r.U32(), r.Str8());
                r.Done();
                Resolve(req, result);
                return;
            }
            case MsgType.PROJECT_DATA:
            {
                var r = new PayloadReader(payload);
                int req = r.U16();
                var result = (ToStatus(r.U8()), r.U32(), r.Blob16());
                r.Done();
                Resolve(req, result);
                return;
            }
            case MsgType.PROJECT_WRITE_RSP:
            {
                var r = new PayloadReader(payload);
                int req = r.U16();
                var result = (ToStatus(r.U8()), r.Str16());
                r.Done();
                Resolve(req, result);
                return;
            }
            default:
                throw new ProtocolException($"unexpected {type} from receiver");
        }
    }

    private static Status ToStatus(byte b) => Enum.IsDefined(typeof(Status), b) ? (Status)b : Status.INTERNAL;

    private void Resolve(int reqId, object result)
    {
        if (_pending.TryRemove(reqId, out var tcs)) tcs.TrySetResult(result);
    }

    private async Task KeepaliveLoopAsync()
    {
        var hb = TimeSpan.FromMilliseconds(Session!.HeartbeatMs);
        var wd = TimeSpan.FromMilliseconds(Session.WatchdogMs);
        var tick = TimeSpan.FromTicks(Math.Min(hb.Ticks, wd.Ticks) / 4);
        try
        {
            while (!_cts.IsCancellationRequested)
            {
                await Task.Delay(tick, _cts.Token);
                if (Stopwatch.GetElapsedTime(Interlocked.Read(ref _lastRx)) > wd)
                {
                    Close(new LinkException("receiver watchdog expired"));
                    return;
                }
                if (Stopwatch.GetElapsedTime(Interlocked.Read(ref _lastTx)) >= hb)
                    await SendAsync(MsgType.HEARTBEAT, [], _cts.Token);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e) when (e is IOException or SocketException or LinkException or ObjectDisposedException)
        {
            Close(e);
        }
    }

    // ------------------------------------------------------------------ requests

    private async Task<T> RequestAsync<T>(MsgType type, Func<int, byte[]> build)
    {
        if (!Connected) throw new LinkException("not connected");
        int reqId;
        TaskCompletionSource<object> tcs;
        do
        {
            reqId = Interlocked.Increment(ref _nextReqId) % 0xFFFF + 1;
            tcs = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
        } while (!_pending.TryAdd(reqId, tcs));
        try
        {
            try
            {
                await SendAsync(type, build(reqId), _cts.Token);
            }
            catch (Exception e) when (e is IOException or SocketException or OperationCanceledException)
            {
                throw new LinkException(e.Message, e);
            }
            var completed = await Task.WhenAny(tcs.Task, Task.Delay(RequestTimeout));
            if (completed != tcs.Task) throw new LinkException($"{type} timed out");
            return (T)await tcs.Task;
        }
        finally
        {
            _pending.TryRemove(reqId, out _);
        }
    }

    private static byte[] IdList(int reqId, IReadOnlyList<int> ids, int? intervalMs = null)
    {
        var w = new PayloadWriter().U16(reqId);
        if (intervalMs is { } i) w.U16(i);
        w.U16(ids.Count);
        foreach (var id in ids) w.U16(id);
        return w.ToArray();
    }

    public async Task<IReadOnlyList<TagValue>> ReadAsync(IReadOnlyList<int> tagIds)
    {
        var all = new List<TagValue>();
        foreach (var chunk in tagIds.Chunk(Frames.MaxReadTags))
            all.AddRange(await RequestAsync<List<TagValue>>(MsgType.READ_REQ, id => IdList(id, chunk)));
        return all;
    }

    public async Task<IReadOnlyList<(int TagId, Status Status)>> WriteAsync(
        IReadOnlyList<(int TagId, DataType Type, object Value)> writes)
    {
        var all = new List<(int, Status)>();
        foreach (var chunk in writes.Chunk(Frames.MaxReadTags))
        {
            all.AddRange(await RequestAsync<List<(int, Status)>>(MsgType.WRITE_REQ, id =>
            {
                var w = new PayloadWriter().U16(id).U16(chunk.Length);
                foreach (var (tagId, type, value) in chunk) w.U16(tagId).Value(type, value);
                return w.ToArray();
            }));
        }
        return all;
    }

    public async Task<Status> MomentaryAsync(int tagId, DataType type, object value, int holdMs)
    {
        var result = await RequestAsync<List<(int, Status)>>(MsgType.MOMENTARY, id =>
            new PayloadWriter().U16(id).U16(tagId).Value(type, value).U16(holdMs).ToArray());
        return result.Count > 0 ? result[0].Item2 : Status.INTERNAL;
    }

    public async Task<IReadOnlyList<(int TagId, Status Status)>> SubscribeAsync(IReadOnlyList<int> tagIds, int intervalMs = 100)
    {
        var all = new List<(int, Status)>();
        foreach (var chunk in tagIds.Chunk(Frames.MaxSubscribeTags))
            all.AddRange(await RequestAsync<List<(int, Status)>>(MsgType.SUBSCRIBE, id => IdList(id, chunk, intervalMs)));
        return all;
    }

    public async Task UnsubscribeAsync(IReadOnlyList<int> tagIds)
    {
        foreach (var chunk in tagIds.Chunk(Frames.MaxSubscribeTags))
            await RequestAsync<List<(int, Status)>>(MsgType.UNSUBSCRIBE, id => IdList(id, chunk));
    }

    public sealed record ProjectInfo(byte[] Hash, uint Size, string Name);

    public Task<ProjectInfo> ProjectInfoAsync() =>
        RequestAsync<ProjectInfo>(MsgType.PROJECT_INFO_REQ, id => new PayloadWriter().U16(id).ToArray());

    /// <summary>Upload the compiled project from the receiver, verifying its hash.</summary>
    public async Task<byte[]> FetchProjectAsync()
    {
        var info = await ProjectInfoAsync();
        if (info.Size == 0) throw new LinkException("receiver has no project");
        var buf = new MemoryStream();
        while (buf.Length < info.Size)
        {
            uint offset = (uint)buf.Length;
            int length = (int)Math.Min(Frames.ProjectChunk, info.Size - offset);
            var (status, off, data) = await RequestAsync<(Status, uint, byte[])>(MsgType.PROJECT_READ,
                id => new PayloadWriter().U16(id).U32(offset).U16(length).ToArray());
            if (status != Status.OK || off != offset || data.Length == 0)
                throw new LinkException($"project read failed at offset {offset}: {status}");
            buf.Write(data);
        }
        var bytes = buf.ToArray();
        if (!SHA256.HashData(bytes).AsSpan().SequenceEqual(info.Hash))
            throw new LinkException("project hash mismatch (project changed during transfer?)");
        return bytes;
    }

    /// <summary>Download and activate a compiled project (needs the engineer role).</summary>
    public async Task<string> DownloadProjectAsync(byte[] data, IProgress<double>? progress = null)
    {
        var digest = SHA256.HashData(data);

        async Task<string> Step(MsgType type, Func<int, byte[]> build)
        {
            var (status, msg) = await RequestAsync<(Status, string)>(type, build);
            if (status != Status.OK) throw new LinkException($"{type} failed: {status} {msg}".Trim());
            return msg;
        }

        await Step(MsgType.PROJECT_WRITE_BEGIN, id => new PayloadWriter().U16(id).U32(data.Length).Raw(digest).ToArray());
        for (int offset = 0; offset < data.Length; offset += Frames.ProjectChunk)
        {
            int o = offset;
            var chunk = data.AsSpan(o, Math.Min(Frames.ProjectChunk, data.Length - o)).ToArray();
            await Step(MsgType.PROJECT_WRITE_CHUNK, id => new PayloadWriter().U16(id).U32(o).Blob16(chunk).ToArray());
            progress?.Report((double)(o + chunk.Length) / data.Length);
        }
        var old = RequestTimeout;
        RequestTimeout = TimeSpan.FromSeconds(Math.Max(old.TotalSeconds, 15)); // activation reconnects PLCs etc.
        try
        {
            return await Step(MsgType.PROJECT_WRITE_COMMIT, id => new PayloadWriter().U16(id).ToArray());
        }
        finally
        {
            RequestTimeout = old;
        }
    }
}
