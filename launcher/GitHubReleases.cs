using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace RvcLauncher;

internal static partial class GitHubReleases
{
    private static readonly HttpClient Client = new(new HttpClientHandler { AllowAutoRedirect = false })
    {
        Timeout = TimeSpan.FromSeconds(12),
        MaxResponseContentBufferSize = 2 * 1024 * 1024,
    };

    public static bool TryRepository(string input, out string repository)
    {
        repository = input.Trim().TrimEnd('/');
        if (repository.Length == 0) return true;
        if (Uri.TryCreate(repository, UriKind.Absolute, out var uri))
        {
            if (uri.Scheme != Uri.UriSchemeHttps || uri.Host != "github.com" || !uri.IsDefaultPort ||
                uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0)
                return false;
            repository = uri.AbsolutePath.Trim('/');
        }
        if (repository.EndsWith(".git", StringComparison.OrdinalIgnoreCase))
            repository = repository[..^4];
        return RepositoryPattern().IsMatch(repository);
    }

    public static Version? TagVersion(string? tag)
    {
        if (tag is null) return null;
        string number = tag.TrimStart('v', 'V').Split('+', 2)[0];
        return Version.TryParse(number, out var version)
            ? new Version(version.Major, version.Minor, Math.Max(version.Build, 0))
            : null;
    }

    public static async Task<string?> LatestTagAsync(string repository, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, $"https://api.github.com/repos/{repository}/releases/latest");
        request.Headers.UserAgent.ParseAdd("RvcDatasetStudioLauncher");
        request.Headers.Accept.ParseAdd("application/vnd.github+json");
        using var response = await Client.SendAsync(request, cancellationToken);
        if (response.StatusCode == HttpStatusCode.NotFound) return null;
        response.EnsureSuccessStatusCode();
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellationToken));
        return json.RootElement.GetProperty("tag_name").GetString();
    }

    public static Uri ReleaseUrl(string repository, string tag) =>
        new($"https://github.com/{repository}/releases/tag/{Uri.EscapeDataString(tag)}");

    [GeneratedRegex(@"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.{1,2}$)[A-Za-z0-9_.-]{1,100}$", RegexOptions.CultureInvariant)]
    private static partial Regex RepositoryPattern();
}
