using System.Net;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;

namespace RvcLauncher;

internal sealed record ReleaseInfo(string Tag, string Notes, Uri? Download, string? Digest, long Size)
{
    public Version? Version => GitHubReleases.TagVersion(Tag);
    public bool CanInstall => Download is not null && Digest?.Length == 71 && Digest.StartsWith("sha256:", StringComparison.Ordinal);
}

internal static class GitHubReleases
{
    public const string Repository = "hhsbjbsj/rvc-dataset-studio";
    private static readonly HttpClient Client = new(new HttpClientHandler { AllowAutoRedirect = false })
    {
        Timeout = TimeSpan.FromMinutes(10),
    };

    public static Version? TagVersion(string? tag)
    {
        if (tag is null) return null;
        string number = tag.TrimStart('v', 'V').Split('+', 2)[0];
        return Version.TryParse(number, out var version)
            ? new Version(version.Major, version.Minor, Math.Max(version.Build, 0)) : null;
    }

    public static async Task<ReleaseInfo?> LatestAsync(CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        using var request = new HttpRequestMessage(HttpMethod.Get, $"https://api.github.com/repos/{Repository}/releases/latest");
        request.Headers.UserAgent.ParseAdd("RvcDatasetStudioLauncher");
        request.Headers.Accept.ParseAdd("application/vnd.github+json");
        using var response = await Client.SendAsync(request, timeout.Token);
        if (response.StatusCode == HttpStatusCode.NotFound) return null;
        response.EnsureSuccessStatusCode();
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(timeout.Token));
        var release = json.RootElement;
        string tag = release.GetProperty("tag_name").GetString()!;
        string name = $"RVC-Dataset-Studio-{tag}-windows-x64.zip";
        foreach (var asset in release.GetProperty("assets").EnumerateArray())
        {
            if (asset.GetProperty("name").GetString() != name) continue;
            var url = new Uri(asset.GetProperty("browser_download_url").GetString()!);
            if (url.Scheme != "https" || url.Host != "github.com" || !url.IsDefaultPort ||
                !url.AbsolutePath.StartsWith($"/{Repository}/releases/download/", StringComparison.Ordinal))
                throw new InvalidDataException("Unexpected release asset address.");
            string? digest = asset.TryGetProperty("digest", out var hash) ? hash.GetString() : null;
            return new(tag, release.GetProperty("body").GetString() ?? "", url, digest, asset.GetProperty("size").GetInt64());
        }
        return new(tag, release.GetProperty("body").GetString() ?? "", null, null, 0);
    }

    public static async Task DownloadAsync(ReleaseInfo release, string destination, IProgress<int> progress, CancellationToken cancellationToken)
    {
        if (!release.CanInstall || release.Size <= 0 || release.Size > 512L * 1024 * 1024)
            throw new InvalidDataException("A verified Windows update package is required.");
        Uri url = release.Download!;
        for (int redirect = 0; redirect < 6; redirect++)
        {
            if (url.Scheme != "https" || !url.IsDefaultPort || url.UserInfo.Length != 0 ||
                url.Host is not ("github.com" or "release-assets.githubusercontent.com" or "objects.githubusercontent.com"))
                throw new InvalidDataException("Unexpected download redirect.");
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.UserAgent.ParseAdd("RvcDatasetStudioLauncher");
            using var response = await Client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if ((int)response.StatusCode is >= 300 and < 400)
            {
                url = new Uri(url, response.Headers.Location ?? throw new InvalidDataException("Missing redirect."));
                continue;
            }
            response.EnsureSuccessStatusCode();
            await using var input = await response.Content.ReadAsStreamAsync(cancellationToken);
            await using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true);
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            byte[] buffer = new byte[81920];
            long received = 0;
            int count;
            while ((count = await input.ReadAsync(buffer, cancellationToken)) > 0)
            {
                received += count;
                if (received > release.Size) throw new InvalidDataException("Update package exceeds its declared size.");
                hash.AppendData(buffer, 0, count);
                await output.WriteAsync(buffer.AsMemory(0, count), cancellationToken);
                progress.Report((int)(received * 100 / release.Size));
            }
            string digest = "sha256:" + Convert.ToHexStringLower(hash.GetHashAndReset());
            if (received != release.Size || !string.Equals(digest, release.Digest, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Update checksum verification failed.");
            return;
        }
        throw new HttpRequestException("Too many download redirects.");
    }
}
