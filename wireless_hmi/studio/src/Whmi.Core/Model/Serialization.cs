using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Whmi.Core.Model;

/// <summary>
/// JSON mapping of the project format. Mirrors <c>to_dict</c>/<c>from_dict</c> in
/// whmi/common/project.py key for key, so files move freely between Studio and the Python tools.
/// </summary>
public static class Serialization
{
    public const string ProjectFormat = "whmi-project";
    public const string CompiledFormat = "whmi-compiled";
    public const int FormatVersion = 1;

    // ------------------------------------------------------------------ value helpers

    /// <summary>A JSON number for <paramref name="value"/>; integral values are written as integers.</summary>
    public static JsonValue Number(double value) =>
        double.IsFinite(value) && Math.Floor(value) == value && Math.Abs(value) < 9e15
            ? JsonValue.Create((long)value)
            : JsonValue.Create(value);

    public static double? ToDouble(JsonNode? node)
    {
        if (node is not JsonValue v) return null;
        if (v.TryGetValue<JsonElement>(out var e))
            return e.ValueKind == JsonValueKind.Number ? e.GetDouble() : null;
        if (v.TryGetValue<double>(out var d)) return d;
        if (v.TryGetValue<long>(out var l)) return l;
        if (v.TryGetValue<int>(out var i)) return i;
        if (v.TryGetValue<ulong>(out var u)) return u;
        if (v.TryGetValue<float>(out var f)) return f;
        return null;
    }

    /// <summary>JSON scalar → bool / long / ulong / double / string (null for null or non-scalars).</summary>
    public static object? ToScalar(JsonNode? node)
    {
        if (node is not JsonValue v) return null;
        if (v.TryGetValue<JsonElement>(out var e))
        {
            return e.ValueKind switch
            {
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.String => e.GetString(),
                JsonValueKind.Number when e.TryGetInt64(out var l) => l,
                JsonValueKind.Number when e.TryGetUInt64(out var u) => u,
                JsonValueKind.Number => e.GetDouble(),
                _ => null,
            };
        }
        if (v.TryGetValue<bool>(out var b)) return b;
        if (v.TryGetValue<string>(out var s)) return s;
        if (v.TryGetValue<long>(out var l2)) return l2;
        if (v.TryGetValue<int>(out var i)) return (long)i;
        if (v.TryGetValue<ulong>(out var u2)) return u2;
        return ToDouble(v);
    }

    public static JsonNode? FromScalar(object? value) => value switch
    {
        null => null,
        bool b => JsonValue.Create(b),
        string s => JsonValue.Create(s),
        long l => JsonValue.Create(l),
        ulong u => JsonValue.Create(u),
        int i => JsonValue.Create((long)i),
        double d => Number(d),
        float f => Number(f),
        _ => throw new ArgumentException($"unsupported value {value}"),
    };

    private static string Str(JsonObject o, string key, string fallback = "") =>
        o[key] is JsonValue v && v.TryGetValue<string>(out var s) ? s : fallback;

    private static int Int(JsonObject o, string key, int fallback)
    {
        var d = ToDouble(o[key]);
        if (d is null) return fallback;
        return (int)d.Value;
    }

    private static bool Bool(JsonObject o, string key) => ToScalar(o[key]) is true;

    // ------------------------------------------------------------------ tag

    public static string SourceName(TagSource s) => s switch
    {
        TagSource.Memory => "memory",
        TagSource.Io => "io",
        TagSource.Plc => "plc",
        _ => "system",
    };

    public static TagSource ParseSource(string s) => s switch
    {
        "memory" => TagSource.Memory,
        "io" => TagSource.Io,
        "plc" => TagSource.Plc,
        "system" => TagSource.System,
        _ => throw new FormatException($"invalid source '{s}'"),
    };

    public static string AccessName(TagAccess a) => a == TagAccess.ReadWrite ? "read_write" : "read";

    public static TagAccess ParseAccess(string s) => s switch
    {
        "read" => TagAccess.Read,
        "read_write" => TagAccess.ReadWrite,
        _ => throw new FormatException($"invalid access '{s}'"),
    };

