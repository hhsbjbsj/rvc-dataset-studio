using System.IO.Compression;
using RvcLauncher;

string fixture = Path.Combine(Path.GetTempPath(), "rvc-updater-tests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(fixture);
try
{
    string root = Path.Combine(fixture, "studio with spaces 中文");
    Directory.CreateDirectory(root);
    File.WriteAllText(Path.Combine(root, "start-rvc.ps1"), "start");
    string stage = Path.Combine(UpdateInstaller.Runtime(root), "updates", "test");
    string payload = Path.Combine(stage, "payload");
    Directory.CreateDirectory(payload);
    string runtime = UpdateInstaller.Runtime(root);
    File.WriteAllText(Path.Combine(root, "README.md"), "old");
    File.WriteAllText(Path.Combine(root, "RvcLauncher.exe"), "old-exe");
    File.WriteAllText(Path.Combine(runtime, "settings.json"), "personal-settings");
    File.WriteAllText(Path.Combine(root, "voiceforge-backend", "voiceforge.db"), "personal-projects");
    File.WriteAllText(Path.Combine(payload, "README.md"), "new");
    File.WriteAllText(Path.Combine(payload, "RvcLauncher.exe"), "new-exe");
    File.WriteAllText(Path.Combine(payload, "LICENSE"), "license");
    var plan = new UpdatePlan(root, stage, "v0.1.1", Environment.ProcessId, 0);
    using (var locked = new FileStream(Path.Combine(root, "RvcLauncher.exe"), FileMode.Open, FileAccess.Read, FileShare.Read))
    {
        try { UpdateInstaller.ApplyFiles(plan); throw new Exception("Expected an install failure."); }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
    }
    Require(File.ReadAllText(Path.Combine(root, "README.md")) == "old", "Roll back files already replaced.");
    Require(!File.Exists(Path.Combine(root, "LICENSE")), "Remove new files during rollback.");
    Require(File.ReadAllText(Path.Combine(runtime, "settings.json")) == "personal-settings", "Keep user settings.");
    Require(File.ReadAllText(Path.Combine(root, "voiceforge-backend", "voiceforge.db")) == "personal-projects", "Keep project data.");

    // Use a fresh staging directory after the failed transaction.
    string next = Path.Combine(runtime, "updates", "next");
    Directory.CreateDirectory(Path.Combine(next, "payload"));
    File.WriteAllText(Path.Combine(next, "payload", "README.md"), "updated");
    File.WriteAllText(Path.Combine(next, "payload", "RvcLauncher.exe"), "updated-exe");
    UpdateInstaller.ApplyFiles(plan with { Stage = next });
    Require(File.ReadAllText(Path.Combine(root, "README.md")) == "updated", "Install a subsequent update.");
    Require(File.ReadAllText(Path.Combine(runtime, "settings.json")) == "personal-settings", "Preserve settings on success.");

    foreach (string unsafePath in new[] { "../escape.txt", "voiceforge-backend/voiceforge.db", "voiceforge-backend/tmp/erase.txt", "frontend/.env" })
    {
        string attempt = Path.Combine(runtime, "updates", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(attempt);
        using (var zip = ZipFile.Open(Path.Combine(attempt, "download.zip"), ZipArchiveMode.Create))
        using (var writer = new StreamWriter(zip.CreateEntry("RVC-Dataset-Studio/" + unsafePath).Open()))
            writer.Write("bad");
        try { UpdateInstaller.Prepare(root, attempt, "v0.1.1"); throw new Exception("Unsafe archive was accepted."); }
        catch (InvalidDataException) { }
    }
    using (var lease = UpdateInstaller.AcquireLease(root))
    {
        try { using var second = UpdateInstaller.AcquireLease(root); throw new Exception("Concurrent update was accepted."); }
        catch (IOException) { }
    }
    Console.WriteLine("PASS: partial-failure rollback, retry, user-data preservation, unsafe archive rejection, update lock.");
}
finally
{
    // Only this test's randomly created directory is eligible for cleanup.
    if (Path.GetFullPath(fixture).StartsWith(Path.GetFullPath(Path.GetTempPath()), StringComparison.OrdinalIgnoreCase))
        Directory.Delete(fixture, true);
}

static void Require(bool condition, string message)
{
    if (!condition) throw new Exception(message);
}
