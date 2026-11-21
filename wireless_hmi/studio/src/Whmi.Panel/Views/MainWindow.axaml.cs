using Avalonia.Controls;
using Avalonia.Threading;
using Whmi.Panel.ViewModels;

namespace Whmi.Panel.Views;

public partial class MainWindow : Window
{
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(500) };
    private bool _exitRequested;

    public MainWindow()
    {
        InitializeComponent();
        _timer.Tick += (_, _) => Vm?.Tick();
        _timer.Start();
        // Losing focus (OS dialog, task switch, sleep) must never leave a button held.
        Deactivated += (_, _) => Vm?.OnDeactivated();
        DataContextChanged += (_, _) =>
        {
            if (Vm is not { } vm) return;
            vm.ExitRequested += () =>
            {
                _exitRequested = true;
                Close();
            };
            vm.PropertyChanged += (_, e) =>
            {
                if (e.PropertyName == nameof(PanelViewModel.Settings)) ApplyWindowMode();
            };
            ApplyWindowMode();
        };
    }

    private PanelViewModel? Vm => DataContext as PanelViewModel;

    private void ApplyWindowMode()
    {
        if (Vm is not { } vm) return;
        WindowState = vm.Settings.Fullscreen || vm.Settings.Kiosk ? WindowState.FullScreen : WindowState.Normal;
        Topmost = vm.Settings.Kiosk;
    }

    protected override async void OnClosing(WindowClosingEventArgs e)
    {
        base.OnClosing(e);
        // In kiosk mode the panel can only be closed from the PIN protected settings page.
        if (Vm is { } vm && vm.Settings.Kiosk && !_exitRequested)
        {
            e.Cancel = true;
            return;
        }
        if (Vm is { } v && !e.Cancel)
        {
            _timer.Stop();
            await v.DisposeAsync();
        }
    }
}
