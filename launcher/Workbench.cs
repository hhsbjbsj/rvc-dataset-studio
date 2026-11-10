using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;

namespace RvcLauncher;

internal sealed class Workbench(string root, string dataDirectory)
{
    private const int PreferredPort = 5173;
    private const int AlreadyRunningExitCode = 3;
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

    public async Task<Uri?> FindPageAsync(CancellationToken cancellationToken)
    {
        if (!HasProject) return null;
        return await FindManagedPageAsync(cancellationToken)
            ?? await CheckPageAsync(PreferredPort, null, cancellationToken);
    }

    private async Task<Uri?> FindManagedPageAsync(CancellationToken cancellationToken)
    {
        Endpoint? endpoint;
        try
        {
            string path = Path.Combine(dataDirectory, "workbench-endpoint.json");
            using var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
            endpoint = JsonSerializer.Deserialize<Endpoint>(stream, JsonSerializerOptions.Web);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            return null;
        }
        return endpoint is { Port: > 0 and <= 65535, Instance.Length: 32 }
            ? await CheckPageAsync(endpoint.Port, endpoint.Instance, cancellationToken) : null;
    }

    private static async Task<Uri?> CheckPageAsync(int port, string? instance, CancellationToken cancellationToken)
    {
        var page = new Uri($"http://127.0.0.1:{port}/rvc");
        try
        {
            using var health = JsonDocument.Parse(await Client.GetStringAsync(new Uri(page, "/api/rvc/health"), cancellationToken));
            var status = health.RootElement;
            if (!status.TryGetProperty("rvc", out var rvc) || rvc.GetString() is not ("ready" or "missing_ffmpeg")) return null;
            if (status.TryGetProperty("launcher_instance", out var runningInstance))
                return instance is not null && runningInstance.GetString() == instance ? page : null;
            if (instance is not null) return null;

            // Older releases and the development script do not publish a managed endpoint.
            string html = await Client.GetStringAsync(page, cancellationToken);
            return html.Contains("id=\"root\"", StringComparison.Ordinal) ? page : null;
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or JsonException or InvalidOperationException)
        {
            return null;
        }
    }

    public async Task<Uri?> StartAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        bool startedHere = !OwnsService;
        if (startedHere)
        {
            service?.Dispose();
            var info = ProcessInfo(Path.Combine(backend, "venv", "Scripts", "python.exe"), backend);
            info.Environment["PYTHONUNBUFFERED"] = "1";
            foreach (string argument in new[] { "-m", "launcher_server", PreferredPort.ToString() })
                info.ArgumentList.Add(argument);
            service = StartLogged(info, "workbench.log");
        }

        try
        {
            var elapsed = Stopwatch.StartNew();
            while (elapsed.Elapsed < TimeSpan.FromSeconds(24))
            {
                if (await FindManagedPageAsync(cancellationToken) is { } page) return page;
                if (service is null || (service.HasExited && service.ExitCode != AlreadyRunningExitCode)) break;
                await Task.Delay(300, cancellationToken);
            }
        }
        catch (OperationCanceledException)
        {
            if (startedHere) StopOwned();
            throw;
        }
        if (startedHere) StopOwned();
        return null;
    }

    private sealed record Endpoint(int Port, string? Instance);

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
        Append($"{Environment.NewLine}[{DateTimeOffset.Now:O}]");
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
