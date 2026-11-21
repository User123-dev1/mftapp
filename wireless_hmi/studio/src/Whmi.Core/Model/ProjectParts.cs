using System.Collections.ObjectModel;
using System.Text.Json.Nodes;

namespace Whmi.Core.Model;

public sealed class PlcConnection : ModelBase
{
    public static IReadOnlyList<string> Drivers { get; } = ["logix", "simulator"];

    private string _name = "PLC1";
    private string _driver = "logix";
    private string _host = "";
    private string _path = "1,0";
    private int _pollMs = 100;
    private int _timeoutMs = 2000;
    private string _heartbeatTag = "";
    private int _heartbeatMs = 500;

    public string Name { get => _name; set => Set(ref _name, value); }
    public string Driver { get => _driver; set => Set(ref _driver, value); }
    public string Host { get => _host; set => Set(ref _host, value); }

    /// <summary>CIP route, e.g. "1,0" = backplane port 1, slot 0.</summary>
    public string Path { get => _path; set => Set(ref _path, value); }

    public int PollMs { get => _pollMs; set => Set(ref _pollMs, value); }
    public int TimeoutMs { get => _timeoutMs; set => Set(ref _timeoutMs, value); }

    /// <summary>Optional DINT tag the receiver increments while an operator panel is connected.</summary>
    public string HeartbeatTag { get => _heartbeatTag; set => Set(ref _heartbeatTag, value); }

    public int HeartbeatMs { get => _heartbeatMs; set => Set(ref _heartbeatMs, value); }

    public override string ToString() => Name;
}

public sealed class IoSlot : ModelBase
{
    private string _catalog = "";

    public IoSlot(int number) => Number = number;

    public int Number { get; }

    /// <summary>Catalog id of the installed module, "" when the slot is empty.</summary>
    public string Catalog
    {
        get => _catalog;
        set
        {
            if (Set(ref _catalog, value ?? "")) OnPropertyChanged(nameof(Module));
        }
    }

    public IoModuleType? Module => IoCatalog.Find(_catalog);
}

public sealed class ReceiverConfig : ModelBase
{
    private string _name = "RX1";
    private int _scanMs = 10;
    private int _heartbeatMs = 250;
    private int _watchdogMs = 1000;

    public ReceiverConfig()
    {
        Slots = new ReadOnlyCollection<IoSlot>(Enumerable.Range(1, IoCatalog.MaxSlots).Select(n => new IoSlot(n)).ToList());
    }

    public string Name { get => _name; set => Set(ref _name, value); }
    public int ScanMs { get => _scanMs; set => Set(ref _scanMs, value); }
    public int HeartbeatMs { get => _heartbeatMs; set => Set(ref _heartbeatMs, value); }
    public int WatchdogMs { get => _watchdogMs; set => Set(ref _watchdogMs, value); }

    /// <summary>Always <see cref="IoCatalog.MaxSlots"/> entries, numbered from 1.</summary>
    public IReadOnlyList<IoSlot> Slots { get; }

    public IoModuleType? ModuleIn(int slot) =>
        slot is >= 1 and <= IoCatalog.MaxSlots ? Slots[slot - 1].Module : null;

    /// <summary>Slot entries outside 1..MaxSlots found in a hand-edited file (reported by validation).</summary>
    public Dictionary<int, string> InvalidSlots { get; } = new();
}

public sealed class Screen : ModelBase
{
    private string _name = "Main";
    private int _width = 1024;
    private int _height = 600;
    private string _background = "#1E1E1E";

    public string Name { get => _name; set => Set(ref _name, value); }
    public int Width { get => _width; set => Set(ref _width, value); }
    public int Height { get => _height; set => Set(ref _height, value); }
    public string Background { get => _background; set => Set(ref _background, value); }
    public ObservableCollection<Widget> Widgets { get; } = [];

    public override string ToString() => Name;
}

/// <summary>
/// A screen widget. All properties live in <see cref="Props"/> exactly as they appear in the
/// project file, so properties this version of Studio doesn't know survive a round trip.
/// </summary>
public sealed class Widget : ModelBase
{
    public static IReadOnlyList<string> Types { get; } =
    [
        "label", "rectangle", "indicator", "numeric_display", "text_display", "bar", "gauge", "trend",
        "button", "numeric_entry", "text_entry", "toggle", "goto",
    ];

    public static IReadOnlyList<string> ButtonActions { get; } = ["momentary", "set", "reset", "toggle", "write"];

    /// <summary>(needs a tag, writes the tag) per widget type.</summary>
    public static (bool NeedsTag, bool Writes) Traits(string? type) => type switch
    {
        "label" or "rectangle" or "goto" => (false, false),
        "indicator" or "numeric_display" or "text_display" or "bar" or "gauge" or "trend" => (true, false),
        "button" or "numeric_entry" or "text_entry" or "toggle" => (true, true),
        _ => (false, false),
    };

    public Widget(JsonObject? props = null) => Props = props ?? new JsonObject();

    public JsonObject Props { get; }

    public string? Id { get => GetString("id"); set => SetValue("id", value); }
    public string? Type { get => GetString("type"); set => SetValue("type", value); }
    public double X { get => GetNumber("x") ?? 0; set => SetValue("x", value); }
    public double Y { get => GetNumber("y") ?? 0; set => SetValue("y", value); }
    public double W { get => GetNumber("w") ?? 0; set => SetValue("w", value); }
    public double H { get => GetNumber("h") ?? 0; set => SetValue("h", value); }
    public string? Tag { get => GetString("tag"); set => SetValue("tag", value); }
    public string? Text { get => GetString("text"); set => SetValue("text", value); }
    public string? Action { get => GetString("action"); set => SetValue("action", value); }
    public string? TargetScreen { get => GetString("screen"); set => SetValue("screen", value); }
    public string? Format { get => GetString("format"); set => SetValue("format", value); }
    public string? Color { get => GetString("color"); set => SetValue("color", value); }
    public string? OnText { get => GetString("on_text"); set => SetValue("on_text", value); }
    public string? OffText { get => GetString("off_text"); set => SetValue("off_text", value); }
    public string? OnColor { get => GetString("on_color"); set => SetValue("on_color", value); }
    public string? OffColor { get => GetString("off_color"); set => SetValue("off_color", value); }
    public double? Min { get => GetNumber("min"); set => SetValue("min", value); }
    public double? Max { get => GetNumber("max"); set => SetValue("max", value); }

    public bool HasNumber(string key) => GetNumber(key) is not null;

    public string? GetString(string key) =>
        Props[key] is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    public double? GetNumber(string key) => Serialization.ToDouble(Props[key]);

    private void SetValue(string key, string? value, [System.Runtime.CompilerServices.CallerMemberName] string? prop = null)
    {
        if (GetString(key) == value && (value is not null || !Props.ContainsKey(key))) return;
        if (string.IsNullOrEmpty(value)) Props.Remove(key);
        else Props[key] = value;
        OnPropertyChanged(prop);
    }

    private void SetValue(string key, double? value, [System.Runtime.CompilerServices.CallerMemberName] string? prop = null)
    {
        if (GetNumber(key) == value) return;
        if (value is null) Props.Remove(key);
        else Props[key] = Serialization.Number(value.Value);
        OnPropertyChanged(prop);
    }

    public Widget Clone() => new((JsonObject)Props.DeepClone());
}
