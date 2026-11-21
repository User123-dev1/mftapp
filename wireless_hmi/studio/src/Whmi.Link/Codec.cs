using System.Buffers.Binary;
using System.Text;
using Whmi.Core;

namespace Whmi.Link;

/// <summary>Malformed or unexpected data on the link; the connection must be closed.</summary>
public sealed class ProtocolException(string message) : Exception(message);

/// <summary>Little-endian payload writer (PROTOCOL.md).</summary>
public sealed class PayloadWriter
{
    private readonly MemoryStream _buf = new();
    private readonly byte[] _tmp = new byte[8];

    public int Length => (int)_buf.Length;

    public PayloadWriter U8(int v) { _buf.WriteByte((byte)v); return this; }

    public PayloadWriter U16(int v)
    {
        BinaryPrimitives.WriteUInt16LittleEndian(_tmp, checked((ushort)v));
        _buf.Write(_tmp, 0, 2);
        return this;
    }

    public PayloadWriter U32(long v)
    {
        BinaryPrimitives.WriteUInt32LittleEndian(_tmp, checked((uint)v));
        _buf.Write(_tmp, 0, 4);
        return this;
    }

    public PayloadWriter I64(long v)
    {
        BinaryPrimitives.WriteInt64LittleEndian(_tmp, v);
        _buf.Write(_tmp, 0, 8);
        return this;
    }

    public PayloadWriter Raw(ReadOnlySpan<byte> b) { _buf.Write(b); return this; }

    public PayloadWriter Blob16(ReadOnlySpan<byte> b)
    {
        if (b.Length > 0xFFFF) throw new ArgumentException("blob too long");
        U16(b.Length);
        return Raw(b);
    }

    public PayloadWriter Str8(string s)
    {
        var b = Encoding.UTF8.GetBytes(s);
        if (b.Length > 0xFF) throw new ArgumentException("string too long for str8");
        U8(b.Length);
        return Raw(b);
    }

    public PayloadWriter Str16(string s) => Blob16(Encoding.UTF8.GetBytes(s));

    /// <summary>Typed Value: CIP type code + data. The value is coerced to <paramref name="type"/> first.</summary>
    public PayloadWriter Value(DataType type, object? value)
    {
        var v = DataTypes.Coerce(type, value);
        U8((byte)type);
        switch (type)
        {
            case DataType.BOOL: return U8((bool)v ? 1 : 0);
            case DataType.STRING: return Str16((string)v);
            case DataType.REAL:
                BinaryPrimitives.WriteSingleLittleEndian(_tmp, (float)(double)v);
                _buf.Write(_tmp, 0, 4);
                return this;
            case DataType.LREAL:
                BinaryPrimitives.WriteDoubleLittleEndian(_tmp, (double)v);
                _buf.Write(_tmp, 0, 8);
                return this;
            case DataType.ULINT:
                BinaryPrimitives.WriteUInt64LittleEndian(_tmp, (ulong)v);
                _buf.Write(_tmp, 0, 8);
                return this;
        }
        long l = (long)v;
        switch (type)
        {
            case DataType.SINT: case DataType.USINT: _buf.WriteByte((byte)l); break;
            case DataType.INT: case DataType.UINT:
                BinaryPrimitives.WriteUInt16LittleEndian(_tmp, (ushort)l); _buf.Write(_tmp, 0, 2); break;
            case DataType.DINT: case DataType.UDINT:
                BinaryPrimitives.WriteUInt32LittleEndian(_tmp, (uint)l); _buf.Write(_tmp, 0, 4); break;
            default:
                BinaryPrimitives.WriteInt64LittleEndian(_tmp, l); _buf.Write(_tmp, 0, 8); break;
        }
        return this;
    }

    public byte[] ToArray() => _buf.ToArray();
}

/// <summary>Bounds-checked little-endian payload reader.</summary>
public sealed class PayloadReader(byte[] data)
{
    private int _pos;

    public int Remaining => data.Length - _pos;

    private ReadOnlySpan<byte> Take(int n)
    {
        if (n < 0 || _pos + n > data.Length) throw new ProtocolException("payload truncated");
        var span = data.AsSpan(_pos, n);
        _pos += n;
        return span;
    }

    public byte U8() => Take(1)[0];
    public ushort U16() => BinaryPrimitives.ReadUInt16LittleEndian(Take(2));
    public uint U32() => BinaryPrimitives.ReadUInt32LittleEndian(Take(4));
    public long I64() => BinaryPrimitives.ReadInt64LittleEndian(Take(8));
    public byte[] Raw(int n) => Take(n).ToArray();
    public byte[] Blob16() => Raw(U16());

    private static string Utf8(ReadOnlySpan<byte> b)
    {
        try { return new UTF8Encoding(false, true).GetString(b); }
        catch (DecoderFallbackException) { throw new ProtocolException("invalid UTF-8"); }
    }

    public string Str8() => Utf8(Take(U8()));
    public string Str16() => Utf8(Take(U16()));

    public (DataType Type, object Value) Value()
    {
        byte code = U8();
        if (!Enum.IsDefined(typeof(DataType), code)) throw new ProtocolException($"unknown value type code 0x{code:X2}");
        var t = (DataType)code;
        object v = t switch
        {
            DataType.BOOL => U8() switch { 0 => false, 1 => true, _ => throw new ProtocolException("BOOL value must be 0 or 1") },
            DataType.SINT => (long)(sbyte)U8(),
            DataType.USINT => (long)U8(),
            DataType.INT => (long)BinaryPrimitives.ReadInt16LittleEndian(Take(2)),
            DataType.UINT => (long)U16(),
            DataType.DINT => (long)BinaryPrimitives.ReadInt32LittleEndian(Take(4)),
            DataType.UDINT => (long)U32(),
            DataType.LINT => I64(),
            DataType.ULINT => BinaryPrimitives.ReadUInt64LittleEndian(Take(8)),
            DataType.REAL => (double)BinaryPrimitives.ReadSingleLittleEndian(Take(4)),
            DataType.LREAL => BinaryPrimitives.ReadDoubleLittleEndian(Take(8)),
            DataType.STRING => Str16(),
            _ => throw new ProtocolException("unreachable"),
        };
        return (t, v);
    }

    public void Done()
    {
        if (Remaining != 0) throw new ProtocolException($"{Remaining} unexpected trailing byte(s)");
    }
}
