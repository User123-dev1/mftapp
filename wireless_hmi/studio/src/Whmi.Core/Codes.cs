namespace Whmi.Core;

/// <summary>Value quality (PROTOCOL.md §4.2). Bit 7 set means bad.</summary>
public enum Quality : byte
{
    GOOD = 0x00,
    UNCERTAIN = 0x40,
    BAD = 0x80,
    BAD_COMM = 0x81,
    BAD_CONFIG = 0x82,
    BAD_NOT_CONNECTED = 0x83,
    BAD_DEVICE = 0x84,
    BAD_WAITING = 0x85,
}

public enum Status : byte
{
    OK = 0,
    BAD_TAG = 1,
    READ_ONLY = 2,
    TYPE_MISMATCH = 3,
    OUT_OF_RANGE = 4,
    NOT_PERMITTED = 5,
    COMM_FAILURE = 6,
    BUSY = 7,
    INVALID = 8,
    INTERNAL = 9,
}

public enum Role : byte
{
    VIEWER = 1,
    OPERATOR = 2,
    ENGINEER = 3,
}

public enum ClientKind : byte
{
    PANEL = 1,
    STUDIO = 2,
    TOOL = 3,
}

public static class CodeExtensions
{
    public static bool IsBad(this Quality q) => ((byte)q & 0x80) != 0;
    public static bool CanWrite(this Role r) => r >= Role.OPERATOR;
}
