using System.Collections.ObjectModel;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Whmi.Core.Model;

public enum Severity { Error, Warning }

public sealed record Issue(Severity Severity, string Path, string Message)
{
    public override string ToString() => $"{Severity.ToString().ToUpperInvariant()}: {Path}: {Message}";
}

public sealed class ProjectException(IReadOnlyList<Issue> issues)
    : Exception($"project has {issues.Count(i => i.Severity == Severity.Error)} error(s):\n  "
                + string.Join("\n  ", issues.Where(i => i.Severity == Severity.Error)))
{
    public IReadOnlyList<Issue> Issues { get; } = issues;
}

public sealed partial class Project : ModelBase
{
    public const int MaxTags = 65000;
    public const int TagNameMax = 64;
    public const int MomentaryMaxHoldMs = 2000;

    [GeneratedRegex("^[A-Za-z_][A-Za-z0-9_]*$")]
    private static partial Regex SegmentRe();

    [GeneratedRegex(@"^[A-Za-z0-9.\-:]+$")]
    private static partial Regex HostRe();

    private string _name = "NewProject";
    private string _description = "";
    private string _startScreen = "";

    public string Name { get => _name; set => Set(ref _name, value); }
    public string Description { get => _description; set => Set(ref _description, value); }
    public string StartScreen { get => _startScreen; set => Set(ref _startScreen, value); }
    public ReceiverConfig Receiver { get; } = new();
    public ObservableCollection<PlcConnection> PlcConnections { get; } = [];
    public ObservableCollection<Tag> Tags { get; } = [];
    public ObservableCollection<Screen> Screens { get; } = [];

    // ------------------------------------------------------------------ persistence

    public static Project FromJson(string json) =>
        Serialization.ProjectFromJson(JsonNode.Parse(json) as JsonObject
                                      ?? throw new FormatException("project file must contain a JSON object"));

    public string ToJson() =>
        Serialization.ProjectToJson(this).ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n";

    public static Project Load(string path) => FromJson(File.ReadAllText(path, Encoding.UTF8));

    public void Save(string path)
    {
        var tmp = path + ".tmp";
        File.WriteAllText(tmp, ToJson(), new UTF8Encoding(false));
        File.Move(tmp, path, overwrite: true);
    }

    public Tag? FindTag(string? name) => Tags.FirstOrDefault(t => t.Name == name);

    public PlcConnection? FindConnection(string? name) => PlcConnections.FirstOrDefault(c => c.Name == name);

    public static bool ValidTagName(string? name) =>
        !string.IsNullOrEmpty(name) && name.Length <= TagNameMax && name.Split('.').All(s => SegmentRe().IsMatch(s));

    // ------------------------------------------------------------------ validation

    public List<Issue> Validate()
    {
        var c = new List<Issue>();
        ValidateReceiver(c);
        ValidateConnections(c);
        ValidateTags(c);
        ValidateScreens(c);
        return c;
    }

    private static void Error(List<Issue> c, string path, string msg) => c.Add(new Issue(Severity.Error, path, msg));
    private static void Warn(List<Issue> c, string path, string msg) => c.Add(new Issue(Severity.Warning, path, msg));

    private void ValidateReceiver(List<Issue> c)
    {
        var r = Receiver;
        if (string.IsNullOrEmpty(r.Name) || !SegmentRe().IsMatch(r.Name))
            Error(c, "receiver.name", "must be an identifier");
        if (r.ScanMs is < 1 or > 1000) Error(c, "receiver.scan_ms", "must be 1..1000");
        if (r.HeartbeatMs is < 50 or > 5000) Error(c, "receiver.heartbeat_ms", "must be 50..5000");
        if (r.WatchdogMs < 2 * r.HeartbeatMs) Error(c, "receiver.watchdog_ms", "must be at least 2 x heartbeat_ms");
        if (r.WatchdogMs > 30000) Error(c, "receiver.watchdog_ms", "must be <= 30000");
        foreach (var (slot, _) in r.InvalidSlots)
            Error(c, $"receiver.slots.{slot}", $"slot must be 1..{IoCatalog.MaxSlots}");
        foreach (var slot in r.Slots.Where(s => s.Catalog.Length > 0 && s.Module is null))
            Error(c, $"receiver.slots.{slot.Number}",
                $"unknown module '{slot.Catalog}'; known: {string.Join(", ", IoCatalog.Modules.Select(m => m.Catalog))}");
    }