    public static Tag TagFromJson(JsonObject d)
    {
        var tag = new Tag { Name = Str(d, "name") };
        if (d["source"] is JsonObject nested) // {"type": "plc", "connection": .., "address": ..}
        {
            tag.Source = ParseSource(Str(nested, "type", "memory"));
            tag.Address = Str(nested, "address");
            tag.Connection = Str(nested, "connection");
        }
        else
        {
            tag.Source = ParseSource(Str(d, "source", "memory"));
            tag.Address = Str(d, "address");
            tag.Connection = Str(d, "connection");
        }
        tag.DataType = DataTypes.Parse(Str(d, "data_type", "DINT"));
        tag.Access = ParseAccess(Str(d, "access", "read"));
        tag.Description = Str(d, "description");
        tag.Units = Str(d, "units");
        tag.Initial = ToScalar(d["initial"]);
        tag.Retentive = Bool(d, "retentive");
        tag.Min = ToDouble(d["min"]);
        tag.Max = ToDouble(d["max"]);
        tag.Deadband = ToDouble(d["deadband"]) ?? 0;
        tag.Momentary = Bool(d, "momentary");
        tag.ReleaseValue = ToScalar(d["release_value"]);
        tag.FailsafeValue = ToScalar(d["failsafe_value"]);
        tag.FailsafeOnAnyLoss = Bool(d, "failsafe_on_any_loss");
        tag.Safety = Bool(d, "safety");
        tag.TagId = Int(d, "tag_id", 0);
        if (d["scaling"] is JsonObject sc)
        {
            tag.Scaling = new Scaling(ToDouble(sc["raw_min"]) ?? 0, ToDouble(sc["raw_max"]) ?? 0,
                ToDouble(sc["eng_min"]) ?? 0, ToDouble(sc["eng_max"]) ?? 0, Bool(sc, "clamp"));
        }
        return tag;
    }

    public static JsonObject TagToJson(Tag t, bool includeId = false)
    {
        var d = new JsonObject
        {
            ["name"] = t.Name,
            ["data_type"] = t.DataType.ToString(),
            ["source"] = SourceName(t.Source),
        };
        if (t.Address.Length > 0) d["address"] = t.Address;
        if (t.Connection.Length > 0) d["connection"] = t.Connection;
        d["access"] = AccessName(t.Access);
        if (t.Description.Length > 0) d["description"] = t.Description;
        if (t.Units.Length > 0) d["units"] = t.Units;
        if (t.Initial is not null) d["initial"] = FromScalar(t.Initial);
        if (t.Retentive) d["retentive"] = true;
        if (t.Min is { } min) d["min"] = Number(min);
        if (t.Max is { } max) d["max"] = Number(max);
        if (t.Deadband != 0) d["deadband"] = JsonValue.Create(t.Deadband); // always a float, like Python
        if (t.Momentary) d["momentary"] = true;
        if (t.ReleaseValue is not null) d["release_value"] = FromScalar(t.ReleaseValue);
        if (t.FailsafeValue is not null) d["failsafe_value"] = FromScalar(t.FailsafeValue);
        if (t.FailsafeOnAnyLoss) d["failsafe_on_any_loss"] = true;
        if (t.Safety) d["safety"] = true;
        if (t.Scaling is { } s)
        {
            d["scaling"] = new JsonObject
            {
                ["raw_min"] = Number(s.RawMin),
                ["raw_max"] = Number(s.RawMax),
                ["eng_min"] = Number(s.EngMin),
                ["eng_max"] = Number(s.EngMax),
                ["clamp"] = s.Clamp,
            };
        }
        if (includeId) d["tag_id"] = t.TagId;
        return d;
    }

    // ------------------------------------------------------------------ project

