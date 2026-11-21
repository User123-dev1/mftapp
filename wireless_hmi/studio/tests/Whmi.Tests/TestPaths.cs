using System.Diagnostics;

namespace Whmi.Tests;

internal static class TestPaths
{
    /// <summary>The wireless_hmi directory (holds examples/ and the Python sources).</summary>
    public static string Root { get; } = FindRoot();

    public static string Example => Path.Combine(Root, "examples", "demo_line.whp");

    private static string FindRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "examples", "demo_line.whp")))
            dir = dir.Parent;
        return dir?.FullName ?? throw new InvalidOperationException("cannot find wireless_hmi root");
    }

    public static string NewTempDir()
    {
        var d = Path.Combine(Path.GetTempPath(), "whmi-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(d);
        return d;
    }

    /// <summary>Run the Python whmi package (python3 -m ...). Returns null when Python/whmi is unavailable.</summary>
    public static (int Code, string Out, string Err)? Python(params string[] args)
    {
        var psi = new ProcessStartInfo("python3") { RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var a in args) psi.ArgumentList.Add(a);
        psi.Environment["PYTHONPATH"] = Path.Combine(Root, "src");
        try
        {
            using var p = Process.Start(psi)!;
            var stdout = p.StandardOutput.ReadToEndAsync();
            var stderr = p.StandardError.ReadToEndAsync();
            if (!p.WaitForExit(60_000)) { p.Kill(); return null; }
            return (p.ExitCode, stdout.Result, stderr.Result);
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return null;
        }
    }

    public static bool PythonAvailable { get; } =
        Python("-c", "import whmi, cryptography") is { Code: 0 };
}