    private void ValidateConnections(List<Issue> c)
    {
        var seen = new HashSet<string>();
        for (int i = 0; i < PlcConnections.Count; i++)
        {
            var conn = PlcConnections[i];
            var p = $"plc_connections[{i}]";
            if (!SegmentRe().IsMatch(conn.Name ?? "")) Error(c, p, $"invalid connection name '{conn.Name}'");
            if (!seen.Add(conn.Name ?? "")) Error(c, p, $"duplicate connection name '{conn.Name}'");
            if (!PlcConnection.Drivers.Contains(conn.Driver))
                Error(c, p, $"unknown driver '{conn.Driver}'; known: {string.Join(", ", PlcConnection.Drivers)}");
            if (conn.Driver == "logix" && (string.IsNullOrEmpty(conn.Host) || !HostRe().IsMatch(conn.Host)))
                Error(c, p, "logix connection needs a valid host/IP address");
            if (conn.PollMs is < 10 or > 60000) Error(c, p, "poll_ms must be 10..60000");
            if (conn.TimeoutMs is < 100 or > 60000) Error(c, p, "timeout_ms must be 100..60000");
            if (conn.HeartbeatTag.Length > 0 && conn.HeartbeatMs is < 100 or > 10000)
                Error(c, p, "heartbeat_ms must be 100..10000");
        }
    }

    private void ValidateTags(List<Issue> c)
    {
        var names = new HashSet<string>();
        var ioUsed = new Dictionary<string, string>();
        var userTags = Tags.Where(t => t.Source != TagSource.System).ToList();
        if (userTags.Count > MaxTags) Error(c, "tags", $"too many tags ({userTags.Count} > {MaxTags})");
        foreach (var t in userTags)
        {
            var p = $"tags.{t.Name}";
            if (!ValidTagName(t.Name))
                Error(c, p, "invalid tag name (segments must be identifiers, separated by '.')");
            if (!names.Add(t.Name.ToLowerInvariant()))
                Error(c, p, "duplicate tag name (names are case-insensitive)");

            if (t.Source == TagSource.Io)
                ValidateIoTag(c, p, t, ioUsed);
            else if (t.Source == TagSource.Plc)
            {
                if (string.IsNullOrEmpty(t.Connection)) Error(c, p, "plc tag needs a connection");
                else if (FindConnection(t.Connection) is null) Error(c, p, $"unknown connection '{t.Connection}'");
                if (string.IsNullOrEmpty(t.Address)) Error(c, p, "plc tag needs an address (PLC tag name)");
            }

            if (t.Scaling is { } sc)
            {
                if (sc.Problem is { } problem) Error(c, p, problem);
                if (!t.DataType.IsFloat()) Error(c, p, "scaled tags must be REAL or LREAL (engineering units)");
                if (t.Source == TagSource.Memory) Warn(c, p, "scaling has no effect on memory tags");
            }

            foreach (var (key, value) in new[] { ("initial", t.Initial), ("release_value", t.ReleaseValue), ("failsafe_value", t.FailsafeValue) })
            {
                if (value is not null && !DataTypes.TryCoerce(t.DataType, value, out _, out var err))
                    Error(c, p, $"{key}: {err}");
            }
            if (t.Min is { } min && t.Max is { } max && min > max) Error(c, p, "min must be <= max");
            if ((t.Min is not null || t.Max is not null) && !t.DataType.IsNumeric())
                Error(c, p, "min/max only apply to numeric tags");
            if (t.Deadband < 0) Error(c, p, "deadband must be >= 0");

            if (t.Momentary)
            {
                if (t.Access != TagAccess.ReadWrite) Error(c, p, "momentary tags must be read_write");
                if (t.DataType == DataType.STRING) Error(c, p, "momentary tags cannot be STRING");
            }
            if (t.FailsafeValue is not null && t.Access != TagAccess.ReadWrite)
                Warn(c, p, "failsafe_value on a read-only tag is ignored");
            if (t.Safety && t.Access == TagAccess.ReadWrite)
                Error(c, p, "safety tags are display-only: access must be 'read'. "
                            + "Safety functions must be hardwired (IEC 60204-1 / ISO 13849)");
        }
    }

