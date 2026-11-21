using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;
using Whmi.Core;

namespace Whmi.Link;

public enum MsgType : byte
{
    HELLO = 0x01,
    CHALLENGE = 0x02,
    AUTH = 0x03,
    AUTH_OK = 0x04,
    AUTH_FAIL = 0x05,
    HEARTBEAT = 0x10,
    SESSION_INFO = 0x11,
    READ_REQ = 0x20,
    READ_RSP = 0x21,
    WRITE_REQ = 0x22,
    WRITE_RSP = 0x23,
    SUBSCRIBE = 0x24,
    SUB_RSP = 0x25,
    UNSUBSCRIBE = 0x26,
    DATA_UPDATE = 0x30,
    MOMENTARY = 0x31,
    PROJECT_INFO_REQ = 0x40,
    PROJECT_INFO = 0x41,
    PROJECT_READ = 0x42,
    PROJECT_DATA = 0x43,
    PROJECT_WRITE_BEGIN = 0x44,
    PROJECT_WRITE_CHUNK = 0x45,
    PROJECT_WRITE_COMMIT = 0x46,
    PROJECT_WRITE_RSP = 0x47,
    PROJECT_CHANGED = 0x48,
    ERROR = 0x7F,
}

public static class Frames
{
    public const int DefaultPort = 47800;
    public const byte ProtocolVersion = 1;
    public const int HeaderSize = 12;
    public const int MaxPayload = 16384;
    public const int GcmTagSize = 16;
    public const int MaxPlaintext = MaxPayload - GcmTagSize;
    public const int ProjectChunk = 8192;
    public const int MaxReadTags = 100;
    public const int MaxSubscribeTags = 4000;
    public const byte FlagEncrypted = 0x01;

    public static bool IsHandshake(MsgType t) => t is >= MsgType.HELLO and <= MsgType.AUTH_FAIL;

    public static byte[] Header(MsgType type, byte flags, uint seq, int length)
    {
        var h = new byte[HeaderSize];
        h[0] = (byte)'W';
        h[1] = (byte)'H';
        h[2] = ProtocolVersion;
        h[3] = (byte)type;
        h[4] = flags;
        h[5] = 0;
        BinaryPrimitives.WriteUInt32LittleEndian(h.AsSpan(6), seq);
        BinaryPrimitives.WriteUInt16LittleEndian(h.AsSpan(10), checked((ushort)length));
        return h;
    }

    public static (byte Type, byte Flags, uint Seq, int Length) ParseHeader(ReadOnlySpan<byte> h)
    {
        if (h[0] != 'W' || h[1] != 'H') throw new ProtocolException("bad magic");
        if (h[2] != ProtocolVersion) throw new ProtocolException($"unsupported protocol version {h[2]}");
        if (h[5] != 0 || (h[4] & ~FlagEncrypted) != 0) throw new ProtocolException("reserved bits set");
        int length = BinaryPrimitives.ReadUInt16LittleEndian(h[10..]);
        if (length > MaxPayload) throw new ProtocolException($"payload length {length} exceeds {MaxPayload}");
        return (h[3], h[4], BinaryPrimitives.ReadUInt32LittleEndian(h[6..]), length);
    }
}

/// <summary>Handshake MACs and session key derivation (PROTOCOL.md §2–3).</summary>
public static class LinkCrypto
{
    public const int KeySize = 32;
    public const int NonceSize = 16;
    public const int MacSize = 32;

    public static byte[] ParseKey(string hex)
    {
        byte[] key;
        try { key = Convert.FromHexString(hex.Trim()); }
        catch (FormatException) { throw new FormatException("key must be hex encoded"); }
        if (key.Length != KeySize) throw new FormatException($"key must be {KeySize} bytes ({KeySize * 2} hex characters)");
        return key;
    }

    public static byte[] AuthContext(byte[] clientNonce, byte[] serverNonce, string clientId, string receiverId) =>
        new PayloadWriter().Raw(clientNonce).Raw(serverNonce).Str8(clientId).Str8(receiverId).ToArray();

    public static byte[] ClientMac(byte[] key, byte[] ctx) => HMACSHA256.HashData(key, (byte[])[.. "WHMI-AUTH-C"u8, .. ctx]);
    public static byte[] ServerMac(byte[] key, byte[] ctx) => HMACSHA256.HashData(key, (byte[])[.. "WHMI-AUTH-S"u8, .. ctx]);

    public static (byte[] C2S, byte[] S2C) DeriveKeys(byte[] key, byte[] clientNonce, byte[] serverNonce)
    {
        byte[] salt = [.. clientNonce, .. serverNonce];
        return (HKDF.DeriveKey(HashAlgorithmName.SHA256, key, KeySize, salt, Encoding.ASCII.GetBytes("WHMI-Link v1 c2s")),
                HKDF.DeriveKey(HashAlgorithmName.SHA256, key, KeySize, salt, Encoding.ASCII.GetBytes("WHMI-Link v1 s2c")));
    }
}

/// <summary>AES-256-GCM for one direction of a session.</summary>
public sealed class FrameCipher(byte[] key, string direction) : IDisposable
{
    private readonly AesGcm _aead = new(key, Frames.GcmTagSize);
    private readonly byte[] _dir = [.. Encoding.ASCII.GetBytes(direction), 0];

    private byte[] Nonce(uint seq)
    {
        var n = new byte[12];
        _dir.CopyTo(n, 0);
        BinaryPrimitives.WriteUInt64LittleEndian(n.AsSpan(4), seq);
        return n;
    }

    public byte[] Seal(uint seq, byte[] header, byte[] plaintext)
    {
        var output = new byte[plaintext.Length + Frames.GcmTagSize];
        _aead.Encrypt(Nonce(seq), plaintext, output.AsSpan(0, plaintext.Length), output.AsSpan(plaintext.Length), header);
        return output;
    }

    public byte[] Open(uint seq, byte[] header, byte[] body)
    {
        if (body.Length < Frames.GcmTagSize) throw new ProtocolException("frame too short");
        var plain = new byte[body.Length - Frames.GcmTagSize];
        try
        {
            _aead.Decrypt(Nonce(seq), body.AsSpan(0, plain.Length), body.AsSpan(plain.Length), plain, header);
        }
        catch (AuthenticationTagMismatchException)
        {
            throw new ProtocolException("frame authentication failed");
        }
        return plain;
    }

    public void Dispose() => _aead.Dispose();
}

public sealed record TagValue(int TagId, Quality Quality, long TimestampMs, DataType DataType, object Value)
{
    internal static TagValue Read(PayloadReader r)
    {
        int id = r.U16();
        byte q = r.U8();
        var quality = Enum.IsDefined(typeof(Quality), q) ? (Quality)q : (q & 0x80) != 0 ? Quality.BAD : Quality.UNCERTAIN;
        long ts = r.I64();
        var (type, value) = r.Value();
        return new TagValue(id, quality, ts, type, value);
    }
}

public sealed record SessionInfo(Role Role, int HeartbeatMs, int WatchdogMs, byte[] ProjectHash, uint ProjectSize)
{
    public bool HasProject => ProjectHash.Any(b => b != 0);

    internal static SessionInfo Decode(byte[] payload)
    {
        var r = new PayloadReader(payload);
        byte role = r.U8();
        if (!Enum.IsDefined(typeof(Role), role)) throw new ProtocolException("invalid role");
        var info = new SessionInfo((Role)role, r.U16(), r.U16(), r.Raw(32), r.U32());
        r.Done();
        return info;
    }
}
