using Avalonia.Controls;
using Avalonia.Input;
using Whmi.Studio.ViewModels;

namespace Whmi.Studio.Views;

public partial class MainWindow : Window
{
    private bool _closeConfirmed;

    public MainWindow() => InitializeComponent();

    private void OnIssueDoubleTapped(object? sender, TappedEventArgs e)
    {
        if (DataContext is MainWindowViewModel vm && IssueList.SelectedItem is IssueItem item)
            vm.Navigate(item);
    }

    protected override async void OnClosing(WindowClosingEventArgs e)
    {
        base.OnClosing(e);
        if (_closeConfirmed || DataContext is not MainWindowViewModel { IsDirty: true } vm) return;
        e.Cancel = true;
        if (await new Services.AvaloniaDialogs(this).ConfirmAsync("Unsaved changes",
                $"Close WHMI Studio and discard unsaved changes to '{vm.Project.Name}'?"))
        {
            _closeConfirmed = true;
            Close();
        }
    }
}
