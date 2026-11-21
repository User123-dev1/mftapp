using System.Text.Json;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Link;
using Whmi.Studio.Services;

namespace Whmi.Studio.ViewModels;

/// <summary>Connection to a receiver: download, upload, live monitoring and test writes.</summary>
public sealed partial class OnlineViewModel : SectionViewModel
{
    private readonly MainWindowViewModel _main;
    private readonly IDialogs _dialogs;
    private LinkClient? _client;
    private CompiledProject? _receiverProject;
    private readonly Dictionary<int, TagRow> _monitored = [];

    public OnlineViewModel(MainWindowViewModel main, IDialogs dialogs) : base("Online", "📡")
    {
        _main = main;
        _dialogs = dialogs;
        LoadSettings();
    }

    [ObservableProperty] private string _host = "192.168.50.1";
    [ObservableProperty] private int _port = Frames.DefaultPort;
    [ObservableProperty] private string _clientId = "studio";
    [ObservableProperty] private string _keyFile = "";
    [ObservableProperty] private string _keyHex = "";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsOffline))]
    [NotifyCanExecuteChangedFor(nameof(ConnectCommand), nameof(DisconnectCommand), nameof(DownloadCommand),
        nameof(UploadCommand), nameof(ToggleMonitorCommand), nameof(WriteCommand))]
    private bool _isConnected;

    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private bool _isMonitoring;
    [ObservableProperty] private string _status = "Not connected";
    [ObservableProperty] private string _receiverId = "";
    [ObservableProperty] private string _role = "";
    [ObservableProperty] private string _receiverProjectText = "";
    [ObservableProperty] private string _syncText = "";
    [ObservableProperty] private double _progress;
    [ObservableProperty] private string _writeTag = "";
    [ObservableProperty] private string _writeValue = "";

    public bool IsOffline => !IsConnected;

    public void OnProjectLoaded()
    {
        _monitored.Clear();
        if (IsConnected) _ = RefreshInfoAsync();
    }

    // ------------------------------------------------------------------ settings

    private static string SettingsPath =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "WhmiStudio", "online.json");

    private void LoadSettings()
    {
        try
        {
            if (!File.Exists(SettingsPath)) return;
            var s = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(SettingsPath));
            if (s is null) return;
            Host = s.GetValueOrDefault("host", Host);
            Port = int.TryParse(s.GetValueOrDefault("port"), out var p) ? p : Port;
            ClientId = s.GetValueOrDefault("client_id", ClientId);
            KeyFile = s.GetValueOrDefault("key_file", KeyFile);
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            // settings are a convenience only
        }
    }

    private void SaveSettings()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath)!);
            // The key itself is never stored; only the path to the key file.
            File.WriteAllText(SettingsPath, JsonSerializer.Serialize(new Dictionary<string, string>
            {
                ["host"] = Host, ["port"] = Port.ToString(), ["client_id"] = ClientId, ["key_file"] = KeyFile,
            }));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
    }

    // ------------------------------------------------------------------ connection

    private byte[] ResolveKey()
    {
        if (KeyHex.Trim().Length > 0) return LinkCrypto.ParseKey(KeyHex);
        if (KeyFile.Trim().Length > 0) return LinkCrypto.ParseKey(File.ReadAllText(KeyFile.Trim()));
        throw new FormatException("enter a key or choose the client's key file");
    }

    [RelayCommand]
    private async Task BrowseKeyAsync()
    {
        var path = await _dialogs.OpenFileAsync("Choose client key file", "Key file", "*.key");
        if (path is not null) KeyFile = path;
    }

    private bool CanConnect() => !IsConnected;

    [RelayCommand(CanExecute = nameof(CanConnect))]
    private async Task ConnectAsync()
    {
        IsBusy = true;
        Status = $"Connecting to {Host}:{Port}…";
        try
        {
            var client = new LinkClient(Host.Trim(), ClientId.Trim(), ResolveKey(), Port, ClientKind.STUDIO);
            await client.ConnectAsync();
            client.Disconnected += reason => Dispatcher.UIThread.Post(() => OnDisconnected(client, reason));
            client.ProjectChanged += hash => Dispatcher.UIThread.Post(() => _ = OnReceiverProjectChangedAsync());
            client.DataReceived += values => Dispatcher.UIThread.Post(() => OnData(values));
            _client = client;
            ReceiverId = client.ReceiverId;
            Role = client.Session!.Role.ToString().ToLowerInvariant();
            IsConnected = true;
            Status = $"Connected to {ReceiverId} as {ClientId} ({Role})";
            SaveSettings();
            await RefreshInfoAsync();
        }
        catch (Exception e) when (e is LinkException or AuthenticationException or ProtocolException or FormatException
                                      or IOException or System.Net.Sockets.SocketException or UnauthorizedAccessException)
        {
            Status = $"Connect failed: {e.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private bool CanUseLink() => IsConnected;

    [RelayCommand(CanExecute = nameof(CanUseLink))]
    private async Task DisconnectAsync()
    {
        var c = _client;
        _client = null;
        StopMonitoringState();
        IsConnected = false;
        Status = "Disconnected";
        if (c is not null) await c.DisposeAsync();
    }

    private void OnDisconnected(LinkClient client, Exception? reason)
    {
        if (_client != client) return;
        _client = null;
        StopMonitoringState();
        IsConnected = false;
        Status = $"Connection lost: {reason?.Message ?? "closed"}";
    }

    private async Task RefreshInfoAsync()
    {
        if (_client is null) return;
        try
        {
            var info = await _client.ProjectInfoAsync();
            if (info.Size == 0)
            {
                ReceiverProjectText = "(no project)";
                SyncText = "Receiver has no project yet: download one.";
                return;
            }
            ReceiverProjectText = $"{info.Name}  ({info.Size:N0} bytes, sha256 {Convert.ToHexString(info.Hash)[..12].ToLowerInvariant()})";
            CompiledProject? local = null;
            try { local = _main.Project.Build(); } catch (ProjectException) { }
            SyncText = local is null ? "Local project has errors; cannot compare."
                : local.Hash.AsSpan().SequenceEqual(info.Hash) ? "✔ Receiver runs this project."
                : "✖ Receiver runs a different version than this project.";
        }
        catch (LinkException e)
        {
            Status = $"Info failed: {e.Message}";
        }
    }

    // ------------------------------------------------------------------ project transfer

    [RelayCommand(CanExecute = nameof(CanUseLink))]
    private async Task DownloadAsync()
    {
        if (_client is null) return;
        var compiled = _main.TryBuild();
        if (compiled is null)
        {
            Status = "Download cancelled: fix the errors listed under Problems first.";
            return;
        }
        if (_client.Session!.Role < Core.Role.ENGINEER)
        {
            Status = $"Download needs the engineer role; {ClientId} is {Role}.";
            return;
        }
        if (!await _dialogs.ConfirmAsync("Download project",
                $"Download '{compiled.Name}' to receiver {ReceiverId}?\n\n"
                + "All local outputs switch OFF while the new project activates, and PLC connections restart."))
            return;
        IsBusy = true;
        Progress = 0;
        try
        {
            var msg = await _client.DownloadProjectAsync(compiled.Data, new Progress<double>(p => Progress = p));
            Status = $"Download OK: {msg}";
            await RefreshInfoAsync();
        }
        catch (LinkException e)
        {
            Status = $"Download failed: {e.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand(CanExecute = nameof(CanUseLink))]
    private async Task UploadAsync()
    {
        if (_client is null) return;
        if (_main.IsDirty && !await _dialogs.ConfirmAsync("Upload project",
                "Replace the open project (it has unsaved changes) with the project from the receiver?"))
            return;
        IsBusy = true;
        try
        {
            var compiled = CompiledProject.FromBytes(await _client.FetchProjectAsync());
            var project = compiled.Project;
            foreach (var t in project.Tags.Where(t => t.Source == TagSource.System).ToList()) project.Tags.Remove(t);
            foreach (var t in project.Tags) t.TagId = 0;
            _main.LoadProject(project, null);
            _main.IsDirty = true; // not saved to a file yet
            Status = $"Uploaded '{project.Name}' from {ReceiverId}";
        }
        catch (Exception e) when (e is LinkException or ProjectException or FormatException)
        {
            Status = $"Upload failed: {e.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    // ------------------------------------------------------------------ monitoring

    [RelayCommand(CanExecute = nameof(CanUseLink))]
    private async Task ToggleMonitorAsync()
    {
        if (IsMonitoring)
        {
            var ids = _monitored.Keys.ToList();
            StopMonitoringState();
            if (_client is not null && ids.Count > 0)
            {
                try { await _client.UnsubscribeAsync(ids); } catch (LinkException) { }
            }
            Status = "Monitoring stopped";
            return;
        }
        await StartMonitoringAsync();
    }

    /// <summary>
    /// Subscribe using the receiver's own compiled project for the tag ids, so values are matched by
    /// tag name even when the open project differs from what the receiver runs.
    /// </summary>
    private async Task StartMonitoringAsync()
    {
        if (_client is null) return;
        try
        {
            _receiverProject = CompiledProject.FromBytes(await _client.FetchProjectAsync());
        }
        catch (Exception e) when (e is LinkException or ProjectException or FormatException)
        {
            Status = $"Monitor failed: {e.Message}";
            return;
        }
        _monitored.Clear();
        foreach (var row in _main.Tags.AllRows)
        {
            row.LiveValue = "";
            row.LiveQuality = "not on receiver";
            if (_receiverProject.Tag(row.Name) is { } t) _monitored[t.TagId] = row;
        }
        try
        {
            await _client.SubscribeAsync(_monitored.Keys.ToList(), 100);
        }
        catch (LinkException e)
        {
            Status = $"Monitor failed: {e.Message}";
            return;
        }
        IsMonitoring = true;
        _main.Tags.ShowLive = true;
        Status = $"Monitoring {_monitored.Count} tags on {ReceiverId}";
    }

    private void StopMonitoringState()
    {
        IsMonitoring = false;
        _main.Tags.ShowLive = false;
        foreach (var row in _monitored.Values)
        {
            row.LiveValue = "";
            row.LiveQuality = "";
        }
        _monitored.Clear();
    }

    private async Task OnReceiverProjectChangedAsync()
    {
        await RefreshInfoAsync();
        if (IsMonitoring)
        {
            StopMonitoringState();
            await StartMonitoringAsync(); // tag ids may have changed
        }
    }

    public void OnData(IReadOnlyList<TagValue> values)
    {
        foreach (var v in values)
        {
            if (!_monitored.TryGetValue(v.TagId, out var row)) continue;
            row.LiveValue = DataTypes.Format(v.Value);
            row.LiveQuality = v.Quality == Quality.GOOD ? "good" : v.Quality.ToString().ToLowerInvariant();
        }
    }

    [RelayCommand(CanExecute = nameof(CanUseLink))]
    private async Task WriteAsync()
    {
        if (_client is null) return;
        _receiverProject ??= CompiledProject.FromBytes(await _client.FetchProjectAsync());
        var tag = _receiverProject.Tag(WriteTag.Trim());
        if (tag is null)
        {
            Status = $"Tag '{WriteTag}' does not exist on the receiver";
            return;
        }
        try
        {
            object? literal = tag.DataType == DataType.STRING ? WriteValue : DataTypes.ParseLiteral(WriteValue);
            object value = DataTypes.Coerce(tag.DataType, literal);
            var result = await _client.WriteAsync([(tag.TagId, tag.DataType, value)]);
            Status = $"{tag.Name} ← {DataTypes.Format(value)}: {result[0].Status}";
        }
        catch (CoercionException e)
        {
            Status = $"Invalid value for {tag.DataType}: {e.Message}";
        }
        catch (LinkException e)
        {
            Status = $"Write failed: {e.Message}";
        }
    }
}
