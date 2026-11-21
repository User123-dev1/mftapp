using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using Whmi.Panel.ViewModels;

namespace Whmi.Panel.Views;

public partial class SettingsView : UserControl
{
    public SettingsView() => InitializeComponent();

    private async void OnImportKey(object? sender, RoutedEventArgs e)
    {
        if (DataContext is not SettingsViewModel vm || TopLevel.GetTopLevel(this) is not { } top) return;
        var files = await top.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = "Choose this panel's key file",
            FileTypeFilter = [new FilePickerFileType("Key file") { Patterns = ["*.key"] }, FilePickerFileTypes.All],
        });
        if (files.Count == 0 || files[0].TryGetLocalPath() is not { } path) return;
        try
        {
            vm.KeyHex = (await File.ReadAllTextAsync(path)).Trim();
            vm.ClientId = Path.GetFileNameWithoutExtension(path); // panel1.key -> panel1
        }
        catch (IOException ex)
        {
            vm.Error = ex.Message;
        }
    }
}
