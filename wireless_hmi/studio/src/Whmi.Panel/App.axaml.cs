using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Whmi.Panel.Platform;
using Whmi.Panel.ViewModels;
using Whmi.Panel.Views;

namespace Whmi.Panel;

public partial class App : Application
{
    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var vm = new PanelViewModel(PanelSettings.DefaultDirectory, DeviceStatus.Create());
            desktop.MainWindow = new MainWindow { DataContext = vm };
            vm.Start();
        }
        base.OnFrameworkInitializationCompleted();
    }
}
