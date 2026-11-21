using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using Whmi.Link;

namespace Whmi.Panel.Platform;

/// <summary>
/// Pairing and kiosk settings of this panel. The link key is encrypted with Windows DPAPI
/// (current user) so copying the settings file to another machine does not copy the key.
/// </summary>
public sealed class PanelSettings
{
    public string Host { get; set; } = "192.168.50.1";
    public int Port { get; set; } = Frames.DefaultPort;
    public string ClientId { get; set; } = "panel1";

    /// <summary>Base64 key: DPAPI-protected on Windows ("dpapi:" prefix), plain elsewhere ("plain:").</summary>
    public string StoredKey { get; set; } = "";

    public bool Fullscreen { get; set; }
    public bool Kiosk { get; set; }

    /// <summary>PIN protecting settings and exit in kiosk mode; empty = no PIN.</summary>
    public string Pin { get; set; } = "";

    [JsonIgnore]
    public bool IsPaired => StoredKey.Length > 0 && Host.Length > 0 && ClientId.Length > 0;

    public static string DefaultDirectory =>
        Environment.GetEnvironmentVariable("WHMI_PANEL_HOME") is { Length: > 0 } dir
            ? dir
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "WhmiPanel");

    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public static PanelSettings Load(string directory)
    {
        var path = Path.Combine(directory, "panel.json");
        try
        {
            if (File.Exists(path))
                return JsonSerializer.Deserialize<PanelSettings>(File.ReadAllText(path), Json) ?? new PanelSettings();
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            // unreadable settings: start unpaired rather than crash on the plant floor
        }
        return new PanelSettings();
    }

    public void Save(string directory)
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "panel.json");
        File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(this, Json));
        File.Move(path + ".tmp", path, overwrite: true);
    }

    public void SetKey(byte[] key)
    {
        StoredKey = OperatingSystem.IsWindows()
            ? "dpapi:" + Convert.ToBase64String(ProtectedData.Protect(key, null, DataProtectionScope.CurrentUser))
            : "plain:" + Convert.ToBase64String(key);
    }

    public byte[]? GetKey()
    {
        try
        {
            if (StoredKey.StartsWith("dpapi:") && OperatingSystem.IsWindows())
                return ProtectedData.Unprotect(Convert.FromBase64String(StoredKey[6..]), null, DataProtectionScope.CurrentUser);
            if (StoredKey.StartsWith("plain:"))
                return Convert.FromBase64String(StoredKey[6..]);
        }
        catch (Exception e) when (e is FormatException or CryptographicException)
        {
        }
        return null;
    }
}
