using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text.Json;

namespace RvcLauncher;

internal sealed class LauncherSettings
{
    public string Language { get; set; } = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName switch
    {
        "ja" => "ja",
        "en" => "en",
        _ => "zh",
    };

    public string Repository { get; set; } = typeof(LauncherSettings).Assembly
        .GetCustomAttributes<AssemblyMetadataAttribute>()
        .FirstOrDefault(attribute => attribute.Key == "UpdateRepository")?.Value ?? "";
    public bool AutoCheck { get; set; } = true;
    public DateTimeOffset LastCheck { get; set; }
    public string? LatestTag { get; set; }
    public WallpaperPlacement Wallpaper { get; set; } = new();

    public static LauncherSettings Load(string path)
    {
        try
        {
            if (File.Exists(path))
                return JsonSerializer.Deserialize<LauncherSettings>(File.ReadAllText(path)) ?? new();
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            // A corrupt or unreadable preferences file should not prevent starting the workbench.
        }
        return new();
    }

    public void Save(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        string temporary = path + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(this));
        File.Move(temporary, path, overwrite: true);
    }
}
