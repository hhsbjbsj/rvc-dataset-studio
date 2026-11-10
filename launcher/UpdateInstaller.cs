using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Text.Json;

namespace RvcLauncher;

internal sealed record UpdatePlan(string Root, string Stage, string Tag, int ParentId, long ParentStarted);

internal static class UpdateInstaller
{
    public static string Runtime(string root) => Path.Combine(root, "voiceforge-backend", "tmp", "launcher");
    public static FileStream AcquireLease(string root)
    {
        Directory.CreateDirectory(Runtime(root));
        return new(Path.Combine(Runtime(root), "update.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
    }

    public static UpdatePlan Prepare(string root, string stage, string tag)
    {
        var plan = new UpdatePlan(Path.GetFullPath(root), Path.GetFullPath(stage), tag,
            Environment.ProcessId, Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks);
        ValidatePlan(plan);
        string payload = Path.Combine(stage, "payload");
        Directory.CreateDirectory(payload);
        using var archive = ZipFile.OpenRead(Path.Combine(stage, "download.zip"));
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (var entry in archive.Entries)
        {
            string name = entry.FullName.Replace('\\', '/');
            if (!name.StartsWith("RVC-Dataset-Studio/", StringComparison.Ordinal))
                throw new InvalidDataException("Unexpected package root.");
            string relative = name["RVC-Dataset-Studio/".Length..];
            if (relative.Length == 0 || name.EndsWith('/')) continue;
            if (!AllowedFile(relative) || !seen.Add(relative) || ((entry.ExternalAttributes >> 16) & 0xf000) == 0xa000)
                throw new InvalidDataException("Unsafe or duplicate update path.");
            total += entry.Length;
            if (total > 1024L * 1024 * 1024 || seen.Count > 10000)
                throw new InvalidDataException("Update package is too large.");
            string destination = Path.Combine(payload, relative.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            entry.ExtractToFile(destination);
        }
        foreach (string file in new[] { "RvcLauncher.exe", "frontend/dist/index.html", "voiceforge-backend/main.py",
                     "voiceforge-backend/requirements-rvc.txt", "setup-rvc.ps1", "start-rvc.ps1" })
            if (!seen.Contains(file)) throw new InvalidDataException($"Missing update file: {file}");
        string? version = FileVersionInfo.GetVersionInfo(Path.Combine(payload, "RvcLauncher.exe")).ProductVersion;
        if (GitHubReleases.TagVersion(version) != GitHubReleases.TagVersion(tag))
            throw new InvalidDataException("The launcher version does not match the release.");
        File.Copy(Environment.ProcessPath!, Path.Combine(stage, "installer.exe"));
        File.WriteAllText(Path.Combine(stage, "plan.json"), JsonSerializer.Serialize(plan));
        return plan;
    }

    public static void StartHelper(UpdatePlan plan)
    {
        var info = new ProcessStartInfo(Path.Combine(plan.Stage, "installer.exe"))
        {
            UseShellExecute = false,
            WorkingDirectory = plan.Stage,
            CreateNoWindow = true,
        };
        info.ArgumentList.Add("--apply-update");
        info.ArgumentList.Add(Path.Combine(plan.Stage, "plan.json"));
        using var helper = Process.Start(info) ?? throw new IOException("Could not start the update installer.");
    }

    public static void Run(string planPath)
    {
        var plan = JsonSerializer.Deserialize<UpdatePlan>(File.ReadAllText(planPath))
            ?? throw new InvalidDataException("Missing update plan.");
        ValidatePlan(plan);
        string runtime = Runtime(plan.Root);
        bool success = false;
        try
        {
            try
            {
                using var parent = Process.GetProcessById(plan.ParentId);
                if (parent.StartTime.ToUniversalTime().Ticks == plan.ParentStarted && !parent.WaitForExit(60000))
                    throw new IOException("The launcher has not exited.");
            }
            catch (ArgumentException) { /* The original launcher has already exited. */ }
            using var lease = AcquireLease(plan.Root);
            foreach (var process in Process.GetProcessesByName("RvcLauncher"))
            {
                using (process)
                    if (string.Equals(process.MainModule?.FileName, Path.Combine(plan.Root, "RvcLauncher.exe"), StringComparison.OrdinalIgnoreCase))
                        throw new IOException("Close the other launcher before installing.");
            }
            // Hold the same byte-range lock used by launcher_server throughout installation.
            using var backend = new FileStream(Path.Combine(runtime, "workbench.lock"), FileMode.OpenOrCreate,
                FileAccess.ReadWrite, FileShare.ReadWrite);
            for (int attempt = 0; ; attempt++)
            {
                try { backend.Lock(0, 1); break; }
                catch (IOException) when (attempt < 99) { Thread.Sleep(100); }
            }
            try { ApplyFiles(plan); }
            finally { backend.Unlock(0, 1); }
            success = true;
        }
        catch (Exception exception)
        {
            File.WriteAllText(Path.Combine(runtime, "update.log"), exception.ToString());
        }
        File.WriteAllText(Path.Combine(runtime, "update-result.json"), JsonSerializer.Serialize(new { success, tag = plan.Tag }));
        Process.Start(new ProcessStartInfo(Path.Combine(plan.Root, "RvcLauncher.exe"))
        {
            UseShellExecute = true,
            WorkingDirectory = plan.Root,
        })?.Dispose();
    }

    internal static void ApplyFiles(UpdatePlan plan)
    {
        ValidatePlan(plan);
        string payload = Path.Combine(plan.Stage, "payload");
        string backup = Path.Combine(plan.Stage, "backup");
        string[] files = Directory.GetFiles(payload, "*", SearchOption.AllDirectories)
            .Select(file => Path.GetRelativePath(payload, file)).Order(StringComparer.OrdinalIgnoreCase).ToArray();
        var existed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string relative in files)
        {
            if (!AllowedFile(relative.Replace('\\', '/'))) throw new InvalidDataException("Unsafe install path.");
            string target = Path.Combine(plan.Root, relative);
            for (string? directory = Path.GetDirectoryName(target); directory is not null; directory = Path.GetDirectoryName(directory))
            {
                if (Directory.Exists(directory) && (File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("An update directory is a link.");
                if (directory.Equals(plan.Root, StringComparison.OrdinalIgnoreCase)) break;
            }
            if (!File.Exists(target)) continue;
            if ((File.GetAttributes(target) & FileAttributes.ReparsePoint) != 0) throw new IOException("An update file is a link.");
            string saved = Path.Combine(backup, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(saved)!);
            File.Copy(target, saved);
            existed.Add(relative);
        }
        string requirements = Path.Combine("voiceforge-backend", "requirements-rvc.txt");
        bool newDependencies = existed.Contains(requirements) &&
            File.ReadAllText(Path.Combine(plan.Root, requirements)) != File.ReadAllText(Path.Combine(payload, requirements));
        var changed = new List<string>();
        try
        {
            foreach (string relative in files)
            {
                string target = Path.Combine(plan.Root, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                // A same-volume rename replaces a whole file, even if the process is interrupted.
                File.Move(Path.Combine(payload, relative), target, true);
                changed.Add(relative);
            }
            if (newDependencies) File.WriteAllText(Path.Combine(Runtime(plan.Root), "needs-setup"), plan.Tag);
        }
        catch
        {
            foreach (string relative in Enumerable.Reverse(changed))
            {
                string target = Path.Combine(plan.Root, relative);
                if (existed.Contains(relative)) File.Copy(Path.Combine(backup, relative), target, true);
                else File.Delete(target);
            }
            throw;
        }
    }

    private static void ValidatePlan(UpdatePlan plan)
    {
        string updates = Path.GetFullPath(Path.Combine(Runtime(plan.Root), "updates")) + Path.DirectorySeparatorChar;
        if (!Path.GetFullPath(plan.Stage).StartsWith(updates, StringComparison.OrdinalIgnoreCase) ||
            !File.Exists(Path.Combine(plan.Root, "start-rvc.ps1")) || GitHubReleases.TagVersion(plan.Tag) is null)
            throw new InvalidDataException("Unexpected installation path or version.");
    }

    private static bool AllowedFile(string relative)
    {
        string[] parts = relative.Split('/');
        if (parts.Any(part => part.Length == 0 || part is "." or ".." || part.Contains(':'))) return false;
        if (parts.Any(part => part.ToLowerInvariant() is ".git" or ".env" or "venv" or "node_modules" or ".npm"
                or "tmp" or "outputs" or "__pycache__" or "bin" or "obj")) return false;
        if (Path.GetExtension(relative).ToLowerInvariant() is ".db" or ".sqlite" or ".wav" or ".mp3" or ".mp4") return false;
        return parts[0] is "frontend" or "voiceforge-backend" or "launcher" ||
            relative is "RvcLauncher.exe" or "README.md" or "README.zh-CN.md" or "LICENSE"
                or "start-rvc.ps1" or "setup-rvc.ps1" or "build-launcher.ps1" or "package-release.ps1" or "CHANGELOG.md";
    }
}
