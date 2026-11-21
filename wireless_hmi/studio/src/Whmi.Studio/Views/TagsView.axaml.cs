using System.ComponentModel;
using Avalonia.Controls;
using Whmi.Studio.ViewModels;

namespace Whmi.Studio.Views;

public partial class TagsView : UserControl
{
    private TagsViewModel? _vm;

    public TagsView()
    {
        InitializeComponent();
        DataContextChanged += (_, _) =>
        {
            if (_vm is not null) _vm.PropertyChanged -= OnVmChanged;
            _vm = DataContext as TagsViewModel;
            if (_vm is not null) _vm.PropertyChanged += OnVmChanged;
            UpdateLiveColumns();
        };
    }

    private void OnVmChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(TagsViewModel.ShowLive)) UpdateLiveColumns();
        if (e.PropertyName == nameof(TagsViewModel.SelectedRow) && _vm?.SelectedRow is { } row) Grid.ScrollIntoView(row, null);
    }

    // DataGrid columns are not part of the visual tree, so their visibility is driven from here.
    private void UpdateLiveColumns()
    {
        bool live = _vm?.ShowLive ?? false;
        foreach (var column in Grid.Columns.Where(c => c.Tag as string == "live"))
            column.IsVisible = live;
    }
}