    public static Project ProjectFromJson(JsonObject d)
    {
        var fmt = Str(d, "format", ProjectFormat);
        if (fmt != ProjectFormat && fmt != CompiledFormat)
            throw new FormatException($"not a WHMI project (format='{fmt}')");
        if (Int(d, "format_version", FormatVersion) > FormatVersion)
            throw new FormatException("project was created by a newer WHMI version");

        var p = new Project
        {
            Name = Str(d, "name", "Untitled"),
            Description = Str(d, "description"),
            StartScreen = Str(d, "start_screen"),
        };
        if (d["receiver"] is JsonObject r)
        {
            var rx = p.Receiver;
            rx.Name = Str(r, "name", "RX1");
            rx.ScanMs = Int(r, "scan_ms", 10);
            rx.HeartbeatMs = Int(r, "heartbeat_ms", 250);
            rx.WatchdogMs = Int(r, "watchdog_ms", 1000);
            if (r["slots"] is JsonObject slots)
            {
                foreach (var (key, value) in slots)
                {
                    var cat = value is JsonValue v && v.TryGetValue<string>(out var c) ? c : "";
                    if (int.TryParse(key, NumberStyles.None, CultureInfo.InvariantCulture, out var n) && n is >= 1 and <= IoCatalog.MaxSlots)
                        rx.Slots[n - 1].Catalog = cat;
                    else
                        rx.InvalidSlots[int.TryParse(key, out var bad) ? bad : -1] = cat;
                }
            }
        }
        foreach (var c in (d["plc_connections"] as JsonArray ?? []).OfType<JsonObject>())
        {
            p.PlcConnections.Add(new PlcConnection
            {
                Name = Str(c, "name"),
                Driver = Str(c, "driver", "logix"),
                Host = Str(c, "host"),
                Path = c["path"] is JsonValue pv ? (ToScalar(pv) is { } pathValue ? DataTypes.Format(pathValue) : "1,0") : "1,0",
                PollMs = Int(c, "poll_ms", 100),
                TimeoutMs = Int(c, "timeout_ms", 2000),
                HeartbeatTag = Str(c, "heartbeat_tag"),
                HeartbeatMs = Int(c, "heartbeat_ms", 500),
            });
        }
        foreach (var t in (d["tags"] as JsonArray ?? []).OfType<JsonObject>())
            p.Tags.Add(TagFromJson(t));
        foreach (var s in (d["screens"] as JsonArray ?? []).OfType<JsonObject>())
        {
            var screen = new Screen
            {
                Name = Str(s, "name"),
                Width = Int(s, "width", 1024),
                Height = Int(s, "height", 600),
                Background = Str(s, "background", "#1E1E1E"),
            };
            foreach (var w in (s["widgets"] as JsonArray ?? []).OfType<JsonObject>())
                screen.Widgets.Add(new Widget((JsonObject)w.DeepClone()));
            p.Screens.Add(screen);
        }
        return p;
    }

    public static JsonObject ProjectToJson(Project p)
    {
        var slots = new JsonObject();
        foreach (var slot in p.Receiver.Slots.Where(s => s.Catalog.Length > 0))
            slots[slot.Number.ToString(CultureInfo.InvariantCulture)] = slot.Catalog;
        foreach (var (n, cat) in p.Receiver.InvalidSlots)
            slots[n.ToString(CultureInfo.InvariantCulture)] = cat;

        var conns = new JsonArray();
        foreach (var c in p.PlcConnections)
        {
            var o = new JsonObject
            {
                ["name"] = c.Name,
                ["driver"] = c.Driver,
                ["host"] = c.Host,
                ["path"] = c.Path,
                ["poll_ms"] = c.PollMs,
                ["timeout_ms"] = c.TimeoutMs,
            };
            if (c.HeartbeatTag.Length > 0)
            {
                o["heartbeat_tag"] = c.HeartbeatTag;
                o["heartbeat_ms"] = c.HeartbeatMs;
            }
            conns.Add(o);
        }

        var screens = new JsonArray();
        foreach (var s in p.Screens)
        {
            var widgets = new JsonArray();
            foreach (var w in s.Widgets) widgets.Add(w.Props.DeepClone());
            screens.Add(new JsonObject
            {
                ["name"] = s.Name,
                ["width"] = s.Width,
                ["height"] = s.Height,
                ["background"] = s.Background,
                ["widgets"] = widgets,
            });
        }

        var tags = new JsonArray();
        foreach (var t in p.Tags.Where(t => t.Source != TagSource.System)) tags.Add(TagToJson(t));

        return new JsonObject
        {
            ["format"] = ProjectFormat,
            ["format_version"] = FormatVersion,
            ["name"] = p.Name,
            ["description"] = p.Description,
            ["receiver"] = new JsonObject
            {
                ["name"] = p.Receiver.Name,
                ["scan_ms"] = p.Receiver.ScanMs,
                ["heartbeat_ms"] = p.Receiver.HeartbeatMs,
                ["watchdog_ms"] = p.Receiver.WatchdogMs,
                ["slots"] = slots,
            },
            ["plc_connections"] = conns,
            ["tags"] = tags,
            ["screens"] = screens,
            ["start_screen"] = p.StartScreen,
        };
    }
}
