using System.Globalization;

namespace RvcLauncher;

internal static class LauncherText
{
    public static string Get(string language, string key, params object[] arguments)
    {
        var entry = Messages[key];
        string message = language switch
        {
            "en" => entry.English,
            "ja" => entry.Japanese,
            _ => entry.Chinese,
        };
        return arguments.Length == 0 ? message : string.Format(CultureInfo.InvariantCulture, message, arguments);
    }

    private static readonly Dictionary<string, (string Chinese, string English, string Japanese)> Messages = new()
    {
        ["kicker"] = ("本地语音制作 · RVC", "LOCAL AUDIO WORKSPACE", "ローカル音声ワークスペース"),
        ["title"] = ("RVC 数据集工作台", "RVC Dataset Studio", "RVC データセットスタジオ"),
        ["description"] = ("导入、试听、切分与导出。在自己的电脑上整理角色语音。", "Import, preview, trim, and export character audio on your PC.", "キャラクター音声の読み込み、試聴、切り出し、書き出しを PC 上で行えます。"),
        ["wallpaper"] = ("更换背景", "Change wallpaper", "背景を変更"),
        ["settings"] = ("启动器设置", "Launcher settings", "ランチャー設定"),
        ["minimize"] = ("最小化", "Minimize", "最小化"),
        ["close"] = ("关闭", "Close", "閉じる"),
        ["autoCheck"] = ("启动时检查，并在启动器打开期间每 12 小时检查一次", "Check on startup and every 12 hours while the launcher is open", "起動時およびランチャーを開いている間に 12 時間ごとに確認"),
        ["language"] = ("启动器语言", "Launcher language", "ランチャーの言語"),
        ["resetWallpaper"] = ("恢复默认壁纸", "Reset wallpaper", "既定の背景に戻す"),
        ["wallpaperFit"] = ("背景适配", "Adjust wallpaper", "背景の表示を調整"),
        ["wallpaperMode"] = ("显示方式", "Display mode", "表示方法"),
        ["wallpaperPreview"] = ("背景裁剪预览", "Wallpaper crop preview", "背景の切り抜きプレビュー"),
        ["wallpaperCrop"] = ("铺满裁剪（保持比例）", "Fill and crop (keep proportions)", "画面いっぱいに表示（縦横比を保持）"),
        ["wallpaperContain"] = ("完整显示（保持比例）", "Show full image (keep proportions)", "画像全体を表示（縦横比を保持）"),
        ["wallpaperStretch"] = ("拉伸填满", "Stretch to fill", "引き伸ばして全体を埋める"),
        ["wallpaperAutoStretch"] = ("小图自动拉伸", "Stretch very small images", "小さい画像は自動で引き伸ばす"),
        ["wallpaperSmallRule"] = ("铺满裁剪时，图片的宽和高都不足显示区的一半，就拉伸整张图填满。", "In crop mode, stretch the full image when both dimensions are at most half the display area.", "切り抜き表示で、画像の幅と高さが両方とも表示領域の半分以下なら、画像全体を引き伸ばします。"),
        ["wallpaperDrag"] = ("拖动预览来选择显示区域，也可聚焦预览后用方向键调整。", "Drag the preview to choose the visible area, or focus it and use the arrow keys.", "プレビューをドラッグするか、フォーカスして矢印キーで表示範囲を調整します。"),
        ["wallpaperContainHint"] = ("显示整张图片，空余区域会露出默认背景。", "Show the full image. The default background fills the extra space.", "画像全体を表示し、余白には既定の背景が見えます。"),
        ["wallpaperStretchHint"] = ("整张图片拉伸到窗口大小，比例会随窗口调整。", "Stretch the full image to the window size. Its proportions follow the window.", "画像全体をウィンドウに合わせて引き伸ばします。縦横比はウィンドウに合わせて変わります。"),
        ["wallpaperAutoApplied"] = ("这张图较小，已自动拉伸填满。关闭小图自动拉伸可改回裁剪。", "This image is small and has been stretched. Turn off automatic stretching to crop it.", "小さい画像を引き伸ばしました。自動引き伸ばしをオフにすると切り抜き表示に戻せます。"),
        ["wallpaperCenter"] = ("居中", "Center", "中央に戻す"),
        ["wallpaperCancel"] = ("取消", "Cancel", "キャンセル"),
        ["wallpaperApply"] = ("应用", "Apply", "適用"),
        ["logs"] = ("打开日志", "Open logs", "ログを開く"),
        ["save"] = ("保存设置", "Save settings", "設定を保存"),
        ["viewUpdate"] = ("更新说明", "Release notes", "更新内容"),
        ["updateVersions"] = ("当前 v{0} · 最新 {1}", "Installed v{0} · Latest {1}", "現在 v{0} · 最新 {1}"),
        ["installUpdate"] = ("下载并重启更新", "Download and restart", "ダウンロードして再起動"),
        ["updateRestartHint"] = ("更新会保留角色项目、导出历史和壁纸设置。安装时会停止工作台并重开启动器，请先保存编辑，等待处理任务完成。", "Updates keep your character projects, export history, and wallpaper settings. Installation stops the studio and restarts the launcher. Save edits and let processing finish first.", "キャラクタープロジェクト、書き出し履歴、壁紙設定は引き継がれます。インストール時にスタジオを停止し、ランチャーを再起動します。編集を保存し、処理の完了をお待ちください。"),
        ["updatePackageMissing"] = ("此版本暂时没有可安装的 Windows 更新包。", "This release has no installable Windows package yet.", "このリリースの Windows 更新パッケージはまだありません。"),
        ["updateOtherService"] = ("请先关闭另一个启动器及它运行的工作台，然后重试更新。", "Close the other launcher and its running studio, then try again.", "別のランチャーとそのスタジオを終了してから再試行してください。"),
        ["updateDownloading"] = ("正在下载更新 · {0}%", "Downloading update · {0}%", "更新をダウンロード中 · {0}%"),
        ["updatePreparing"] = ("正在校验和准备安装…", "Verifying and preparing installation…", "検証してインストールを準備中…"),
        ["cancelDownload"] = ("取消下载", "Cancel download", "ダウンロードを中止"),
        ["downloadCancelled"] = ("下载已取消，可以稍后重试。", "Download cancelled. You can try again later.", "ダウンロードを中止しました。後で再試行できます。"),
        ["installUpdateFailed"] = ("更新未完成。请重试，或在设置里打开日志。", "Update did not complete. Try again or open the logs in Settings.", "更新を完了できませんでした。再試行するか、設定からログを開いてください。"),
        ["updateInstalled"] = ("更新已安装，可以继续使用工作台。", "Update installed. Your studio is ready to use.", "更新をインストールしました。スタジオを使用できます。"),
        ["checkUpdates"] = ("检查更新", "Check updates", "更新を確認"),
        ["launch"] = ("启动工作台", "Launch studio", "スタジオを起動"),
        ["openStudio"] = ("打开工作台", "Open studio", "スタジオを開く"),
        ["initialize"] = ("初始化环境", "Set up studio", "初期セットアップ"),
        ["idle"] = ("工作台等待启动", "Ready to launch", "起動できます"),
        ["checkingService"] = ("正在检查本机服务…", "Checking local service…", "ローカルサービスを確認中…"),
        ["ready"] = ("本机工作台已就绪 · {0}", "Local studio is running · {0}", "ローカルスタジオは起動済みです · {0}"),
        ["starting"] = ("正在启动本机服务…", "Starting local service…", "ローカルサービスを起動中…"),
        ["missingRoot"] = ("请把 EXE 放在项目根目录，再双击打开。", "Place the EXE in the project folder and open it again.", "EXE をプロジェクトフォルダーに置いて、開き直してください。"),
        ["needsSetup"] = ("首次使用请初始化环境，需要 Python、Node.js 和 FFmpeg。", "First run requires setup with Python, Node.js, and FFmpeg.", "初回セットアップには Python、Node.js、FFmpeg が必要です。"),
        ["settingUp"] = ("正在初始化环境。可在设置里打开日志。", "Setting up the studio. Logs are available in Settings.", "セットアップ中です。設定からログを確認できます。"),
        ["setupDone"] = ("初始化完成，可以启动工作台。", "Setup complete. You can launch the studio.", "セットアップが完了しました。スタジオを起動できます。"),
        ["setupFailed"] = ("初始化失败。请在设置里打开日志查看原因。", "Setup failed. Open the logs from Settings for details.", "セットアップに失敗しました。設定からログを確認してください。"),
        ["startFailed"] = ("工作台暂未响应。可以重试，或在设置里打开日志。", "The studio did not respond. Try again or open the logs from Settings.", "スタジオから応答がありません。再試行するか、設定からログを開いてください。"),
        ["browserFailed"] = ("工作台已就绪。请用浏览器打开 {0}", "The studio is running. Open {0} in your browser.", "スタジオは起動しています。ブラウザーで {0} を開いてください。"),
        ["updateConfigured"] = ("v{0} · 从官方 GitHub 仓库检查更新", "v{0} · Updates from the official GitHub repository", "v{0} · 公式 GitHub リポジトリから更新"),
        ["updateChecking"] = ("正在查询 GitHub Releases…", "Checking GitHub Releases…", "GitHub Releases を確認中…"),
        ["updateNone"] = ("这个仓库暂时没有可读取的正式版本。", "No stable release could be read for this repository.", "このリポジトリの正式リリースを取得できませんでした。"),
        ["updateNew"] = ("发现新版本 {0} · 当前 v{1}", "Update available: {0} · Installed v{1}", "新しいバージョン {0} · 現在 v{1}"),
        ["updateCurrent"] = ("当前 v{0} · 已是最新版本", "v{0} · Up to date", "v{0} · 最新バージョンです"),
        ["updateFailed"] = ("无法检查更新，请检查网络后重试。", "Update check failed. Check the connection and try again.", "更新を確認できませんでした。接続を確認して再試行してください。"),
        ["settingsFailed"] = ("无法保存启动器设置。", "Could not save launcher settings.", "ランチャー設定を保存できませんでした。"),
        ["imageFiles"] = ("图片文件", "Image files", "画像ファイル"),
        ["imageFailed"] = ("无法读取或保存这张背景图片。", "Could not read or save this wallpaper.", "背景画像を読み込みまたは保存できませんでした。"),
        ["closeRunning"] = ("关闭启动器会停止它启动的本机服务。请先保存正在编辑的内容。要继续关闭吗？", "Closing the launcher stops the local service it started. Save your work first. Close now?", "ランチャーを閉じると起動したサービスが停止します。作業を保存してから閉じてください。閉じますか？"),
        ["closeBusy"] = ("请等待当前操作完成。", "Wait for the current operation to finish.", "現在の処理が完了するまでお待ちください。"),
        ["logsFailed"] = ("无法打开日志目录。", "Could not open the log folder.", "ログフォルダーを開けませんでした。"),
    };
}