    private void ValidateIoTag(List<Issue> c, string p, Tag t, Dictionary<string, string> ioUsed)
    {
        if (!IoAddress.TryParse(t.Address, out var addr) || addr is null)
        {
            Error(c, p, $"invalid I/O address '{t.Address}' (expected e.g. 'Slot1.DI.0')");
            return;
        }
        if (addr.Slot is < 1 or > IoCatalog.MaxSlots || Receiver.Slots[addr.Slot - 1].Catalog.Length == 0)
        {
            Error(c, p, $"slot {addr.Slot} has no module configured");
            return;
        }
        var cat = Receiver.Slots[addr.Slot - 1].Catalog;
        var mod = IoCatalog.Find(cat);
        if (mod is null) return; // reported by receiver validation
        if (mod.Kind != addr.Kind)
        {
            Error(c, p, $"slot {addr.Slot} holds a {mod.Kind} module ({cat}), not {addr.Kind}");
            return;
        }
        if (addr.Channel < 0 || addr.Channel >= mod.Channels)
            Error(c, p, $"channel {addr.Channel} out of range 0..{mod.Channels - 1} for {cat}");
        var key = addr.ToString();
        if (ioUsed.TryGetValue(key, out var other)) Error(c, p, $"I/O point {key} already used by tag {other}");
        ioUsed[key] = t.Name;
        if (mod.Kind is "DI" or "DO" && t.DataType != DataType.BOOL) Error(c, p, $"{mod.Kind} points must be BOOL tags");
        if (mod.Kind is "AI" or "AO" && !t.DataType.IsNumeric()) Error(c, p, $"{mod.Kind} points must be numeric tags");
        if (!mod.IsOutput && t.Access == TagAccess.ReadWrite)
            Error(c, p, $"{mod.Kind} points are inputs and cannot be read_write");
    }

