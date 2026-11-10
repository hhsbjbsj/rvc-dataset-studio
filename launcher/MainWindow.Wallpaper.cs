using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Input;
using System.Windows.Media;
using Microsoft.Win32;

namespace RvcLauncher;

public partial class MainWindow
{
    private WallpaperImage? wallpaperImage;
    private WallpaperPlacement wallpaperDraft = new();
    private Point? wallpaperDragStart;
    private Point wallpaperDragAnchor;

    private static WallpaperPlacement NormalizePlacement(WallpaperPlacement? value)
    {
        value ??= new();
        return value with
        {
            Fit = Enum.IsDefined(value.Fit) ? value.Fit : WallpaperFit.Crop,
            X = double.IsFinite(value.X) ? Math.Clamp(value.X, 0, 1) : 0.5,
            Y = double.IsFinite(value.Y) ? Math.Clamp(value.Y, 0, 1) : 0.5,
        };
    }

    private void RenderWallpaperLabels()
    {
        FitWallpaperButton.Content = T("wallpaperFit");
        ResetWallpaperButton.Content = ResetWallpaperQuickButton.Content = T("resetWallpaper");
        WallpaperEditorTitle.Text = T("wallpaperFit");
        WallpaperFitLabel.Text = T("wallpaperMode");
        AutomationProperties.SetName(WallpaperFitInput, T("wallpaperMode"));
        AutomationProperties.SetName(WallpaperPreviewHost, T("wallpaperPreview"));
        WallpaperCropItem.Content = T("wallpaperCrop");
        WallpaperContainItem.Content = T("wallpaperContain");
        WallpaperStretchItem.Content = T("wallpaperStretch");
        WallpaperAutoStretch.Content = T("wallpaperAutoStretch");
        WallpaperAutoStretch.ToolTip = T("wallpaperSmallRule");
        CenterWallpaperButton.Content = T("wallpaperCenter");
        CancelWallpaperButton.Content = T("wallpaperCancel");
        SaveWallpaperButton.Content = T("wallpaperApply");
        RefreshWallpaper();
    }

    private void ChooseWallpaper(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog
        {
            Title = T("wallpaper"),
            Filter = T("imageFiles") + "|*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp",
            CheckFileExists = true,
        };
        if (dialog.ShowDialog(this) != true) return;
        try
        {
            var image = WallpaperImage.Load(dialog.FileName);
            Directory.CreateDirectory(dataDirectory);
            string temporary = wallpaperPath + ".tmp";
            File.Copy(dialog.FileName, temporary, overwrite: true);
            File.Move(temporary, wallpaperPath, overwrite: true);
            wallpaperImage = image;
            settings.Wallpaper = settings.Wallpaper with { X = 0.5, Y = 0.5 };
            if (!TrySave(settings)) ShowMessage("settingsFailed");
            RefreshWallpaper();
            ShowWallpaperEditor();
        }
        catch (Exception exception) when (IsWallpaperError(exception))
        {
            ShowMessage("imageFailed");
        }
    }

    private void LoadWallpaper()
    {
        if (!File.Exists(wallpaperPath)) return;
        try { wallpaperImage = WallpaperImage.Load(wallpaperPath); }
        catch (Exception exception) when (IsWallpaperError(exception))
        {
            // Keep the built-in background when the saved copy is unreadable.
        }
    }

    private static bool IsWallpaperError(Exception exception) =>
        exception is IOException or UnauthorizedAccessException or NotSupportedException or FileFormatException or ArgumentException;

    private void ResetWallpaper(object sender, RoutedEventArgs e)
    {
        try
        {
            File.Delete(wallpaperPath);
            wallpaperImage = null;
            RefreshWallpaper();
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            ShowMessage("imageFailed");
        }
    }

    private WallpaperFit EffectiveFit(WallpaperPlacement placement)
    {
        if (wallpaperImage is null || !placement.StretchSmall || placement.Fit != WallpaperFit.Crop)
            return placement.Fit;

        var dpi = VisualTreeHelper.GetDpi(WallpaperHost);
        bool small = wallpaperImage.Width * 2.0 <= WallpaperHost.ActualWidth * dpi.DpiScaleX &&
            wallpaperImage.Height * 2.0 <= WallpaperHost.ActualHeight * dpi.DpiScaleY;
        return small ? WallpaperFit.Stretch : WallpaperFit.Crop;
    }

    private void RefreshWallpaper()
    {
        Wallpaper.Display(wallpaperImage, settings.Wallpaper, EffectiveFit(settings.Wallpaper));
        FitWallpaperButton.IsEnabled = ResetWallpaperQuickButton.IsEnabled =
            ResetWallpaperButton.IsEnabled = wallpaperImage is not null;
        if (WallpaperOverlay.Visibility == Visibility.Visible) RefreshWallpaperPreview();
    }

    private void OpenWallpaperEditor(object sender, RoutedEventArgs e) => ShowWallpaperEditor();

