using System.Globalization;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Link;
using Whmi.Panel.Platform;
using Whmi.Runtime;

namespace Whmi.Panel.ViewModels;

/// <summary>On-screen keypad for numeric entry, text entry and the settings PIN.</summary>
public sealed partial class KeypadViewModel : ObservableObject
{
    private readonly PanelViewModel _panel;
    private readonly Func<string, Task<string?>> _onOk;

    private KeypadViewModel(PanelViewModel panel, string title, string hint, string text, bool isText, bool isPin,
        Func<string, Task<string?>> onOk)
    {
        _panel = panel;
        Title = title;
        Hint = hint;
        _entry = text;
        IsText = isText;
        IsPin = isPin;
        _onOk = onOk;
    }

    public string Title { get; }
    public string Hint { get; }
    public bool IsText { get; }
    public bool IsPin { get; }
    public bool IsNumeric => !IsText;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Display))]
    private string _entry;

    [ObservableProperty] private string _error = "";
    [ObservableProperty] private bool _busy;

    public string Display => IsPin ? new string('●', Entry.Length) : Entry;

    public static KeypadViewModel ForTag(PanelViewModel panel, Tag tag, bool text, CachedValue? current)
    {
        string hint = tag.DataType.ToString();
        if (tag.Min is not null || tag.Max is not null)
            hint += $"   {Num(tag.Min) ?? "…"} to {Num(tag.Max) ?? "…"}";
        if (tag.Units.Length > 0) hint += $" {tag.Units}";
        string start = current is { Good: true } ? DataTypes.Format(current.Value) : "";
        return new KeypadViewModel(panel, tag.Description.Length > 0 ? tag.Description : tag.Name, hint, start,
            text || tag.DataType == DataType.STRING, false, entry => WriteAsync(panel, tag, entry));
    }

    public static KeypadViewModel ForPin(PanelViewModel panel, Func<string, string?> check) =>
        new(panel, "Enter PIN", "Settings are protected", "", false, true, entry => Task.FromResult(check(entry)));

    private static string? Num(double? d) => d?.ToString("R", CultureInfo.InvariantCulture);

    /// <summary>Validate locally first (fast feedback), then write; the receiver re-checks everything.</summary>
    private static async Task<string?> WriteAsync(PanelViewModel panel, Tag tag, string entry)
    {
        object? literal = tag.DataType == DataType.STRING ? entry : DataTypes.ParseLiteral(entry);
        if (literal is null) return "Enter a value";
        object value;
        try
        {
            value = DataTypes.Coerce(tag.DataType, literal);
        }
        catch (CoercionException e)
        {
            return e.OutOfRange ? $"Out of range for {tag.DataType}" : $"Not a valid {tag.DataType} value";
        }
        if (tag.DataType.IsNumeric())
        {
            double d = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (tag.Min is { } min && d < min) return $"Minimum is {Num(min)}";
            if (tag.Max is { } max && d > max) return $"Maximum is {Num(max)}";
        }
        if (panel.Runtime is not { } rt) return "No communication";
        var status = await rt.WriteAsync(tag.Name, value);
        return status == Status.OK ? null : PanelViewModel.Describe(status);
    }

    [RelayCommand]
    private void Key(string key)
    {
        Error = "";
        if (key == "." && Entry.Contains('.')) return;
        if (Entry.Length < 24) Entry += key;
    }

    [RelayCommand]
    private void Backspace()
    {
        if (Entry.Length > 0) Entry = Entry[..^1];
    }

    [RelayCommand]
    private void Clear() => Entry = "";

    [RelayCommand]
    private void Negate() => Entry = Entry.StartsWith('-') ? Entry[1..] : "-" + Entry;

    [RelayCommand]
    private void Cancel() => _panel.Keypad = null;

    [RelayCommand]
    private async Task OkAsync()
    {
        if (Busy) return;
        Busy = true;
        try
        {
            var error = await _onOk(Entry);
            if (error is null)
            {
                if (_panel.Keypad == this) _panel.Keypad = null;
            }
            else
            {
                Error = error;
            }
        }
        finally
        {
            Busy = false;
        }
    }
}

/// <summary>Pairing (receiver address, client id, key) and kiosk options.</summary>
public sealed partial class SettingsViewModel : ObservableObject
{
    private readonly PanelViewModel _panel;
    private readonly PanelSettings _current;

    public SettingsViewModel(PanelViewModel panel, PanelSettings current)
    {
        _panel = panel;
        _current = current;
        _host = current.Host;
        _port = current.Port;
        _clientId = current.ClientId;
        _fullscreen = current.Fullscreen;
        _kiosk = current.Kiosk;
        _pin = current.Pin;
    }

    [ObservableProperty] private string _host;
    [ObservableProperty] private int _port;
    [ObservableProperty] private string _clientId;
    [ObservableProperty] private string _keyHex = "";
    [ObservableProperty] private bool _fullscreen;
    [ObservableProperty] private bool _kiosk;
    [ObservableProperty] private string _pin;
    [ObservableProperty] private string _error = "";

    public bool HasKey => _current.GetKey() is not null;
    public string KeyStatus => HasKey ? "A key is stored (leave empty to keep it)." : "No key stored yet.";
    public bool CanCancel => _current.IsPaired;

    [RelayCommand]
    private void Save()
    {
        if (Host.Trim().Length == 0 || ClientId.Trim().Length == 0)
        {
            Error = "Receiver address and panel ID are required.";
            return;
        }
        if (Pin.Length > 0 && (Pin.Length < 4 || !Pin.All(char.IsDigit)))
        {
            Error = "PIN must be at least 4 digits (or empty for none).";
            return;
        }
        var next = new PanelSettings
        {
            Host = Host.Trim(),
            Port = Port,
            ClientId = ClientId.Trim(),
            StoredKey = _current.StoredKey,
            Fullscreen = Fullscreen,
            Kiosk = Kiosk,
            Pin = Pin,
        };
        if (KeyHex.Trim().Length > 0)
        {
            try
            {
                next.SetKey(LinkCrypto.ParseKey(KeyHex));
            }
            catch (FormatException e)
            {
                Error = e.Message;
                return;
            }
        }
        if (next.GetKey() is null)
        {
            Error = "Enter the panel key (64 hex characters, from the receiver's .key file).";
            return;
        }
        _panel.ApplySettings(next);
    }

    [RelayCommand]
    private void Cancel()
    {
        if (CanCancel) _panel.SettingsPage = null;
    }

    [RelayCommand]
    private void Exit() => _panel.RequestExit();
}
