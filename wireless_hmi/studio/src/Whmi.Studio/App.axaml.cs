using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Whmi.Studio.Services;
using Whmi.Studio.ViewModels;
using Whmi.Studio.Views;

namespace Whmi.Studio;

public partial class App : Application
{
    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var window = new MainWindow();
            var vm = new MainWindowViewModel(new AvaloniaDialogs(window));
            window.DataContext = vm;
            if (desktop.Args is [var path, ..] && File.Exists(path))
                vm.OpenPath(path);
            desktop.MainWindow = window;
        }
        base.OnFrameworkInitializationCompleted();
    }
}
