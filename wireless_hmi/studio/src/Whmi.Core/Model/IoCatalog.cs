using System.Text.RegularExpressions;

namespace Whmi.Core.Model;

public sealed record IoModuleType(
    string Catalog, string Kind, int Channels, DataType RawType, double RawMin, double RawMax, string Description)
{
    public bool IsOutput => Kind is "DO" or "AO";
}

/// <summary>I/O modules that fit the receiver's slots (mirrors whmi.common.project.IO_CATALOG).</summary>
public static class IoCatalog
{
    public const int MaxSlots = 16;

    public static IReadOnlyList<IoModuleType> Modules { get; } =
    [
        new("DI8", "DI", 8, DataType.BOOL, 0, 1, "8-point 24 VDC sinking digital input"),
        new("DI16", "DI", 16, DataType.BOOL, 0, 1, "16-point 24 VDC sinking digital input"),
        new("DO8", "DO", 8, DataType.BOOL, 0, 1, "8-point 24 VDC sourcing digital output, 0.5 A"),
        new("DO16", "DO", 16, DataType.BOOL, 0, 1, "16-point 24 VDC sourcing digital output, 0.5 A"),
        new("AI4", "AI", 4, DataType.INT, 0, 32000, "4-channel 4-20 mA analog input, 0..32000 counts"),
        new("AI4V", "AI", 4, DataType.INT, 0, 32000, "4-channel 0-10 V analog input, 0..32000 counts"),
        new("AO2", "AO", 2, DataType.INT, 0, 32000, "2-channel 4-20 mA analog output, 0..32000 counts"),
        new("AO2V", "AO", 2, DataType.INT, 0, 32000, "2-channel 0-10 V analog output, 0..32000 counts"),
    ];

    public static IoModuleType? Find(string? catalog) => Modules.FirstOrDefault(m => m.Catalog == catalog);
}

public sealed partial record IoAddress(int Slot, string Kind, int Channel)
{
    [GeneratedRegex(@"^Slot(\d+)\.(DI|DO|AI|AO)\.(\d+)$")]
    private static partial Regex Pattern();

    public static bool TryParse(string? text, out IoAddress? address)
    {
        address = null;
        var m = Pattern().Match((text ?? "").Trim());
        if (!m.Success || !int.TryParse(m.Groups[1].Value, out var slot) || !int.TryParse(m.Groups[3].Value, out var ch))
            return false;
        address = new IoAddress(slot, m.Groups[2].Value, ch);
        return true;
    }

    public override string ToString() => $"Slot{Slot}.{Kind}.{Channel}";
}
