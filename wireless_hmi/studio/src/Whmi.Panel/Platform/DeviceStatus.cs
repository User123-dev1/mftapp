using System.Runtime.InteropServices;

namespace Whmi.Panel.Platform;

public sealed record DeviceReading(int? BatteryPercent, bool Charging, int? WifiSignal, string? Ssid);

/// <summary>Battery and WiFi readings for the status bar.</summary>
public interface IDeviceStatus
{
    DeviceReading Read();
}

public static class DeviceStatus
{
    public static IDeviceStatus Create() =>
        OperatingSystem.IsWindows() ? new WindowsDeviceStatus() : new NoDeviceStatus();
}

public sealed class NoDeviceStatus : IDeviceStatus
{
    public DeviceReading Read() => new(null, false, null, null);
}

/// <summary>Reads battery via GetSystemPowerStatus and WiFi signal via the native WLAN API.</summary>
public sealed partial class WindowsDeviceStatus : IDeviceStatus
{
    public DeviceReading Read()
    {
        int? battery = null;
        bool charging = false;
        try
        {
            if (GetSystemPowerStatus(out var ps))
            {
                charging = ps.ACLineStatus == 1;
                bool noBattery = (ps.BatteryFlag & 128) != 0;
                if (!noBattery && ps.BatteryLifePercent <= 100) battery = ps.BatteryLifePercent;
            }
        }
        catch (Exception e) when (e is DllNotFoundException or EntryPointNotFoundException)
        {
        }

        var (signal, ssid) = ReadWifi();
        return new DeviceReading(battery, charging, signal, ssid);
    }

    // ------------------------------------------------------------------ battery

    [StructLayout(LayoutKind.Sequential)]
    private struct SystemPowerStatus
    {
        public byte ACLineStatus;
        public byte BatteryFlag;
        public byte BatteryLifePercent;
        public byte SystemStatusFlag;
        public int BatteryLifeTime;
        public int BatteryFullLifeTime;
    }

    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetSystemPowerStatus(out SystemPowerStatus status);

    // ------------------------------------------------------------------ WiFi (wlanapi.dll)

    private const int OpcodeCurrentConnection = 7;   // wlan_intf_opcode_current_connection
    private const int InterfaceInfoSize = 532;        // GUID + WCHAR[256] + state
    private const int OffsetSsidLength = 520;         // WLAN_CONNECTION_ATTRIBUTES.wlanAssociationAttributes.dot11Ssid
    private const int OffsetSignalQuality = 576;      // ...wlanAssociationAttributes.wlanSignalQuality (0-100)

    [LibraryImport("wlanapi.dll")]
    private static partial uint WlanOpenHandle(uint clientVersion, IntPtr reserved, out uint negotiatedVersion, out IntPtr handle);

    [LibraryImport("wlanapi.dll")]
    private static partial uint WlanCloseHandle(IntPtr handle, IntPtr reserved);

    [LibraryImport("wlanapi.dll")]
    private static partial uint WlanEnumInterfaces(IntPtr handle, IntPtr reserved, out IntPtr interfaceList);

    [LibraryImport("wlanapi.dll")]
    private static partial uint WlanQueryInterface(IntPtr handle, ref Guid interfaceGuid, int opCode, IntPtr reserved,
        out int dataSize, out IntPtr data, IntPtr opcodeValueType);

    [LibraryImport("wlanapi.dll")]
    private static partial void WlanFreeMemory(IntPtr memory);

    private static (int?, string?) ReadWifi()
    {
        IntPtr handle = IntPtr.Zero, list = IntPtr.Zero;
        try
        {
            if (WlanOpenHandle(2, IntPtr.Zero, out _, out handle) != 0) return (null, null);
            if (WlanEnumInterfaces(handle, IntPtr.Zero, out list) != 0) return (null, null);
            int count = Marshal.ReadInt32(list);
            for (int i = 0; i < count; i++)
            {
                var item = list + 8 + i * InterfaceInfoSize;
                var guid = Marshal.PtrToStructure<Guid>(item);
                if (WlanQueryInterface(handle, ref guid, OpcodeCurrentConnection, IntPtr.Zero, out int size, out IntPtr data, IntPtr.Zero) != 0)
                    continue; // not connected
                try
                {
                    if (size < OffsetSignalQuality + 4) continue;
                    int quality = Marshal.ReadInt32(data, OffsetSignalQuality);
                    int ssidLen = Math.Clamp(Marshal.ReadInt32(data, OffsetSsidLength), 0, 32);
                    var ssidBytes = new byte[ssidLen];
                    Marshal.Copy(data + OffsetSsidLength + 4, ssidBytes, 0, ssidLen);
                    if (quality is >= 0 and <= 100)
                        return (quality, System.Text.Encoding.UTF8.GetString(ssidBytes));
                }
                finally
                {
                    WlanFreeMemory(data);
                }
            }
            return (null, null);
        }
        catch (Exception e) when (e is DllNotFoundException or EntryPointNotFoundException)
        {
            return (null, null); // e.g. Windows Server without the WLAN service
        }
        finally
        {
            if (list != IntPtr.Zero) WlanFreeMemory(list);
            if (handle != IntPtr.Zero) WlanCloseHandle(handle, IntPtr.Zero);
        }
    }
}
