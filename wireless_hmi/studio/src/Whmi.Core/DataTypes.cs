using System.Globalization;
using System.Text;

namespace Whmi.Core;

/// <summary>
/// Tag data types. Values are the CIP elementary type codes used by Rockwell Logix
/// controllers and on the WHMI-Link wire protocol.
/// </summary>
public enum DataType : byte
{
    BOOL = 0xC1,
    SINT = 0xC2,
    INT = 0xC3,
    DINT = 0xC4,
    LINT = 0xC5,
    USINT = 0xC6,
    UINT = 0xC7,
    UDINT = 0xC8,
    ULINT = 0xC9,
    REAL = 0xCA,
    LREAL = 0xCB,
    STRING = 0xD0,
}

/// <summary>A value cannot be represented in the target data type.</summary>
public sealed class CoercionException(string message, bool outOfRange = false) : Exception(message)
{
    public bool OutOfRange { get; } = outOfRange;
}

public static class DataTypes
{
    public const int StringMaxLength = 82; // Logix STRING capacity (bytes)

    public static IReadOnlyList<DataType> All { get; } = Enum.GetValues<DataType>();

    public static DataType Parse(string name)
    {
        if (Enum.TryParse<DataType>(name.Trim(), ignoreCase: true, out var dt) && Enum.IsDefined(dt))
            return dt;
        throw new FormatException($"unknown data type '{name}'");
    }

    public static bool IsInteger(this DataType t) => t is >= DataType.SINT and <= DataType.ULINT;
    public static bool IsFloat(this DataType t) => t is DataType.REAL or DataType.LREAL;
    public static bool IsNumeric(this DataType t) => t.IsInteger() || t.IsFloat();

    public static (Int128 Min, Int128 Max) IntRange(this DataType t) => t switch
    {
        DataType.SINT => (sbyte.MinValue, sbyte.MaxValue),
        DataType.INT => (short.MinValue, short.MaxValue),
        DataType.DINT => (int.MinValue, int.MaxValue),
        DataType.LINT => (long.MinValue, long.MaxValue),
        DataType.USINT => (byte.MinValue, byte.MaxValue),
        DataType.UINT => (ushort.MinValue, ushort.MaxValue),
        DataType.UDINT => (uint.MinValue, uint.MaxValue),
        DataType.ULINT => (ulong.MinValue, ulong.MaxValue),
        _ => throw new ArgumentException($"{t} is not an integer type"),
    };

    /// <summary>The zero value: <c>false</c>, <c>0L</c>, <c>0.0</c> or <c>""</c>.</summary>
    public static object Zero(this DataType t) => t switch
    {
        DataType.BOOL => false,
        DataType.STRING => "",
        DataType.REAL or DataType.LREAL => 0.0,
        DataType.ULINT => 0UL,
        _ => 0L,
    };

    /// <summary>
    /// Convert a value to the canonical .NET representation of <paramref name="type"/>:
    /// <c>bool</c>, <c>long</c> (<c>ulong</c> for ULINT), <c>double</c> or <c>string</c>.
    /// Lossy conversions throw instead of truncating, exactly like the Python implementation:
    /// a truncated setpoint written to a machine is worse than a rejected write.
    /// </summary>
    public static object Coerce(DataType type, object? value)
    {
        if (value is null)
            throw new CoercionException($"cannot convert null to {type}");

        if (type == DataType.BOOL)
        {
            switch (value)
            {
                case bool b: return b;
                case string s:
                    var v = s.Trim().ToLowerInvariant();
                    if (v is "1" or "true" or "on") return true;
                    if (v is "0" or "false" or "off") return false;
                    break;
                default:
                    if (IsNumber(value))
                    {
                        var d = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                        if (d == 0) return false;
                        if (d == 1) return true;
                    }
                    break;
            }
            throw new CoercionException($"cannot convert {Show(value)} to BOOL");
        }

        if (type == DataType.STRING)
        {
            if (value is not string str)
                throw new CoercionException($"cannot convert {Show(value)} to STRING");
            if (Encoding.UTF8.GetByteCount(str) > StringMaxLength)
                throw new CoercionException($"string longer than {StringMaxLength} bytes", outOfRange: true);
            return str;
        }

        if (value is string text)
            value = type.IsFloat() ? ParseFloat(text, type) : ParseInteger(text, type);
        if (value is bool flag)
            value = flag ? 1L : 0L;
        if (!IsNumber(value))
            throw new CoercionException($"cannot convert {Show(value)} to {type}");

        if (type.IsFloat())
        {
            double f = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (type == DataType.REAL)
            {
                if (double.IsFinite(f) && Math.Abs(f) > float.MaxValue)
                    throw new CoercionException($"{Show(value)} out of range for REAL", outOfRange: true);
                f = (float)f;
            }
            return f;
        }

