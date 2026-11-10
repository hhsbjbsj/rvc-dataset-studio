using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace RvcLauncher;

public partial class MainWindow : Window
{
    private static readonly Version InstalledVersion = typeof(MainWindow).Assembly.GetName().Version!;
    private static readonly string VersionLabel = InstalledVersion.ToString(3);
    private static readonly TimeSpan UpdateInterval = TimeSpan.FromHours(12);
    private readonly CancellationTokenSource shutdown = new();
    private readonly DispatcherTimer serviceTimer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly DispatcherTimer updateTimer = new() { Interval = TimeSpan.FromMinutes(1) };
    private readonly string dataDirectory;
    private readonly string settingsPath;
    private readonly string wallpaperPath;
    private readonly Workbench workbench;
    private LauncherSettings settings;
    private Uri? page;
    private bool busy;
    private bool probing;
    private bool checkingUpdate;
    private string serviceMessage = "checkingService";
    private object[] serviceArguments = [];
    private string? updateError;

    public MainWindow()
    {
        InitializeComponent();
        string root = Workbench.FindRoot();
        dataDirectory = Path.Combine(root, "voiceforge-backend", "tmp", "launcher");
        settingsPath = Path.Combine(dataDirectory, "settings.json");
        wallpaperPath = Path.Combine(dataDirectory, "wallpaper");
        workbench = new Workbench(root, dataDirectory);
        settings = LauncherSettings.Load(settingsPath);
        settings.Wallpaper = NormalizePlacement(settings.Wallpaper);
        if (GitHubReleases.TryRepository(settings.Repository ?? "", out string repository)) settings.Repository = repository;
        else settings.Repository = "";
        LoadWallpaper();
        WallpaperHost.SizeChanged += (_, _) => RefreshWallpaper();
        RenderLabels();
        Loaded += async (_, _) =>
        {
            serviceTimer.Start();
            updateTimer.Start();
            await ProbeAsync();
            await CheckIfDueAsync();
        };
        serviceTimer.Tick += async (_, _) => await ProbeAsync();
        updateTimer.Tick += async (_, _) => await CheckIfDueAsync();
    }

    private string T(string key, params object[] arguments) => LauncherText.Get(settings.Language, key, arguments);

    private void RenderLabels()
    {
        HeroKicker.Text = T("kicker");
        HeroTitle.Text = T("title");
        HeroDescription.Text = T("description");
        WallpaperButton.Content = T("wallpaper");
        RenderWallpaperLabels();
        SetAccessibleLabel(RailSettings, T("settings"));
        SetAccessibleLabel(MinimizeButton, T("minimize"));
        SetAccessibleLabel(CloseButton, T("close"));
        SetAccessibleLabel(DismissSettingsButton, T("close"));
        SettingsTitle.Text = T("settings");
        RepositoryLabel.Text = T("repo");
        AutomationProperties.SetName(RepositoryInput, T("repo"));
        RepositoryHelp.Text = T("repoHelp");
        AutoUpdateInput.Content = T("autoCheck");
        LanguageLabel.Text = T("language");
        AutomationProperties.SetName(LanguageInput, T("language"));
        LogButton.Content = T("logs");
        SaveSettingsButton.Content = T("save");
        UpdateButton.Content = T("checkUpdates");
        ReleaseButton.Content = T("openRelease");
        RenderService();
        RenderUpdate();
    }

    private static void SetAccessibleLabel(Button button, string label)
    {
        button.ToolTip = label;
        AutomationProperties.SetName(button, label);
    }

    private string IdleMessage() => !workbench.HasProject ? "missingRoot" : workbench.NeedsSetup ? "needsSetup" : "idle";

    private void SetServiceMessage(string key, params object[] arguments)
    {
        serviceMessage = key;
        serviceArguments = arguments;
        RenderService();
    }

