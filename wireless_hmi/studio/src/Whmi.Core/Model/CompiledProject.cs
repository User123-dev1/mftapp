using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Whmi.Core.Model;

/// <summary>A validated project with tag ids, as stored on the receiver. Its SHA-256 identifies the version.</summary>
public sealed class CompiledProject
{
    private CompiledProject(byte[] data, Project project, IReadOnlyList<Issue> warnings)
    {
        Data = data;
        Hash = SHA256.HashData(data);
        Project = project;
        Warnings = warnings;
        foreach (var t in project.Tags)
        {
            TagsById[t.TagId] = t;
            TagsByName.TryAdd(t.Name, t);
        }
    }

    public byte[] Data { get; }
    public byte[] Hash { get; }
    public string HashHex => Convert.ToHexString(Hash).ToLowerInvariant();
    public Project Project { get; }
    public IReadOnlyList<Issue> Warnings { get; }
    public Dictionary<int, Tag> TagsById { get; } = new();
    public Dictionary<string, Tag> TagsByName { get; } = new(StringComparer.OrdinalIgnoreCase);
    public string Name => Project.Name;

    internal static CompiledProject FromDocument(JsonObject doc, IReadOnlyList<Issue> warnings)
    {
        var data = CanonicalJson.Serialize(doc);
        return new CompiledProject(data, Serialization.ProjectFromJson(doc), warnings);
    }

    /// <summary>Load and fully re-validate a compiled project (it may come off the network).</summary>
    public static CompiledProject FromBytes(byte[] data)
    {
        var doc = JsonNode.Parse(data) as JsonObject ?? throw new FormatException("not a JSON object");
        if (doc["format"]?.GetValue<string>() != Serialization.CompiledFormat)
            throw new FormatException("not a compiled WHMI project");
        var project = Serialization.ProjectFromJson(doc);
        var issues = project.Validate();
        if (issues.Any(i => i.Severity == Severity.Error)) throw new ProjectException(issues);
        var ids = project.Tags.Select(t => t.TagId).ToList();
        if (ids.Distinct().Count() != ids.Count || ids.Any(i => i is < 1 or > 0xFFFF))
            throw new FormatException("compiled project has invalid tag ids");
        return new CompiledProject(data, project, issues.Where(i => i.Severity == Severity.Warning).ToList());
    }

    public static CompiledProject Load(string path) => FromBytes(File.ReadAllBytes(path));

    public void Save(string path) => File.WriteAllBytes(path, Data);

    public Tag? Tag(string name) => TagsByName.GetValueOrDefault(name);
}

/// <summary>
/// Canonical JSON identical to Python's
/// <c>json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)</c>,
/// so Studio and the Python tools compute the same project hash.
/// </summary>
public static class CanonicalJson
{
    public static byte[] Serialize(JsonNode? node)
    {
        var sb = new StringBuilder();
        Write(sb, node);
        return new UTF8Encoding(false).GetBytes(sb.ToString());
    }

    private static void Write(StringBuilder sb, JsonNode? node)
    {
        switch (node)
        {
            case null:
                sb.Append("null");
                break;
            case JsonObject o:
                sb.Append('{');
                bool first = true;
                foreach (var (key, value) in o.OrderBy(kv => kv.Key, StringComparer.Ordinal))
                {
                    if (!first) sb.Append(',');
                    first = false;
                    WriteString(sb, key);
                    sb.Append(':');
                    Write(sb, value);
                }
                sb.Append('}');
                break;
            case JsonArray a:
                sb.Append('[');
                for (int i = 0; i < a.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    Write(sb, a[i]);
                }
                sb.Append(']');
                break;
            case JsonValue v:
                WriteValue(sb, v);
                break;
        }
    }

    private static void WriteValue(StringBuilder sb, JsonValue v)
    {
        if (v.TryGetValue<JsonElement>(out var e))
        {
            switch (e.ValueKind)
            {
                case JsonValueKind.String: WriteString(sb, e.GetString()!); return;
                case JsonValueKind.True: sb.Append("true"); return;
                case JsonValueKind.False: sb.Append("false"); return;
                case JsonValueKind.Null: sb.Append("null"); return;
                case JsonValueKind.Number:
                    // Python keeps the literal's kind: integers stay integers, floats get repr().
                    var raw = e.GetRawText();
                    if (raw.IndexOfAny(['.', 'e', 'E']) < 0) sb.Append(raw);
                    else sb.Append(PyFloat(e.GetDouble()));
                    return;
            }
        }
        switch (Serialization.ToScalar(v))
        {
            case string s: WriteString(sb, s); break;
            case bool b: sb.Append(b ? "true" : "false"); break;
            case long l: sb.Append(l.ToString(CultureInfo.InvariantCulture)); break;
            case ulong u: sb.Append(u.ToString(CultureInfo.InvariantCulture)); break;
            case double d: sb.Append(PyFloat(d)); break;
            default: sb.Append("null"); break;
        }
    }

    /// <summary>Python float repr: shortest round-trip, ".0" on integral values, "e-05" exponents.</summary>
    public static string PyFloat(double d)
    {
        if (double.IsNaN(d)) return "NaN";
        if (double.IsPositiveInfinity(d)) return "Infinity";
        if (double.IsNegativeInfinity(d)) return "-Infinity";
        // .NET "R" is the same shortest round-trip digits as Python; only the notation differs.
        double abs = Math.Abs(d);
        var text = d.ToString("R", CultureInfo.InvariantCulture);
        int ePos = text.IndexOf('E');
        if (abs != 0 && (abs < 1e-4 || abs >= 1e16))
        {
            if (ePos < 0) // .NET chose fixed notation; force scientific
            {
                text = d.ToString("E16", CultureInfo.InvariantCulture);
                ePos = text.IndexOf('E');
                var m = text[..ePos].TrimEnd('0').TrimEnd('.');
                text = m + text[ePos..];
                ePos = m.Length;
            }
            int exp = int.Parse(text[(ePos + 1)..], CultureInfo.InvariantCulture);
            return $"{text[..ePos]}e{(exp < 0 ? "-" : "+")}{Math.Abs(exp):00}";
        }
        if (ePos >= 0) // 1e15..1e16: Python prints these in full
            text = decimal.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
        return text.Contains('.') ? text : text + ".0";
    }

    private static void WriteString(StringBuilder sb, string s)
    {
        sb.Append('"');
        foreach (char ch in s)
        {
            switch (ch)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                default:
                    if (ch < 0x20) sb.Append("\\u").Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
                    else sb.Append(ch);
                    break;
            }
        }
        sb.Append('"');
    }
}
