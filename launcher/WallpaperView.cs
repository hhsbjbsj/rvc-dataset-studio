using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace RvcLauncher;

internal enum WallpaperFit { Crop, Contain, Stretch }

internal sealed record WallpaperPlacement
{
    public WallpaperFit Fit { get; init; }
    public double X { get; init; } = 0.5;
    public double Y { get; init; } = 0.5;
    public bool StretchSmall { get; init; } = true;
}

internal sealed record WallpaperImage(BitmapSource Bitmap, int Width, int Height)
{
    public static WallpaperImage Load(string path)
    {
        using var stream = File.OpenRead(path);
        var frame = BitmapDecoder.Create(stream, BitmapCreateOptions.DelayCreation, BitmapCacheOption.OnDemand).Frames[0];
        int width = frame.PixelWidth;
        int height = frame.PixelHeight;

        using var pixels = File.OpenRead(path);
        var bitmap = new BitmapImage();
        bitmap.BeginInit();
        bitmap.CacheOption = BitmapCacheOption.OnLoad;
        if (width >= height && width > 2560) bitmap.DecodePixelWidth = 2560;
        else if (height > 2560) bitmap.DecodePixelHeight = 2560;
        bitmap.StreamSource = pixels;
        bitmap.EndInit();
        bitmap.Freeze();
        return new WallpaperImage(bitmap, width, height);
    }
}

public sealed class WallpaperView : FrameworkElement
{
    private WallpaperImage? image;
    private WallpaperPlacement placement = new();
    private WallpaperFit fit;

    internal void Display(WallpaperImage? source, WallpaperPlacement options, WallpaperFit effectiveFit)
    {
        image = source;
        placement = options;
        fit = effectiveFit;
        InvalidateVisual();
    }

    internal Vector CropOverflow
    {
        get
        {
            if (image is null || fit != WallpaperFit.Crop) return default;
            var size = ScaledSize(image, RenderSize, fit);
            return new Vector(Math.Max(0, size.Width - ActualWidth), Math.Max(0, size.Height - ActualHeight));
        }
    }

    protected override void OnRender(DrawingContext drawing)
    {
        base.OnRender(drawing);
        if (image is null || ActualWidth <= 0 || ActualHeight <= 0) return;

        Size size = ScaledSize(image, RenderSize, fit);
        double x = (ActualWidth - size.Width) * (fit == WallpaperFit.Crop ? placement.X : 0.5);
        double y = (ActualHeight - size.Height) * (fit == WallpaperFit.Crop ? placement.Y : 0.5);
        drawing.PushClip(new RectangleGeometry(new Rect(RenderSize)));
        drawing.DrawImage(image.Bitmap, new Rect(x, y, size.Width, size.Height));
        drawing.Pop();
    }

    private static Size ScaledSize(WallpaperImage source, Size viewport, WallpaperFit fit)
    {
        if (fit == WallpaperFit.Stretch) return viewport;
        double horizontal = viewport.Width / source.Width;
        double vertical = viewport.Height / source.Height;
        double scale = fit == WallpaperFit.Crop ? Math.Max(horizontal, vertical) : Math.Min(horizontal, vertical);
        return new Size(source.Width * scale, source.Height * scale);
    }
}
