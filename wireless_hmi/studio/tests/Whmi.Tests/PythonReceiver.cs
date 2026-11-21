using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using Whmi.Core;
using Whmi.Link;

namespace Whmi.Tests;

/// <summary>Runs the Python receiver as a real process and talks to it with the C# client.</summary>
public sealed class PythonReceiver : IDisposable
{
    private Process? _process;

    public PythonReceiver()
    {
        if (!TestPaths.PythonAvailable) return;
        DataDir = TestPaths.NewTempDir();
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        Port = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        var init = TestPaths.Python("-m", "whmi.tools.cli", "receiver", "init", "--data-dir", DataDir, "--id", "RX-NET",
            "--port", Port.ToString(), "--client", "studio:engineer", "--client", "panel1:operator", "--client", "viewer:viewer");
        if (init is not { Code: 0 }) throw new InvalidOperationException($"receiver init failed: {init?.Err}");

        StartProcess();
    }

    /// <summary>Start (or restart) the receiver process on the same port and data directory.</summary>
    public void StartProcess()
    {
        var psi = new ProcessStartInfo("python3") { RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var a in new[] { "-m", "whmi.receiver", "--config", Path.Combine(DataDir, "receiver.json"), "--log-level", "WARNING" })
            psi.ArgumentList.Add(a);
        psi.Environment["PYTHONPATH"] = Path.Combine(TestPaths.Root, "src");
        _process = Process.Start(psi)!;
        _process.OutputDataReceived += (_, _) => { };
        _process.ErrorDataReceived += (_, _) => { };
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();

        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (DateTime.UtcNow < deadline)
        {
            try
            {
                using var probe = new TcpClient();
                probe.Connect(IPAddress.Loopback, Port);
                Available = true;
                return;
            }
            catch (SocketException) { Thread.Sleep(100); }
        }
        throw new InvalidOperationException("python receiver did not start");
    }

    /// <summary>Kill the receiver process (simulates the receiver losing power or WiFi).</summary>
    public void StopProcess()
    {
        if (_process is { HasExited: false })
        {
            _process.Kill(entireProcessTree: true);
            _process.WaitForExit(5000);
        }
        _process?.Dispose();
        _process = null;
    }

    public bool Available { get; private set; }
    public string DataDir { get; } = "";
    public int Port { get; }

    public byte[] Key(string clientId) => LinkCrypto.ParseKey(File.ReadAllText(Path.Combine(DataDir, clientId + ".key")));

    public LinkClient Client(string clientId, byte[]? key = null) =>
        new("127.0.0.1", clientId, key ?? Key(clientId), Port, ClientKind.STUDIO);

    public void Dispose() => StopProcess();
}