    private void RenderService()
    {
        ServiceStatus.Text = T(serviceMessage, serviceArguments);
        ServiceStatus.ToolTip = ServiceStatus.Text;
        ServiceDot.Fill = page is not null ? Brushes.LightGreen : busy ? Brushes.LightSkyBlue : Brushes.LightSlateGray;
        ServiceProgress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        LaunchButton.IsEnabled = !busy && workbench.HasProject;
        LaunchButton.Content = T(page is not null ? "openStudio" : workbench.NeedsSetup ? "initialize" : "launch");
    }

    private async Task ProbeAsync()
    {
        if (busy || probing || shutdown.IsCancellationRequested) return;
        probing = true;
        try
        {
            Uri? available = await workbench.FindPageAsync(shutdown.Token);
            if (busy || shutdown.IsCancellationRequested) return;
            bool changed = page != available;
            page = available;
            if (changed || serviceMessage == "checkingService")
            {
                if (page is not null) SetServiceMessage("ready", page.Authority);
                else SetServiceMessage(IdleMessage());
            }
        }
        finally
        {
            probing = false;
        }
    }

    private async void Launch(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        busy = true;
        SetServiceMessage("checkingService");
        bool installing = false;
        try
        {
            page = await workbench.FindPageAsync(shutdown.Token);
            if (page is null)
            {
                if (!workbench.HasProject)
                {
                    SetServiceMessage("missingRoot");
                    return;
                }
                installing = workbench.NeedsSetup;
                if (installing)
                {
                    SetServiceMessage("settingUp");
                    bool setup = await workbench.SetupAsync(shutdown.Token);
                    SetServiceMessage(setup ? "setupDone" : "setupFailed");
                    return;
                }
                SetServiceMessage("starting");
                page = await workbench.StartAsync(shutdown.Token);
            }
            if (page is null)
            {
                SetServiceMessage("startFailed");
                return;
            }
            SetServiceMessage("ready", page.Authority);
            try { Open(page.AbsoluteUri); }
            catch (Win32Exception) { SetServiceMessage("browserFailed", page.AbsoluteUri); }
        }
        catch (Exception exception) when (exception is Win32Exception or IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            SetServiceMessage(installing ? "setupFailed" : "startFailed");
        }
        finally
        {
            busy = false;
            RenderService();
        }
    }

    private async Task CheckIfDueAsync()
    {
        if (settings.AutoCheck && settings.Repository.Length != 0 && DateTimeOffset.UtcNow - settings.LastCheck >= UpdateInterval)
            await CheckAsync();
    }

    private async void CheckUpdates(object sender, RoutedEventArgs e)
    {
        if (settings.Repository.Length == 0)
        {
            ShowSettings();
            return;
        }
        await CheckAsync();
    }

    private async Task CheckAsync()
    {
        if (checkingUpdate || shutdown.IsCancellationRequested) return;
        string repository = settings.Repository;
        checkingUpdate = true;
        updateError = null;
        RenderUpdate();
        try
        {
            string? tag = await GitHubReleases.LatestTagAsync(repository, shutdown.Token);
            if (repository == settings.Repository) settings.LatestTag = tag;
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or JsonException
            or KeyNotFoundException or InvalidOperationException)
        {
            if (repository == settings.Repository) updateError = "updateFailed";
        }
        finally
        {
            checkingUpdate = false;
            if (!shutdown.IsCancellationRequested)
            {
                if (repository == settings.Repository)
                {
                    settings.LastCheck = DateTimeOffset.UtcNow;
                    if (!TrySave(settings)) updateError ??= "settingsFailed";
                }
                RenderUpdate();
            }
        }
    }

    private void RenderUpdate()
    {
        UpdateButton.IsEnabled = !checkingUpdate;
        ReleaseButton.Visibility = Visibility.Collapsed;
        if (checkingUpdate) UpdateStatus.Text = T("updateChecking");
        else if (updateError is not null) UpdateStatus.Text = T(updateError);
        else if (settings.Repository.Length == 0) UpdateStatus.Text = T("updateSource", VersionLabel);
        else if (settings.LastCheck == default) UpdateStatus.Text = T("updateConfigured", VersionLabel);
        else if (settings.LatestTag is not { } tag) UpdateStatus.Text = T("updateNone");
        else
        {
            Version? latest = GitHubReleases.TagVersion(tag);
            if (latest is null || latest > GitHubReleases.TagVersion(VersionLabel))
            {
                ReleaseButton.Visibility = Visibility.Visible;
                UpdateStatus.Text = latest is null ? T("updateUnrecognized", tag) : T("updateNew", tag, VersionLabel);
            }
            else UpdateStatus.Text = T("updateCurrent", VersionLabel);
        }
    }

