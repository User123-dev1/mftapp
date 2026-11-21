using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.VisualTree;
using Whmi.Studio.ViewModels;

namespace Whmi.Studio.Views;

/// <summary>Screen designer: pointer handling for selecting, moving and resizing widgets.</summary>
public partial class ScreensView : UserControl
{
    private WidgetItem? _dragItem;
    private bool _resizing;
    private Point _start;
    private Rect _origin;

    public ScreensView()
    {
        InitializeComponent();
        DesignCanvas.AddHandler(PointerPressedEvent, OnPressed, RoutingStrategies.Bubble, handledEventsToo: true);
        DesignCanvas.AddHandler(PointerMovedEvent, OnMoved, RoutingStrategies.Bubble, handledEventsToo: true);
        DesignCanvas.AddHandler(PointerReleasedEvent, OnReleased, RoutingStrategies.Bubble, handledEventsToo: true);
        KeyDown += OnKeyDown;
    }

    private ScreensViewModel? Vm => DataContext as ScreensViewModel;

    private static WidgetItem? ItemAt(object? source, out bool onResizeHandle)
    {
        onResizeHandle = false;
        for (var v = source as Visual; v is not null; v = v.GetVisualParent())
        {
            if (v is StyledElement { Classes: var classes } && classes.Contains("resize")) onResizeHandle = true;
            if (v is Control { DataContext: WidgetItem item }) return item;
            if (v is ItemsControl) break;
        }
        return null;
    }

    private void OnPressed(object? sender, PointerPressedEventArgs e)
    {
        if (Vm is not { } vm || !e.GetCurrentPoint(DesignCanvas).Properties.IsLeftButtonPressed) return;
        Focus();
        var item = ItemAt(e.Source, out var onHandle);
        vm.SelectedWidget = item;
        if (item is null) return;
        _dragItem = item;
        _resizing = onHandle && item.IsSelected;
        _start = e.GetPosition(DesignCanvas);
        _origin = new Rect(item.X, item.Y, item.W, item.H);
        e.Pointer.Capture(DesignCanvas);
        e.Handled = true;
    }

    private void OnMoved(object? sender, PointerEventArgs e)
    {
        if (_dragItem is null || Vm is not { } vm) return;
        var delta = e.GetPosition(DesignCanvas) - _start;
        if (_resizing) vm.ResizeTo(_dragItem, _origin.Width + delta.X, _origin.Height + delta.Y);
        else vm.MoveTo(_dragItem, _origin.X + delta.X, _origin.Y + delta.Y);
    }

    private void OnReleased(object? sender, PointerReleasedEventArgs e)
    {
        _dragItem = null;
        e.Pointer.Capture(null);
    }

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
        if (Vm is not { SelectedWidget: { } w } vm || e.Source is TextBox or NumericUpDown or AutoCompleteBox) return;
        double step = e.KeyModifiers.HasFlag(KeyModifiers.Shift) ? 1 : ScreensViewModel.Grid;
        switch (e.Key)
        {
            case Key.Delete: vm.DeleteWidgetCommand.Execute(null); break;
            case Key.Left: w.X -= step; break;
            case Key.Right: w.X += step; break;
            case Key.Up: w.Y -= step; break;
            case Key.Down: w.Y += step; break;
            default: return;
        }
        e.Handled = true;
    }
}
