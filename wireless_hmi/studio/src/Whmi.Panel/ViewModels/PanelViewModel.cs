using System.Globalization;
using Avalonia.Media;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Panel.Platform;
using Whmi.Runtime;

namespace Whmi.Panel.ViewModels;

public sealed partial class PanelViewModel : ObservableObject, IAsyncDisposable
{
    private readonly string _dataDir;
    private readonly IDeviceStatus _device;
    private readonly Action<Action> _dispatch;
    private readonly Dictionary<string, ScreenViewModel> _screens = [];
    private readonly Dictionary<int, List<WidgetViewModel>> _byTag = [];
    private DateTime _toastUntil;

    public PanelViewModel(string dataDir, IDeviceStatus device, Action<Action>? dispatch = null)
    {
        _dataDir = dataDir;
        _device = device;
        _dispatch = dispatch ?? (a => Dispatcher.UIThread.Post(a));
        Settings = PanelSettings.Load(dataDir);
    }

    public PanelSettings Settings { get; private set; }
    public HmiRuntime? Runtime { get; private set; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ScreenName), nameof(HasScreen))]
    private ScreenViewModel? _currentScreen;

    [ObservableProperty] private string _stateText = "Not paired";
    [ObservableProperty] private IBrush _stateBrush = Brushes.Gray;
    [ObservableProperty] private string _receiverText = "";
    [ObservableProperty] private bool _showCommLost = true;
    [ObservableProperty] private string _commLostTitle = "NOT CONNECTED";
    [ObservableProperty] private string _commLostDetail = "";
    [ObservableProperty] private string _clock = "";
    [ObservableProperty] private string _batteryText = "";
    [ObservableProperty] private double _batteryFill;
    [ObservableProperty] private IBrush _batteryBrush = Brushes.LimeGreen;
    [ObservableProperty] private bool _hasBattery;
    [ObservableProperty] private bool _lowBattery;
    [ObservableProperty] private string _wifiText = "";
    [ObservableProperty] private bool _hasWifi;
    [ObservableProperty] private int _wifiBars;
    [ObservableProperty] private string _toast = "";
    [ObservableProperty] private KeypadViewModel? _keypad;
    [ObservableProperty] private SettingsViewModel? _settingsPage;

    public string ScreenName => CurrentScreen?.Name ?? "";
    public bool HasScreen => CurrentScreen is not null;
    public bool HasToast => Toast.Length > 0;
    public bool CanWrite => Runtime?.CanWrite ?? false;
    public double Bar1 => WifiBars >= 1 ? 1 : 0.25;
    public double Bar2 => WifiBars >= 2 ? 1 : 0.25;
    public double Bar3 => WifiBars >= 3 ? 1 : 0.25;
    public double Bar4 => WifiBars >= 4 ? 1 : 0.25;

    public double BatteryFillWidth => 22 * BatteryFill;

    partial void OnToastChanged(string value) => OnPropertyChanged(nameof(HasToast));

    partial void OnBatteryFillChanged(double value) => OnPropertyChanged(nameof(BatteryFillWidth));

    partial void OnWifiBarsChanged(int value)
    {
        OnPropertyChanged(nameof(Bar1));
        OnPropertyChanged(nameof(Bar2));
        OnPropertyChanged(nameof(Bar3));
        OnPropertyChanged(nameof(Bar4));
    }

    /// <summary>Raised when the operator exits the app from the (PIN protected) settings page.</summary>
    public event Action? ExitRequested;

    // ------------------------------------------------------------------ lifecycle

    public void Start()
    {
        Tick();
        if (Settings.IsPaired) StartRuntime();
        else OpenSettingsPage();
    }

    public async ValueTask DisposeAsync()
    {
        if (Runtime is { } rt)
        {
            Runtime = null;
            await rt.DisposeAsync();
        }
    }

    public async void StartRuntime()
    {
        await DisposeAsync();
        var key = Settings.GetKey();
        if (key is null)
        {
            SetCommLost("NOT PAIRED", "Open Settings and enter this panel's key.");
            return;
        }
        var rt = new HmiRuntime(new PanelConnection(Settings.Host, Settings.Port, Settings.ClientId, key),
            Path.Combine(_dataDir, "cache"), 100, _dispatch);
        rt.StateChanged += OnStateChanged;
        rt.ProjectLoaded += OnProjectLoaded;
        rt.ValueChanged += OnValueChanged;
        Runtime = rt;
        ReceiverText = Settings.Host;
        if (rt.Project is { } cached) OnProjectLoaded(cached);
        OnStateChanged(rt.State);
        rt.Start();
    }

    // ------------------------------------------------------------------ runtime events (UI thread)

    private void OnProjectLoaded(CompiledProject project)
    {
        var previous = CurrentScreen?.Name;
        _screens.Clear();
        _byTag.Clear();
        foreach (var s in project.Project.Screens)
        {
            var vm = new ScreenViewModel(s, this);
            _screens[s.Name] = vm;
            foreach (var w in vm.Widgets)
            {
                if (w.Tag is null) continue;
                if (!_byTag.TryGetValue(w.Tag.TagId, out var list)) _byTag[w.Tag.TagId] = list = [];
                list.Add(w);
                w.Refresh(Runtime!.Get(w.Tag));
            }
        }
        var start = previous is not null && _screens.ContainsKey(previous) ? previous : project.Project.StartScreen;
        CurrentScreen = _screens.GetValueOrDefault(start) ?? _screens.Values.FirstOrDefault();
        Keypad = null;
    }

    private void OnValueChanged(Tag tag, CachedValue value)
    {
        if (!_byTag.TryGetValue(tag.TagId, out var widgets)) return;
        foreach (var w in widgets) w.Refresh(value);
    }

    private void OnStateChanged(LinkState state)
    {
        var rt = Runtime;
        var name = rt?.ReceiverId is { Length: > 0 } id ? id : Settings.Host;
        ReceiverText = name;
        (StateText, StateBrush) = state switch
        {
            LinkState.Online => ("Online", (IBrush)Brushes.LimeGreen),
            LinkState.Connecting => ("Connecting…", Brushes.Orange),
            LinkState.Syncing => ("Loading project…", Brushes.Orange),
            LinkState.NoProject => ("No project", Brushes.Orange),
            LinkState.AuthFailed => ("Key rejected", Brushes.Red),
            _ => ("Offline", Brushes.Red),
        };
        switch (state)
        {
            case LinkState.Online:
                ShowCommLost = false;
                break;
            case LinkState.AuthFailed:
                SetCommLost("KEY REJECTED", $"Receiver {name} does not accept this panel's key. Check Settings.");
                break;
            case LinkState.NoProject:
                SetCommLost("NO PROJECT", $"Download a project to {name} from WHMI Studio.");
                break;
            case LinkState.Syncing:
                SetCommLost("LOADING PROJECT", $"Receiving the project from {name}…");
                break;
            default:
                SetCommLost("COMMUNICATION LOST", $"Reconnecting to {name} ({Settings.Host}:{Settings.Port})… {Explain(rt?.LastError)}".Trim());
                break;
        }
        if (state != LinkState.Online) Keypad = null;
        // enabled state of write widgets depends on the link
        foreach (var w in _screens.Values.SelectMany(s => s.Widgets)) w.Refresh(w.Value ?? NotConnected(w));
    }

    /// <summary>Operator-friendly wording for low-level connection errors.</summary>
    public static string Explain(string? error) => error switch
    {
        null or "" => "",
        _ when error.Contains("end of the stream", StringComparison.OrdinalIgnoreCase)
               || error.Contains("reset", StringComparison.OrdinalIgnoreCase) => "The receiver closed the connection.",
        _ when error.Contains("refused", StringComparison.OrdinalIgnoreCase) => "The receiver is not answering.",
        _ when error.Contains("timed out", StringComparison.OrdinalIgnoreCase)
               || error.Contains("watchdog", StringComparison.OrdinalIgnoreCase) => "No answer from the receiver (out of WiFi range?).",
        _ when error.Contains("unreachable", StringComparison.OrdinalIgnoreCase)
               || error.Contains("No such host", StringComparison.OrdinalIgnoreCase) => "Receiver not reachable: check WiFi.",
        _ => error,
    };

    private static CachedValue NotConnected(WidgetViewModel w) =>
        new(w.Tag?.DataType.Zero() ?? 0L, Quality.BAD_NOT_CONNECTED, 0);

    private void SetCommLost(string title, string detail)
    {
        CommLostTitle = title;
        CommLostDetail = detail;
        ShowCommLost = true;
    }

    // ------------------------------------------------------------------ actions

    public void ShowScreen(string name)
    {
        if (!_screens.TryGetValue(name, out var screen)) return;
        _ = Runtime?.ReleaseAllAsync(); // a held button must never stay held across screens
        CurrentScreen = screen;
    }

    /// <summary>Fire-and-forget a runtime call; failures appear as a toast.</summary>
    public async void Run(Task<Status> task, string what)
    {
        var status = await task;
        if (status != Status.OK) ShowToast($"{what}: {Describe(status)}");
    }

    public static string Describe(Status s) => s switch
    {
        Status.OUT_OF_RANGE => "value out of range",
        Status.READ_ONLY => "tag is read-only",
        Status.NOT_PERMITTED => "this panel may not write",
        Status.COMM_FAILURE => "no communication",
        Status.TYPE_MISMATCH => "wrong value type",
        Status.INVALID => "not allowed",
        _ => s.ToString(),
    };

    public void ShowToast(string message)
    {
        Toast = message;
        _toastUntil = DateTime.UtcNow.AddSeconds(4);
    }

    /// <summary>Window lost focus / app is being minimised: release anything held.</summary>
    public void OnDeactivated() => _ = Runtime?.ReleaseAllAsync();

    public void OpenKeypad(Tag tag, bool text)
    {
        var current = Runtime?.Get(tag);
        Keypad = KeypadViewModel.ForTag(this, tag, text, current);
    }

    // ------------------------------------------------------------------ settings

    [RelayCommand]
    private void OpenSettings()
    {
        if (Settings.Pin.Length == 0 || !Settings.IsPaired)
        {
            OpenSettingsPage();
            return;
        }
        Keypad = KeypadViewModel.ForPin(this, entered =>
        {
            if (entered != Settings.Pin) return "Wrong PIN";
            OpenSettingsPage();
            return null;
        });
    }

    private void OpenSettingsPage() => SettingsPage = new SettingsViewModel(this, Settings);

    public void ApplySettings(PanelSettings settings)
    {
        Settings = settings;
        settings.Save(_dataDir);
        SettingsPage = null;
        OnPropertyChanged(nameof(Settings));
        StartRuntime();
    }

    public void RequestExit() => ExitRequested?.Invoke();

    // ------------------------------------------------------------------ periodic (every 500 ms)

    public void Tick()
    {
        Clock = DateTime.Now.ToString("HH:mm", CultureInfo.CurrentCulture);
        var r = _device.Read();
        HasBattery = r.BatteryPercent is not null;
        if (r.BatteryPercent is { } pct)
        {
            BatteryText = $"{pct}%{(r.Charging ? " ⚡" : "")}";
            BatteryFill = pct / 100.0;
            BatteryBrush = pct <= 15 ? Brushes.Red : pct <= 30 ? Brushes.Orange : Brushes.LimeGreen;
            LowBattery = pct <= 15 && !r.Charging;
        }
        HasWifi = r.WifiSignal is not null;
        if (r.WifiSignal is { } q)
        {
            WifiBars = q >= 75 ? 4 : q >= 50 ? 3 : q >= 25 ? 2 : q > 0 ? 1 : 0;
            WifiText = $"{q}%";
        }
        foreach (var t in CurrentScreen?.Widgets.OfType<TrendWidgetViewModel>() ?? []) t.Sample();
        if (Toast.Length > 0 && DateTime.UtcNow > _toastUntil) Toast = "";
    }
}