    private void OpenRelease(object sender, RoutedEventArgs e)
    {
        if (settings.LatestTag is not { } tag) return;
        try { Open(GitHubReleases.ReleaseUrl(settings.Repository, tag).AbsoluteUri); }
        catch (Win32Exception) { ShowMessage("releaseFailed"); }
    }

    private void OpenSettings(object sender, RoutedEventArgs e) => ShowSettings();

    private void ShowSettings()
    {
        RepositoryInput.Text = settings.Repository;
        AutoUpdateInput.IsChecked = settings.AutoCheck;
        LanguageInput.SelectedItem = LanguageInput.Items.OfType<ComboBoxItem>()
            .FirstOrDefault(item => Equals(item.Tag, settings.Language)) ?? LanguageInput.Items[0];
        SettingsError.Visibility = Visibility.Collapsed;
        SettingsOverlay.Visibility = Visibility.Visible;
        RepositoryInput.Focus();
    }

    private async void SaveSettings(object sender, RoutedEventArgs e)
    {
        if (!GitHubReleases.TryRepository(RepositoryInput.Text, out string repository))
        {
            SettingsError.Text = T("invalidRepository");
            SettingsError.Visibility = Visibility.Visible;
            RepositoryInput.Focus();
            return;
        }
        bool sameRepository = string.Equals(repository, settings.Repository, StringComparison.OrdinalIgnoreCase);
        var edited = new LauncherSettings
        {
            Repository = repository,
            AutoCheck = AutoUpdateInput.IsChecked == true,
            Language = (LanguageInput.SelectedItem as ComboBoxItem)?.Tag as string ?? "zh",
            LastCheck = sameRepository ? settings.LastCheck : default,
            LatestTag = sameRepository ? settings.LatestTag : null,
            Wallpaper = settings.Wallpaper,
        };
        if (!TrySave(edited))
        {
            SettingsError.Text = T("settingsFailed");
            SettingsError.Visibility = Visibility.Visible;
            return;
        }
        settings = edited;
        updateError = null;
        SettingsOverlay.Visibility = Visibility.Collapsed;
        RenderLabels();
        await CheckIfDueAsync();
    }

    private bool TrySave(LauncherSettings value)
    {
        try
        {
            value.Save(settingsPath);
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    private void DismissSettings(object sender, RoutedEventArgs e) => SettingsOverlay.Visibility = Visibility.Collapsed;

    private void OpenLogs(object sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(dataDirectory);
            Open(dataDirectory);
        }
        catch (Exception exception) when (exception is Win32Exception or IOException or UnauthorizedAccessException)
        {
            ShowMessage("logsFailed");
        }
    }

    private static void Open(string target) => Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
    private void ShowMessage(string key) => MessageBox.Show(this, T(key), Title, MessageBoxButton.OK, MessageBoxImage.Information);
    private void Minimize(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
    private void CloseWindow(object sender, RoutedEventArgs e) => Close();

    protected override void OnClosing(CancelEventArgs e)
    {
        if (busy)
        {
            e.Cancel = true;
            ShowMessage("closeBusy");
        }
        else if (workbench.OwnsService)
        {
            e.Cancel = MessageBox.Show(this, T("closeRunning"), Title, MessageBoxButton.YesNo,
                MessageBoxImage.Question, MessageBoxResult.No) != MessageBoxResult.Yes;
        }
        base.OnClosing(e);
    }

    protected override void OnClosed(EventArgs e)
    {
        serviceTimer.Stop();
        updateTimer.Stop();
        shutdown.Cancel();
        workbench.StopOwned();
        shutdown.Dispose();
        base.OnClosed(e);
    }
}