        Int128 i;
        if (value is double or float or decimal)
        {
            double d = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (!double.IsFinite(d) || Math.Floor(d) != d)
                throw new CoercionException($"{Show(value)} is not an integer value for {type}");
            if (d < -1.8e19 || d > 1.9e19)
                throw new CoercionException($"{Show(value)} out of range for {type}", outOfRange: true);
            i = (Int128)d;
        }
        else
        {
            i = value is ulong u ? u : Convert.ToInt64(value, CultureInfo.InvariantCulture);
        }
        var (min, max) = type.IntRange();
        if (i < min || i > max)
            throw new CoercionException($"{i} out of range for {type} [{min}..{max}]", outOfRange: true);
        return type == DataType.ULINT ? (object)(ulong)i : (long)i;
    }

    public static bool TryCoerce(DataType type, object? value, out object result, out string? error)
    {
        try
        {
            result = Coerce(type, value);
            error = null;
            return true;
        }
        catch (CoercionException e)
        {
            result = type.Zero();
            error = e.Message;
            return false;
        }
    }

    internal static bool IsNumber(object v) =>
        v is sbyte or byte or short or ushort or int or uint or long or ulong or float or double or decimal;

    private static object ParseFloat(string text, DataType type)
    {
        if (double.TryParse(text.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var d))
            return d;
        throw new CoercionException($"cannot convert '{text}' to {type}");
    }

    /// <summary>Integer literal: decimal, or 0x / 0o / 0b prefixed (like Python's int(s, 0)).</summary>
    private static object ParseInteger(string text, DataType type)
    {
        var s = text.Trim().Replace("_", "");
        bool negative = s.StartsWith('-');
        if (negative || s.StartsWith('+')) s = s[1..];
        int radix = 10;
        if (s.Length > 2 && s[0] == '0')
        {
            radix = char.ToLowerInvariant(s[1]) switch { 'x' => 16, 'o' => 8, 'b' => 2, _ => 10 };
            if (radix != 10) s = s[2..];
        }
        if (s.Length == 0)
            throw new CoercionException($"cannot convert '{text}' to {type}");
        Int128 acc = 0;
        foreach (char c in s)
        {
            int digit = c switch
            {
                >= '0' and <= '9' => c - '0',
                >= 'a' and <= 'f' => c - 'a' + 10,
                >= 'A' and <= 'F' => c - 'A' + 10,
                _ => 99,
            };
            if (digit >= radix)
                throw new CoercionException($"cannot convert '{text}' to {type}");
            acc = acc * radix + digit;
            if (acc > (Int128)ulong.MaxValue + 1)
                throw new CoercionException($"'{text}' out of range for {type}", outOfRange: true);
        }
        if (negative) acc = -acc;
        if (acc > long.MaxValue) return (ulong)acc;
        if (acc < long.MinValue)
            throw new CoercionException($"'{text}' out of range for {type}", outOfRange: true);
        return (long)acc;
    }

    internal static string Show(object? v) => v switch
    {
        null => "null",
        string s => $"'{s}'",
        bool b => b ? "true" : "false",
        IFormattable f => f.ToString(null, CultureInfo.InvariantCulture),
        _ => v.ToString() ?? "?",
    };

    /// <summary>Display text of a value as an operator would type it.</summary>
    public static string Format(object? v) => v switch
    {
        null => "",
        bool b => b ? "true" : "false",
        double d => d.ToString("R", CultureInfo.InvariantCulture),
        IFormattable f => f.ToString(null, CultureInfo.InvariantCulture),
        _ => v.ToString() ?? "",
    };

    /// <summary>
    /// Parse what a user typed in an editor cell into a JSON-like value
    /// (<c>null</c> for empty, bool, long, double or the text itself).
    /// </summary>
    public static object? ParseLiteral(string? text)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        var t = text.Trim();
        if (t.Equals("true", StringComparison.OrdinalIgnoreCase)) return true;
        if (t.Equals("false", StringComparison.OrdinalIgnoreCase)) return false;
        if (long.TryParse(t, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var l)) return l;
        if (double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out var d)) return d;
        return text;
    }
}

/// <summary>Linear scaling from raw (device) units to engineering units.</summary>
public sealed record Scaling(double RawMin, double RawMax, double EngMin, double EngMax, bool Clamp = false)
{
    public double ToEng(double raw)
    {
        double span = (raw - RawMin) / (RawMax - RawMin);
        if (Clamp) span = Math.Clamp(span, 0, 1);
        return EngMin + span * (EngMax - EngMin);
    }

    public double ToRaw(double eng)
    {
        double span = (eng - EngMin) / (EngMax - EngMin);
        if (Clamp) span = Math.Clamp(span, 0, 1);
        return RawMin + span * (RawMax - RawMin);
    }

    public string? Problem =>
        RawMax == RawMin ? "scaling raw_min and raw_max must differ"
        : EngMax == EngMin ? "scaling eng_min and eng_max must differ"
        : null;
}
