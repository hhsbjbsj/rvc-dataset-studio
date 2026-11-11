using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace RvcLauncher;

internal sealed record ReleaseInfo(string Tag, string Notes, Uri? Download, string? Digest, long Size)
{
    public Version? Version => GitHubReleases.TagVersion(Tag);
    public bool CanInstall => Download is not null && Size is > 0 and <= 512L * 1024 * 1024 && Digest?.Length == 71 &&
        Digest.StartsWith("sha256:", StringComparison.Ordinal) && Digest.Skip(7).All(Uri.IsHexDigit);
}

internal static class GitHubReleases
{
    public const string Repository = "hhsbjbsj/rvc-dataset-studio";
    private static readonly Uri LatestApi = new($"https://api.github.com/repos/{Repository}/releases/latest");
    private static readonly Uri LatestManifest = new($"https://github.com/{Repository}/releases/latest/download/rvc-update.json");
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

    public static bool TryProxyTemplate(string input, out string template)
    {
        template = input.Trim();
        if (template.Length is <= 8 or > 1024) return false;
        if (!template.Contains("{url}", StringComparison.Ordinal) && !template.Contains("{encodedUrl}", StringComparison.Ordinal))
        {
            // Accept a proxy prefix or a complete example link with a GitHub URL in its path.
            int embedded = template.IndexOf("https://github.com/", 8, StringComparison.OrdinalIgnoreCase);
            if (embedded < 0) embedded = template.IndexOf("https://api.github.com/", 8, StringComparison.OrdinalIgnoreCase);
            int encoded = template.IndexOf("https%3A%2F%2Fgithub.com%2F", 8, StringComparison.OrdinalIgnoreCase);
            if (encoded < 0) encoded = template.IndexOf("https%3A%2F%2Fapi.github.com%2F", 8, StringComparison.OrdinalIgnoreCase);
            if (embedded >= 0) template = template[..embedded] + "{url}";
            else if (encoded >= 0) template = template[..encoded] + "{encodedUrl}";
            else if (Uri.TryCreate(template, UriKind.Absolute, out var prefix) && IsHttps(prefix) && prefix.Query.Length == 0)
                template = template.TrimEnd('/') + "/{url}";
            else return false;
        }
        int authorityEnd = template.IndexOfAny(['/', '?', '#'], 8);
        if (authorityEnd < 0 || template[..authorityEnd].Contains('{') || template[..authorityEnd].Contains('}') ||
            !Uri.TryCreate(template[..authorityEnd], UriKind.Absolute, out var gateway) || !IsHttps(gateway)) return false;
        string first = Expand(template, LatestManifest);
        string second = Expand(template, LatestApi);
        return !first.Contains('{') && !first.Contains('}') &&
            Uri.TryCreate(first, UriKind.Absolute, out var asset) && IsHttps(asset) &&
            Uri.TryCreate(second, UriKind.Absolute, out var api) && IsHttps(api) &&
            string.Equals(asset.Authority, gateway.Authority, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(api.Authority, gateway.Authority, StringComparison.OrdinalIgnoreCase);
    }

    public static async Task<ReleaseInfo?> LatestAsync(CancellationToken cancellationToken, string? proxyTemplate = null)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(proxyTemplate is null ? 15 : 25));
        if (proxyTemplate is not null)
        {
            // Many gateways mirror release downloads without mirroring api.github.com.
            try
            {
                var manifest = await ReadReleaseAsync(LatestManifest, proxyTemplate, timeout.Token);
                if (manifest?.CanInstall == true) return manifest;
            }
            catch (Exception exception) when (exception is HttpRequestException or JsonException or InvalidDataException) { }
        }
        var release = await ReadReleaseAsync(LatestApi, proxyTemplate, timeout.Token);
        if (proxyTemplate is not null && release is null) throw new InvalidDataException("No release returned by the update proxy.");
        return release;
    }

    private static async Task<ReleaseInfo?> ReadReleaseAsync(Uri source, string? proxyTemplate, CancellationToken cancellationToken)
    {
        using var response = await SendAsync(source, proxyTemplate, cancellationToken);
        if (response.StatusCode == HttpStatusCode.NotFound) return null;
        response.EnsureSuccessStatusCode();
        await response.Content.LoadIntoBufferAsync(1024 * 1024, cancellationToken);
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellationToken));
        var release = json.RootElement;
        if (release.ValueKind != JsonValueKind.Object ||
            !release.TryGetProperty("tag_name", out var tagValue) || tagValue.ValueKind != JsonValueKind.String ||
            !release.TryGetProperty("assets", out var assets) || assets.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("Missing release metadata.");
        string tag = tagValue.GetString()!;
        if (!Regex.IsMatch(tag, @"^v[0-9]+\.[0-9]+\.[0-9]+$") || TagVersion(tag) is null)
            throw new InvalidDataException("Unexpected release version.");
        string notes = release.TryGetProperty("body", out var body) && body.ValueKind == JsonValueKind.String ? body.GetString()! : "";
        string name = $"RVC-Dataset-Studio-{tag}-windows-x64.zip";
        foreach (var asset in assets.EnumerateArray())
        {
            if (asset.ValueKind != JsonValueKind.Object ||
                !asset.TryGetProperty("name", out var assetName) || assetName.ValueKind != JsonValueKind.String ||
                assetName.GetString() != name) continue;
            // The repository and asset address are fixed even when a mirror rewrites URLs in the response.
            var url = new Uri($"https://github.com/{Repository}/releases/download/{tag}/{name}");
            string? digest = asset.TryGetProperty("digest", out var hash) && hash.ValueKind == JsonValueKind.String ? hash.GetString() : null;
            long size = asset.TryGetProperty("size", out var length) && length.ValueKind == JsonValueKind.Number &&
                length.TryGetInt64(out long value) ? value : 0;
            return new(tag, notes, url, digest, size);
        }
        return new(tag, notes, null, null, 0);
    }

    public static async Task DownloadAsync(ReleaseInfo release, string destination, IProgress<int> progress,
        CancellationToken cancellationToken, string? proxyTemplate = null)
    {
        if (!release.CanInstall)
            throw new InvalidDataException("A verified Windows update package is required.");
        using var response = await SendAsync(release.Download!, proxyTemplate, cancellationToken);
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
    }

    private static async Task<HttpResponseMessage> SendAsync(Uri source, string? proxyTemplate, CancellationToken cancellationToken)
    {
        if (proxyTemplate is not null && !TryProxyTemplate(proxyTemplate, out proxyTemplate))
            throw new InvalidDataException("Invalid update proxy.");
        Uri url = proxyTemplate is null ? source : new(Expand(proxyTemplate, source));
        string gateway = url.Authority;
        for (int redirect = 0; redirect < 6; redirect++)
        {
            bool official = url.IsDefaultPort && url.Host is ("github.com" or "api.github.com" or
                "release-assets.githubusercontent.com" or "objects.githubusercontent.com");
            if (!IsHttps(url) || (!official && (proxyTemplate is null ||
                !string.Equals(url.Authority, gateway, StringComparison.OrdinalIgnoreCase))))
                throw new InvalidDataException("Unexpected update redirect.");
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.UserAgent.ParseAdd("RvcDatasetStudioLauncher");
            if (source == LatestApi) request.Headers.Accept.ParseAdd("application/vnd.github+json");
            if (source == LatestApi || source == LatestManifest) request.Headers.CacheControl = new() { NoCache = true };
            var response = await Client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if ((int)response.StatusCode is not (>= 300 and < 400)) return response;
            using (response)
            {
                url = new Uri(url, response.Headers.Location ?? throw new InvalidDataException("Missing redirect."));
                if (proxyTemplate is not null && IsHttps(url) && url.IsDefaultPort && url.Host is ("github.com" or "api.github.com"))
                    url = new Uri(Expand(proxyTemplate, url));
            }
        }
        throw new HttpRequestException("Too many update redirects.");
    }

    private static string Expand(string template, Uri source) => template
        .Replace("{encodedUrl}", Uri.EscapeDataString(source.AbsoluteUri), StringComparison.Ordinal)
        .Replace("{url}", source.AbsoluteUri, StringComparison.Ordinal);

    private static bool IsHttps(Uri uri) =>
        uri.Scheme == Uri.UriSchemeHttps && uri.Host.Length != 0 && uri.UserInfo.Length == 0 && uri.Fragment.Length == 0;

}
