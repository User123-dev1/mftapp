using System.Globalization;
using System.Text;
using Whmi.Core;
using Whmi.Core.Model;

namespace Whmi.Studio.Services;

/// <summary>Tag database import/export as CSV (Excel-friendly; RFC 4180 quoting).</summary>
public static class TagCsv
{
    public static readonly string[] Columns =
    [
        "name", "data_type", "source", "connection", "address", "access", "units", "description",
        "min", "max", "deadband", "initial", "momentary", "release_value", "failsafe_value",
        "failsafe_on_any_loss", "retentive", "safety", "scaling",
    ];

    public static string Export(IEnumerable<Tag> tags)
    {
        var sb = new StringBuilder();
        sb.AppendLine(string.Join(",", Columns));
        foreach (var t in tags.Where(t => t.Source != TagSource.System))
        {
            string[] cells =
            [
                t.Name, t.DataType.ToString(), Serialization.SourceName(t.Source), t.Connection, t.Address,
                Serialization.AccessName(t.Access), t.Units, t.Description,
                Num(t.Min), Num(t.Max), t.Deadband == 0 ? "" : Num(t.Deadband), DataTypes.Format(t.Initial),
                Flag(t.Momentary), DataTypes.Format(t.ReleaseValue), DataTypes.Format(t.FailsafeValue),
                Flag(t.FailsafeOnAnyLoss), Flag(t.Retentive), Flag(t.Safety), ScalingText.Format(t.Scaling),
            ];
            sb.AppendLine(string.Join(",", cells.Select(Quote)));
        }
        return sb.ToString();
    }

    /// <summary>Parse CSV into tags. Errors name the line so a user can fix the spreadsheet.</summary>
    public static List<Tag> Import(string csv)
    {
        var rows = Parse(csv);
        if (rows.Count == 0) return [];
        var header = rows[0].Select(h => h.Trim().ToLowerInvariant()).ToList();
        if (!header.Contains("name")) throw new FormatException("CSV needs a 'name' column");
        var tags = new List<Tag>();
        for (int i = 1; i < rows.Count; i++)
        {
            var row = rows[i];
            if (row.All(string.IsNullOrWhiteSpace)) continue;
            string Get(string col)
            {
                int idx = header.IndexOf(col);
                return idx >= 0 && idx < row.Count ? row[idx].Trim() : "";
            }
            try
            {
                var t = new Tag
                {
                    Name = Get("name"),
                    DataType = Get("data_type") is { Length: > 0 } dt ? DataTypes.Parse(dt) : DataType.DINT,
                    Source = Get("source") is { Length: > 0 } src ? Serialization.ParseSource(src) : TagSource.Memory,
                    Connection = Get("connection"),
                    Address = Get("address"),
                    Access = Get("access") is { Length: > 0 } acc ? Serialization.ParseAccess(acc) : TagAccess.Read,
                    Units = Get("units"),
                    Description = Get("description"),
                    Min = ParseNum(Get("min")),
                    Max = ParseNum(Get("max")),
                    Deadband = ParseNum(Get("deadband")) ?? 0,
                    Initial = DataTypes.ParseLiteral(Get("initial")),
                    Momentary = ParseFlag(Get("momentary")),
                    ReleaseValue = DataTypes.ParseLiteral(Get("release_value")),
                    FailsafeValue = DataTypes.ParseLiteral(Get("failsafe_value")),
                    FailsafeOnAnyLoss = ParseFlag(Get("failsafe_on_any_loss")),
                    Retentive = ParseFlag(Get("retentive")),
                    Safety = ParseFlag(Get("safety")),
                    Scaling = ScalingText.Parse(Get("scaling")),
                };
                tags.Add(t);
            }
            catch (FormatException e)
            {
                throw new FormatException($"line {i + 1}: {e.Message}");
            }
        }
        return tags;
    }

    private static string Num(double? d) => d is { } v ? v.ToString("R", CultureInfo.InvariantCulture) : "";
    private static string Flag(bool b) => b ? "true" : "";

    private static double? ParseNum(string s) =>
        s.Length == 0 ? null
        : double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d
        : throw new FormatException($"'{s}' is not a number");

    private static bool ParseFlag(string s) => s.ToLowerInvariant() is "true" or "1" or "yes" or "x";

    private static string Quote(string s) =>
        s.IndexOfAny([',', '"', '\n', '\r']) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;

    private static List<List<string>> Parse(string csv)
    {
        var rows = new List<List<string>>();
        var row = new List<string>();
        var cell = new StringBuilder();
        bool quoted = false;
        for (int i = 0; i < csv.Length; i++)
        {
            char c = csv[i];
            if (quoted)
            {
                if (c == '"' && i + 1 < csv.Length && csv[i + 1] == '"') { cell.Append('"'); i++; }
                else if (c == '"') quoted = false;
                else cell.Append(c);
            }
            else if (c == '"') quoted = true;
            else if (c == ',') { row.Add(cell.ToString()); cell.Clear(); }
            else if (c is '\n' or '\r')
            {
                if (c == '\r' && i + 1 < csv.Length && csv[i + 1] == '\n') i++;
                row.Add(cell.ToString());
                cell.Clear();
                rows.Add(row);
                row = [];
            }
            else cell.Append(c);
        }
        if (cell.Length > 0 || row.Count > 0)
        {
            row.Add(cell.ToString());
            rows.Add(row);
        }
        return rows;
    }
}

/// <summary>Scaling as one editable text: "raw_min,raw_max,eng_min,eng_max[,clamp]".</summary>
public static class ScalingText
{
    public static string Format(Scaling? s) => s is null ? ""
        : string.Join(",", new[] { s.RawMin, s.RawMax, s.EngMin, s.EngMax }
            .Select(d => d.ToString("R", CultureInfo.InvariantCulture))) + (s.Clamp ? ",clamp" : "");

    public static Scaling? Parse(string? text)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        var parts = text.Split(',', StringSplitOptions.TrimEntries);
        if (parts.Length is < 4 or > 5 || (parts.Length == 5 && parts[4] != "clamp"))
            throw new FormatException("scaling must be raw_min,raw_max,eng_min,eng_max[,clamp]");
        var n = parts.Take(4).Select(p => double.TryParse(p, NumberStyles.Float, CultureInfo.InvariantCulture, out var d)
            ? d : throw new FormatException($"scaling value '{p}' is not a number")).ToArray();
        return new Scaling(n[0], n[1], n[2], n[3], parts.Length == 5);
    }
}
