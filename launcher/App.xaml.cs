using System.Windows;

namespace RvcLauncher;

public partial class App : Application
{
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        if (e.Args is ["--apply-update", var plan])
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            try { await Task.Run(() => UpdateInstaller.Run(plan)); }
            catch (Exception exception)
            {
                MessageBox.Show("Update could not finish. " + exception.Message, "RVC Dataset Studio");
            }
            Shutdown();
            return;
        }
        MainWindow = new MainWindow();
        MainWindow.Show();
    }
}
