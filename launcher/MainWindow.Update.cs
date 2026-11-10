using System.ComponentModel;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;

namespace RvcLauncher;

public partial class MainWindow
{
    private ReleaseInfo? latestRelease;
    private bool checkingUpdate;
    private bool applyingUpdate;
    private string? updateError;
    private CancellationTokenSource? updateDownload;

    private async Task CheckIfDueAsync()
    {
        if (settings.AutoCheck && DateTimeOffset.UtcNow - settings.LastCheck >= UpdateInterval)
            await CheckAsync(false);
    }

    private async void CheckUpdates(object sender, RoutedEventArgs e) => await CheckAsync(true);

    private async Task CheckAsync(bool showDetails)
    {
        if (checkingUpdate || busy || shutdown.IsCancellationRequested) return;
        checkingUpdate = true;
        updateError = null;
        RenderUpdate();
        try
        {
            latestRelease = await GitHubReleases.LatestAsync(shutdown.Token);
            settings.LatestTag = latestRelease?.Tag;
            if (showDetails && latestRelease is not null) ShowUpdatePanel();
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or JsonException
            or KeyNotFoundException or InvalidOperationException or IOException)
        {
            updateError = "updateFailed";
        }
        finally
        {
            checkingUpdate = false;
            if (!shutdown.IsCancellationRequested)
            {
                settings.LastCheck = DateTimeOffset.UtcNow;
                if (!TrySave(settings)) updateError ??= "settingsFailed";
                RenderUpdate();
            }
        }
    }

    private void RenderUpdate()
    {
        UpdateButton.IsEnabled = !checkingUpdate && !busy;
        ReleaseButton.Visibility = latestRelease is not null ? Visibility.Visible : Visibility.Collapsed;
        ReleaseButton.IsEnabled = !busy;
        if (checkingUpdate) UpdateStatus.Text = T("updateChecking");
        else if (updateError is not null) UpdateStatus.Text = T(updateError);
        else if (latestRelease?.Version is { } version && version > GitHubReleases.TagVersion(VersionLabel))
            UpdateStatus.Text = T("updateNew", latestRelease.Tag, VersionLabel);
        else if (latestRelease is not null) UpdateStatus.Text = T("updateCurrent", VersionLabel);
        else UpdateStatus.Text = T("updateConfigured", VersionLabel);
    }

    private void ShowUpdate(object sender, RoutedEventArgs e) => ShowUpdatePanel();

    private void ShowUpdatePanel()
    {
        if (latestRelease is null || busy) return;
        UpdateTitle.Text = T("viewUpdate");
        UpdateVersion.Text = T("updateVersions", VersionLabel, latestRelease.Tag);
        ReleaseNotes.Text = latestRelease.Notes;
        InstallUpdateButton.Content = T("installUpdate");
        CancelUpdateButton.Content = T("close");
        InstallUpdateButton.IsEnabled = latestRelease.CanInstall && latestRelease.Version > GitHubReleases.TagVersion(VersionLabel);
        UpdateHint.Text = T(latestRelease.CanInstall ? "updateRestartHint" : "updatePackageMissing");
        DownloadProgress.Visibility = Visibility.Collapsed;
        UpdateOverlay.Visibility = Visibility.Visible;
    }

    private void DismissUpdate(object sender, RoutedEventArgs e)
    {
        if (updateDownload is not null) updateDownload.Cancel();
        else UpdateOverlay.Visibility = Visibility.Collapsed;
    }

    private async void InstallUpdate(object sender, RoutedEventArgs e)
    {
        if (busy || latestRelease?.CanInstall != true) return;
        busy = true;
        RenderService();
        RenderUpdate();
        InstallUpdateButton.IsEnabled = false;
        CancelUpdateButton.Content = T("cancelDownload");
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(shutdown.Token);
        updateDownload = cancellation;
        try
        {
            string root = Workbench.FindRoot();
            page = await workbench.FindPageAsync(cancellation.Token);
            if (page is not null && !workbench.OwnsService)
            {
                UpdateHint.Text = T("updateOtherService");
                return;
            }
            using var lease = UpdateInstaller.AcquireLease(root);
            string stage = Path.Combine(dataDirectory, "updates", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(stage);
            DownloadProgress.Value = 0;
            DownloadProgress.Visibility = Visibility.Visible;
            var progress = new Progress<int>(value =>
            {
                DownloadProgress.Value = value;
                UpdateHint.Text = T("updateDownloading", value);
            });
            await GitHubReleases.DownloadAsync(latestRelease, Path.Combine(stage, "download.zip"), progress, cancellation.Token);
            UpdateHint.Text = T("updatePreparing");
            var plan = await Task.Run(() => UpdateInstaller.Prepare(root, stage, latestRelease.Tag), cancellation.Token);
            cancellation.Token.ThrowIfCancellationRequested();
            UpdateInstaller.StartHelper(plan);
            applyingUpdate = true;
            Close();
        }
        catch (OperationCanceledException)
        {
            UpdateHint.Text = T("downloadCancelled");
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or HttpRequestException
            or InvalidOperationException or Win32Exception)
        {
            File.WriteAllText(Path.Combine(dataDirectory, "update.log"), exception.ToString());
            UpdateHint.Text = T("installUpdateFailed");
        }
        finally
        {
            updateDownload = null;
            busy = false;
            if (!applyingUpdate)
            {
                CancelUpdateButton.Content = T("close");
                InstallUpdateButton.IsEnabled = latestRelease?.CanInstall == true;
                RenderService();
                RenderUpdate();
            }
        }
    }

    private void ReadUpdateResult()
    {
        string path = Path.Combine(dataDirectory, "update-result.json");
        try
        {
            if (!File.Exists(path)) return;
            using var result = JsonDocument.Parse(File.ReadAllText(path));
            bool success = result.RootElement.GetProperty("success").GetBoolean();
            File.Delete(path);
            MessageBox.Show(this, T(success ? "updateInstalled" : "installUpdateFailed"), Title,
                MessageBoxButton.OK, success ? MessageBoxImage.Information : MessageBoxImage.Warning);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException) { }
    }
}
