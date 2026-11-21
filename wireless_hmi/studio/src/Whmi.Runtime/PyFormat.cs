using System.Globalization;
using System.Text;
using Whmi.Core;

namespace Whmi.Runtime;

/// <summary>
/// Formats a value with the Python-style format strings used in widget "format" properties,
/// e.g. <c>"{:.1f} %"</c>, <c>"{:d} pcs"</c>, <c>"{:,.2f}"</c>, <c>"{:08.3f}"</c>, <c>"{}"</c>.
/// Supported: fill/align/sign/zero/width, the ',' separator, precision and the types f, d, e, %, x, X, s.
/// Anything unparseable falls back to the plain value, so a typo never blanks the display.
/// </summary>
public static class PyFormat
{
    public static string Format(string? format, object? value)
    {
        if (string.IsNullOrEmpty(format)) return DataTypes.Format(value);
        var sb = new StringBuilder();
        int i = 0;
        while (i < format.Length)
        {
            char c = format[i];
            if (c == '{' && i + 1 < format.Length && format[i + 1] == '{') { sb.Append('{'); i += 2; continue; }
            if (c == '}' && i + 1 < format.Length && format[i + 1] == '}') { sb.Append('}'); i += 2; continue; }
            if (c == '{')
            {
                int end = format.IndexOf('}', i);
                if (end < 0) return DataTypes.Format(value);
                var field = format[(i + 1)..end];
                int colon = field.IndexOf(':');
                var spec = colon >= 0 ? field[(colon + 1)..] : "";
                sb.Append(Apply(spec, value));
                i = end + 1;
                continue;
            }
            sb.Append(c);
            i++;
        }
        return sb.ToString();
    }

    private static string Apply(string spec, object? value)
    {
        // [[fill]align][sign][0][width][,][.precision][type]
        int p = 0;
        char fill = ' ', align = '\0';
        if (spec.Length >= 2 && spec[1] is '<' or '>' or '^') { fill = spec[0]; align = spec[1]; p = 2; }
        else if (spec.Length >= 1 && spec[0] is '<' or '>' or '^') { align = spec[0]; p = 1; }
        char sign = '-';
        if (p < spec.Length && spec[p] is '+' or '-' or ' ') sign = spec[p++];
        bool zero = false;
        if (p < spec.Length && spec[p] == '0') { zero = true; p++; }
        int width = 0;
        while (p < spec.Length && char.IsDigit(spec[p])) width = width * 10 + (spec[p++] - '0');
        bool grouping = false;
        if (p < spec.Length && spec[p] == ',') { grouping = true; p++; }
        int? precision = null;
        if (p < spec.Length && spec[p] == '.')
        {
            p++;
            int prec = 0;
            while (p < spec.Length && char.IsDigit(spec[p])) prec = prec * 10 + (spec[p++] - '0');
            precision = prec;
        }
        char type = p < spec.Length ? spec[p++] : '\0';
        if (p != spec.Length) return DataTypes.Format(value);

        string body;
        var inv = CultureInfo.InvariantCulture;
        double? number = value switch
        {
            bool b => b ? 1 : 0,
            string => null,
            null => null,
            _ when DataTypesHelper.IsNumber(value) => Convert.ToDouble(value, inv),
            _ => null,
        };
        switch (type)
        {
            case 'f' or 'F' when number is { } f:
                body = Fixed(f, precision ?? 6, grouping);
                break;
            case '%' when number is { } pct:
                body = Fixed(pct * 100, precision ?? 6, grouping) + "%";
                break;
            case 'e' or 'E' when number is { } e:
                body = e.ToString((type == 'e' ? "0." + new string('0', precision ?? 6) + "e+00" : "0." + new string('0', precision ?? 6) + "E+00"), inv);
                break;
            case 'd' when number is { } d:
                body = Fixed(d, 0, grouping);
                break;
            case 'x' or 'X' when number is { } x:
                body = ((long)x).ToString(type == 'x' ? "x" : "X", inv);
                break;
            case 's' or '\0':
                body = grouping && number is { } g ? g.ToString("#,0.##########", inv) : DataTypes.Format(value);
                break;
            default:
                return DataTypes.Format(value);
        }
        if (number is { } signed && sign != '-' && signed >= 0) body = sign + body;

        if (body.Length >= width) return body;
        if (zero && align == '\0' && number is not null)
        {
            bool negative = body.StartsWith('-') || body.StartsWith('+') || body.StartsWith(' ');
            var digits = negative ? body[1..] : body;
            return (negative ? body[..1] : "") + digits.PadLeft(width - (negative ? 1 : 0), '0');
        }
        if (align == '\0') align = number is null ? '<' : '>';
        int pad = width - body.Length;
        return align switch
        {
            '<' => body + new string(fill, pad),
            '^' => new string(fill, pad / 2) + body + new string(fill, pad - pad / 2),
            _ => new string(fill, pad) + body,
        };
    }

    /// <summary>
    /// Fixed-point text rounded like Python: correctly rounded from the exact binary value, with exact
    /// ties going to the even digit ("{:.0f}" of 2.5 is "2"; .NET alone would print "3").
    /// </summary>
    private static string Fixed(double d, int precision, bool grouping)
    {
        var inv = CultureInfo.InvariantCulture;
        if (!double.IsFinite(d)) return d.ToString(inv);
        string text = d.ToString("F" + precision, inv);
        // .NET prints exact digits for large precisions: detect an exact tie ("5" followed by zeros).
        string wide = Math.Abs(d).ToString("F" + (precision + 30), inv);
        int dot = wide.IndexOf('.');
        string rest = wide[(dot + 1 + precision)..];
        if (rest[0] == '5' && rest[1..].All(c => c == '0'))
        {
            string kept = wide[..(dot + 1 + precision)].TrimEnd('.');
            char last = kept[^1];
            var truncated = decimal.Parse(kept, inv);
            if ((last - '0') % 2 == 1) truncated += precision == 0 ? 1m : 1m / (decimal)Math.Pow(10, precision);
            text = (d < 0 ? "-" : "") + truncated.ToString("F" + precision, inv);
        }
        if (!grouping) return text;
        bool negative = text.StartsWith('-');
        if (negative) text = text[1..];
        int point = text.IndexOf('.');
        string intPart = point >= 0 ? text[..point] : text;
        string frac = point >= 0 ? text[point..] : "";
        var sb = new StringBuilder();
        for (int i = 0; i < intPart.Length; i++)
        {
            if (i > 0 && (intPart.Length - i) % 3 == 0) sb.Append(',');
            sb.Append(intPart[i]);
        }
        return (negative ? "-" : "") + sb + frac;
    }

    private static class DataTypesHelper
    {
        public static bool IsNumber(object v) =>
            v is sbyte or byte or short or ushort or int or uint or long or ulong or float or double or decimal;
    }
}
