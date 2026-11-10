using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;

namespace RvcLauncher;

internal sealed class Workbench(string root, string dataDirectory)
{
    public const int Port = 5173;
    public static readonly Uri Page = new($"http://127.0.0.1:{Port}/rvc");
    private readonly string backend = Path.Combine(root, "voiceforge-backend");
    private readonly object logLock = new();
    private Process? service;

    private static readonly HttpClient Client = new(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
    {
        Timeout = TimeSpan.FromSeconds(2),
        MaxResponseContentBufferSize = 128 * 1024,
    };

    public bool HasProject => File.Exists(Path.Combine(backend, "main.py"));
    public bool NeedsSetup => !File.Exists(Path.Combine(backend, "venv", "Scripts", "python.exe"))
        || !File.Exists(Path.Combine(root, "frontend", "dist", "index.html"));
    public bool OwnsService => service is { HasExited: false };

    public static string FindRoot()
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            if (File.Exists(Path.Combine(directory.FullName, "start-rvc.ps1"))) return directory.FullName;
        }
        return AppContext.BaseDirectory;
    }

    public static async Task<bool> IsReadyAsync(CancellationToken cancellationToken)
    {
        try
        {
            using var health = JsonDocument.Parse(await Client.GetStringAsync(new Uri(Page, "/api/rvc/health"), cancellationToken));
            if (!health.RootElement.TryGetProperty("rvc", out var rvc) || rvc.GetString() != "ready") return false;
            string html = await Client.GetStringAsync(Page, cancellationToken);
            return html.Contains("id=\"root\"", StringComparison.Ordinal);
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or JsonException or InvalidOperationException)
        {
            return false;
        }
    }

    public async Task<bool> StartAsync(CancellationToken cancellationToken)
    {
        if (await IsReadyAsync(cancellationToken)) return true;
        cancellationToken.ThrowIfCancellationRequested();
        if (!OwnsService)
        {
            service?.Dispose();
            var info = ProcessInfo(Path.Combine(backend, "venv", "Scripts", "python.exe"), backend);
            info.Environment["RVC_SERVE_FRONTEND"] = "true";
            info.Environment["PYTHONUNBUFFERED"] = "1";
            foreach (string argument in new[] { "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", Port.ToString(), "--no-access-log" })
                info.ArgumentList.Add(argument);
            service = StartLogged(info, "workbench.log");
        }

        var elapsed = Stopwatch.StartNew();
        while (elapsed.Elapsed < TimeSpan.FromSeconds(24))
        {
            if (await IsReadyAsync(cancellationToken)) return true;
            if (service is null || service.HasExited) break;
            await Task.Delay(300, cancellationToken);
        }
        StopOwned();
        return false;
    }

    public async Task<bool> SetupAsync(CancellationToken cancellationToken)
    {
        string powershell = Path.Combine(Environment.SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe");
        var info = ProcessInfo(powershell, root);
        foreach (string argument in new[] { "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", Path.Combine(root, "setup-rvc.ps1") })
            info.ArgumentList.Add(argument);
        using var setup = StartLogged(info, "setup.log");
        await setup.WaitForExitAsync(cancellationToken);
        return setup.ExitCode == 0 && !NeedsSetup;
    }

    public void StopOwned()
    {
        try
        {
            if (service is { HasExited: false }) service.Kill(entireProcessTree: true);
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            // The process may finish while the window closes.
        }
        finally
        {
            service?.Dispose();
            service = null;
        }
    }

    private ProcessStartInfo ProcessInfo(string fileName, string directory)
    {
        Directory.CreateDirectory(dataDirectory);
        var info = new ProcessStartInfo(fileName)
        {
            WorkingDirectory = directory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        info.Environment["TEMP"] = dataDirectory;
        info.Environment["TMP"] = dataDirectory;
        return info;
    }

    private Process StartLogged(ProcessStartInfo info, string logName)
    {
        string path = Path.Combine(dataDirectory, logName);
        File.WriteAllText(path, $"[{DateTimeOffset.Now:O}]{Environment.NewLine}");
        var process = new Process { StartInfo = info };
        process.OutputDataReceived += (_, args) => Append(args.Data);
        process.ErrorDataReceived += (_, args) => Append(args.Data);
        try
        {
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            return process;
        }
        catch
        {
            process.Dispose();
            throw;
        }

        void Append(string? text)
        {
            if (text is null) return;
            try
            {
                lock (logLock) File.AppendAllText(path, text + Environment.NewLine);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                // A log write error should not terminate the service.
            }
        }
    }
}