    private void ShowWallpaperEditor()
    {
        if (wallpaperImage is null) return;
        wallpaperDraft = settings.Wallpaper;
        WallpaperFitInput.SelectedIndex = (int)wallpaperDraft.Fit;
        WallpaperAutoStretch.IsChecked = wallpaperDraft.StretchSmall;
        WallpaperOverlay.Visibility = Visibility.Visible;
        RefreshWallpaperPreview();
        WallpaperFitInput.Focus();
    }

    private void WallpaperOptionsChanged(object sender, RoutedEventArgs e)
    {
        if (WallpaperOverlay is null || WallpaperOverlay.Visibility != Visibility.Visible || WallpaperFitInput.SelectedIndex < 0)
            return;
        wallpaperDraft = wallpaperDraft with
        {
            Fit = (WallpaperFit)WallpaperFitInput.SelectedIndex,
            StretchSmall = WallpaperAutoStretch.IsChecked == true,
        };
        RefreshWallpaperPreview();
    }

    private void RefreshWallpaperPreview()
    {
        double width = WallpaperHost.ActualWidth;
        double height = WallpaperHost.ActualHeight;
        if (width > 0 && height > 0)
        {
            double scale = Math.Min(464 / width, 225 / height);
            WallpaperPreviewHost.Width = width * scale + 2;
            WallpaperPreviewHost.Height = height * scale + 2;
        }
        var fit = EffectiveFit(wallpaperDraft);
        WallpaperPreview.Display(wallpaperImage, wallpaperDraft, fit);
        WallpaperAutoStretch.IsEnabled = wallpaperDraft.Fit == WallpaperFit.Crop;
        WallpaperPreviewHost.Cursor = fit == WallpaperFit.Crop ? Cursors.SizeAll : Cursors.Arrow;
        CenterWallpaperButton.IsEnabled = fit == WallpaperFit.Crop;
        WallpaperHint.Text = T(fit switch
        {
            WallpaperFit.Crop => "wallpaperDrag",
            WallpaperFit.Contain => "wallpaperContainHint",
            WallpaperFit.Stretch when wallpaperDraft.Fit == WallpaperFit.Crop => "wallpaperAutoApplied",
            _ => "wallpaperStretchHint",
        });
    }

    private void BeginWallpaperDrag(object sender, MouseButtonEventArgs e)
    {
        if (WallpaperPreview.CropOverflow.LengthSquared < 1 || !WallpaperPreviewHost.CaptureMouse()) return;
        WallpaperPreviewHost.Focus();
        wallpaperDragStart = e.GetPosition(WallpaperPreviewHost);
        wallpaperDragAnchor = new Point(wallpaperDraft.X, wallpaperDraft.Y);
        e.Handled = true;
    }

    private void DragWallpaper(object sender, MouseEventArgs e)
    {
        if (wallpaperDragStart is Point start)
            PanWallpaper(wallpaperDragAnchor, e.GetPosition(WallpaperPreviewHost) - start);
    }

    private void EndWallpaperDrag(object sender, MouseEventArgs e) => ReleaseWallpaperDrag();

    private void ReleaseWallpaperDrag()
    {
        wallpaperDragStart = null;
        if (WallpaperPreviewHost.IsMouseCaptured) WallpaperPreviewHost.ReleaseMouseCapture();
    }

    private void PanWallpaperWithKeys(object sender, KeyEventArgs e)
    {
        var delta = e.Key switch
        {
            Key.Left => new Vector(-12, 0),
            Key.Right => new Vector(12, 0),
            Key.Up => new Vector(0, -12),
            Key.Down => new Vector(0, 12),
            _ => default,
        };
        if (delta.LengthSquared == 0 || WallpaperPreview.CropOverflow.LengthSquared < 1) return;
        PanWallpaper(new Point(wallpaperDraft.X, wallpaperDraft.Y), delta);
        e.Handled = true;
    }

    private void PanWallpaper(Point anchor, Vector delta)
    {
        Vector overflow = WallpaperPreview.CropOverflow;
        wallpaperDraft = wallpaperDraft with
        {
            X = overflow.X > 0.1 ? Math.Clamp(anchor.X - delta.X / overflow.X, 0, 1) : anchor.X,
            Y = overflow.Y > 0.1 ? Math.Clamp(anchor.Y - delta.Y / overflow.Y, 0, 1) : anchor.Y,
        };
        RefreshWallpaperPreview();
    }

    private void CenterWallpaper(object sender, RoutedEventArgs e)
    {
        wallpaperDraft = wallpaperDraft with { X = 0.5, Y = 0.5 };
        RefreshWallpaperPreview();
    }

    private void CloseWallpaperEditor(object sender, RoutedEventArgs e) => HideWallpaperEditor();

    private void HideWallpaperEditor()
    {
        ReleaseWallpaperDrag();
        WallpaperOverlay.Visibility = Visibility.Collapsed;
        FitWallpaperButton.Focus();
    }

    private void SaveWallpaper(object sender, RoutedEventArgs e)
    {
        var previous = settings.Wallpaper;
        settings.Wallpaper = wallpaperDraft;
        if (!TrySave(settings))
        {
            settings.Wallpaper = previous;
            ShowMessage("settingsFailed");
            return;
        }
        HideWallpaperEditor();
        RefreshWallpaper();
    }
}