    private void ValidateScreens(List<Issue> c)
    {
        var names = new HashSet<string>();
        foreach (var s in Screens)
        {
            if (string.IsNullOrEmpty(s.Name) || !names.Add(s.Name))
                Error(c, $"screens.{s.Name}", "screen names must be unique and non-empty");
        }
        if (Screens.Count > 0 && StartScreen.Length > 0 && !names.Contains(StartScreen))
            Error(c, "start_screen", $"unknown screen '{StartScreen}'");
        if (Screens.Count > 0 && StartScreen.Length == 0)
            Warn(c, "start_screen", "not set; the first screen will be used");

        var bindable = new Dictionary<string, Tag>();
        foreach (var t in Tags) bindable[t.Name] = t;
        foreach (var t in SystemTags()) bindable[t.Name] = t;

        foreach (var s in Screens)
        {
            var ids = new HashSet<string>();
            for (int i = 0; i < s.Widgets.Count; i++)
            {
                var w = s.Widgets[i];
                var wid = string.IsNullOrEmpty(w.Id) ? $"#{i}" : w.Id;
                var p = $"screens.{s.Name}.{wid}";
                if (!ids.Add(wid)) Error(c, p, "duplicate widget id");
                if (w.Type is null || !Widget.Types.Contains(w.Type))
                {
                    Error(c, p, $"unknown widget type '{w.Type}'");
                    continue;
                }
                foreach (var dim in new[] { "x", "y", "w", "h" })
                    if (!w.HasNumber(dim)) Error(c, p, $"missing/invalid {dim}");
                var (needsTag, writes) = Widget.Traits(w.Type);
                if (w.Type == "goto" && (w.TargetScreen is null || !names.Contains(w.TargetScreen)))
                    Error(c, p, $"goto target '{w.TargetScreen}' does not exist");
                if (!needsTag) continue;
                if (w.Tag is null || !bindable.TryGetValue(w.Tag, out var tag))
                {
                    Error(c, p, $"bound tag '{w.Tag}' does not exist");
                    continue;
                }
                if (!writes) continue;
                if (tag.Safety) Error(c, p, $"tag {tag.Name} is a safety tag and cannot be bound to a write widget");
                else if (tag.Access != TagAccess.ReadWrite) Error(c, p, $"tag {tag.Name} is read-only");
                if (w.Type == "button")
                {
                    var action = w.Action ?? "momentary";
                    if (!Widget.ButtonActions.Contains(action)) Error(c, p, $"invalid button action '{action}'");
                    if (action == "momentary" && !tag.Momentary)
                        Error(c, p, $"momentary button needs tag {tag.Name} to be flagged momentary");
                    if (action is "set" or "reset" or "toggle" && tag.DataType != DataType.BOOL)
                        Error(c, p, $"action '{action}' needs a BOOL tag");
                }
                if (w.Type == "toggle" && tag.DataType != DataType.BOOL) Error(c, p, "toggle needs a BOOL tag");
            }
        }
    }

    // ------------------------------------------------------------------ build

    /// <summary>Diagnostic tags produced by the receiver itself (mirrors Python system_tags()).</summary>
    public List<Tag> SystemTags()
    {
        Tag Sys(string name, DataType type, string key, string description, string units = "", double deadband = 0) =>
            new() { Name = name, DataType = type, Source = TagSource.System, Address = key, Description = description,
                    Units = units, Deadband = deadband };

        var tags = new List<Tag>
        {
            Sys("_System.Link.Clients", DataType.UINT, "link.clients", "Number of authenticated WHMI-Link sessions"),
            Sys("_System.Link.Connected", DataType.BOOL, "link.connected", "At least one operator/engineer session is connected"),
            Sys("_System.Uptime", DataType.UDINT, "uptime", "Receiver uptime", units: "s"),
            Sys("_System.ScanTime", DataType.REAL, "scan_time", "Last I/O scan duration", units: "ms", deadband: 0.5),
        };
        foreach (var conn in PlcConnections)
            tags.Add(Sys($"_System.PLC.{conn.Name}.Connected", DataType.BOOL, $"plc.{conn.Name}.connected",
                $"PLC connection {conn.Name} is up"));
        return tags;
    }

    /// <summary>Validate and compile. Throws <see cref="ProjectException"/> listing every error.</summary>
    public CompiledProject Build()
    {
        var issues = Validate();
        if (issues.Any(i => i.Severity == Severity.Error)) throw new ProjectException(issues);
        var tags = Tags.Where(t => t.Source != TagSource.System).Select(t => t.Clone()).ToList();
        tags.AddRange(SystemTags());
        for (int i = 0; i < tags.Count; i++) tags[i].TagId = i + 1;

        var doc = Serialization.ProjectToJson(this);
        doc["format"] = Serialization.CompiledFormat;
        var arr = new JsonArray();
        foreach (var t in tags) arr.Add(Serialization.TagToJson(t, includeId: true));
        doc["tags"] = arr;
        if (Screens.Count > 0 && StartScreen.Length == 0) doc["start_screen"] = Screens[0].Name;
        return CompiledProject.FromDocument(doc, issues.Where(i => i.Severity == Severity.Warning).ToList());
    }
}
