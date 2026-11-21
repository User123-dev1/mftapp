using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.Globalization;
using System.Text.Json.Nodes;
using Avalonia.Media;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Whmi.Core.Model;

namespace Whmi.Studio.ViewModels;

/// <summary>Designer wrapper around a <see cref="Widget"/>: geometry, properties and a preview look.</summary>
public sealed partial class WidgetItem : ObservableObject
{
    public WidgetItem(Widget widget)
    {
        Widget = widget;
        widget.PropertyChanged += (_, _) => RaiseAll();
    }

    public Widget Widget { get; }

    [ObservableProperty] private bool _isSelected;

    public string Type => Widget.Type ?? "?";
    public string? Id { get => Widget.Id; set => Widget.Id = value; }
    public double X { get => Widget.X; set => Widget.X = Math.Max(0, Math.Round(value)); }
    public double Y { get => Widget.Y; set => Widget.Y = Math.Max(0, Math.Round(value)); }
    public double W { get => Widget.W; set => Widget.W = Math.Max(10, Math.Round(value)); }
    public double H { get => Widget.H; set => Widget.H = Math.Max(10, Math.Round(value)); }
    public string? Tag { get => Widget.Tag; set => Widget.Tag = value; }
    public string? Text { get => Widget.Text; set => Widget.Text = value; }
    public string? Action { get => Widget.Action; set => Widget.Action = value; }
    public string? TargetScreen { get => Widget.TargetScreen; set => Widget.TargetScreen = value; }
    public string? Format { get => Widget.Format; set => Widget.Format = value; }
    public string? Color { get => Widget.Color; set => Widget.Color = value; }
    public string? OnText { get => Widget.OnText; set => Widget.OnText = value; }
    public string? OffText { get => Widget.OffText; set => Widget.OffText = value; }
    public string? OnColor { get => Widget.OnColor; set => Widget.OnColor = value; }
    public string? OffColor { get => Widget.OffColor; set => Widget.OffColor = value; }

    public string MinText
    {
        get => Widget.Min?.ToString("R", CultureInfo.InvariantCulture) ?? "";
        set => Widget.Min = ParseNum(value);
    }

    public string MaxText
    {
        get => Widget.Max?.ToString("R", CultureInfo.InvariantCulture) ?? "";
        set => Widget.Max = ParseNum(value);
    }

    // which property groups apply to this widget type
    public bool HasTag => Core.Model.Widget.Traits(Type).NeedsTag;
    public bool IsButton => Type == "button";
    public bool IsIndicator => Type == "indicator";
    public bool IsGoto => Type == "goto";
    public bool HasRange => Type is "bar" or "gauge" or "trend";
    public bool HasFormat => Type is "numeric_display" or "numeric_entry" or "text_display";
    public bool HasColor => Type is "button" or "rectangle" or "label" or "toggle" or "goto";
    public bool IsBar => Type is "bar" or "gauge";

    // preview look on the design canvas
    public string Caption => Type switch
    {
        "label" => Text ?? "Label",
        "rectangle" => "",
        "indicator" => OffText ?? OnText ?? Tag ?? "indicator",
        "numeric_display" => Tag is null ? "###.#" : $"{Tag}\n###.#",
        "text_display" => Tag ?? "text",
        "numeric_entry" or "text_entry" => $"[ {Tag ?? "entry"} ]",
        "button" => Text ?? Tag ?? "BUTTON",
        "toggle" => Text ?? Tag ?? "TOGGLE",
        "goto" => $"→ {Text ?? TargetScreen ?? "screen"}",
        "trend" => $"trend: {Tag}",
        _ => $"{Tag}",
    };

    public IBrush Fill => Brush(Type switch
    {
        "label" => Color ?? "Transparent",
        "rectangle" => Color ?? "#333333",
        "indicator" => OffColor ?? "#555555",
        "numeric_display" or "text_display" or "trend" => "#0E0E0E",
        "numeric_entry" or "text_entry" => "#ECECEC",
        "button" => Color ?? "#3A6EA5",
        "toggle" => Color ?? "#5A5A5A",
        "goto" => Color ?? "#444444",
        _ => "#262626",
    });

    public IBrush CaptionBrush => Brush(Type is "numeric_entry" or "text_entry" ? "#111111" : "#F2F2F2");

    public IBrush OutlineBrush => IsSelected ? Brushes.DeepSkyBlue : Brush("#80FFFFFF");

    public double BarFillHeight => Math.Max(0, H * 0.6 - 2);

    partial void OnIsSelectedChanged(bool value) => OnPropertyChanged(nameof(OutlineBrush));

    private static IBrush Brush(string color) =>
        Avalonia.Media.Color.TryParse(color, out var c) ? new SolidColorBrush(c) : Brushes.Magenta;

    private static double? ParseNum(string? s) =>
        string.IsNullOrWhiteSpace(s) ? null
        : double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d
        : throw new FormatException($"'{s}' is not a number");

    private void RaiseAll() => OnPropertyChanged(string.Empty);
}

/// <summary>Designer wrapper around a <see cref="Screen"/>.</summary>
public sealed partial class ScreenItem : ObservableObject
{
    private readonly Dictionary<Widget, WidgetItem> _items = [];

    public ScreenItem(Screen screen)
    {
        Screen = screen;
        screen.PropertyChanged += (_, _) => OnPropertyChanged(string.Empty);
        screen.Widgets.CollectionChanged += (_, _) => Sync();
        Sync();
    }

    public Screen Screen { get; }
    public ObservableCollection<WidgetItem> Widgets { get; } = [];

    public string Name { get => Screen.Name; set => Screen.Name = value.Trim(); }
    public int Width { get => Screen.Width; set => Screen.Width = Math.Clamp(value, 100, 4096); }
    public int Height { get => Screen.Height; set => Screen.Height = Math.Clamp(value, 100, 4096); }
    public string Background { get => Screen.Background; set => Screen.Background = value; }

    public IBrush BackgroundBrush =>
        Color.TryParse(Screen.Background, out var c) ? new SolidColorBrush(c) : Brushes.Black;

    public WidgetItem ItemFor(Widget w)
    {
        if (!_items.TryGetValue(w, out var item)) _items[w] = item = new WidgetItem(w);
        return item;
    }

    private void Sync()
    {
        var live = Screen.Widgets.ToHashSet();
        foreach (var gone in _items.Keys.Where(k => !live.Contains(k)).ToList()) _items.Remove(gone);
        Widgets.Clear();
        foreach (var w in Screen.Widgets) Widgets.Add(ItemFor(w));
    }

    public override string ToString() => Name;
}

public sealed partial class ScreensViewModel : SectionViewModel
{
    public const double Grid = 5;

    private readonly Dictionary<Screen, ScreenItem> _items = [];

    public ScreensViewModel(Project project) : base("Screens", "🖥")
    {
        Project = project;
        project.Screens.CollectionChanged += OnScreensChanged;
        SyncScreens();
        SelectedScreen = Screens.FirstOrDefault();
        RefreshLookups();
    }

    public Project Project { get; }
    public ObservableCollection<ScreenItem> Screens { get; } = [];
    public ObservableCollection<string> TagNames { get; } = [];
    public ObservableCollection<string> ScreenNames { get; } = [];

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(DeleteScreenCommand), nameof(AddWidgetCommand), nameof(SetStartScreenCommand))]
    [NotifyPropertyChangedFor(nameof(IsStartScreen))]
    private ScreenItem? _selectedScreen;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(DeleteWidgetCommand), nameof(DuplicateWidgetCommand))]
    [NotifyPropertyChangedFor(nameof(HasWidget))]
    private WidgetItem? _selectedWidget;

    public bool HasWidget => SelectedWidget is not null;
    public bool IsStartScreen => SelectedScreen is not null && Project.StartScreen == SelectedScreen.Name;

    partial void OnSelectedScreenChanged(ScreenItem? value) => SelectedWidget = null;

    partial void OnSelectedWidgetChanged(WidgetItem? oldValue, WidgetItem? newValue)
    {
        if (oldValue is not null) oldValue.IsSelected = false;
        if (newValue is not null) newValue.IsSelected = true;
    }

    private void OnScreensChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        SyncScreens();
        RefreshLookups();
    }

    private void SyncScreens()
    {
        var live = Project.Screens.ToHashSet();
        foreach (var gone in _items.Keys.Where(k => !live.Contains(k)).ToList()) _items.Remove(gone);
        var selected = SelectedScreen;
        Screens.Clear();
        foreach (var s in Project.Screens)
        {
            if (!_items.TryGetValue(s, out var item)) _items[s] = item = new ScreenItem(s);
            Screens.Add(item);
        }
        SelectedScreen = selected is not null && Screens.Contains(selected) ? selected : Screens.FirstOrDefault();
    }

    public void RefreshLookups()
    {
        Replace(TagNames, Project.Tags.Select(t => t.Name).Concat(Project.SystemTags().Select(t => t.Name)).ToList());
        Replace(ScreenNames, Project.Screens.Select(s => s.Name).ToList());
        OnPropertyChanged(nameof(IsStartScreen));
    }

    private static void Replace(ObservableCollection<string> target, List<string> items)
    {
        if (target.SequenceEqual(items)) return;
        target.Clear();
        foreach (var i in items) target.Add(i);
    }

    /// <summary>Select the screen/widget an issue path points at ("screens.Main.start").</summary>
    public void SelectByPath(string path)
    {
        if (path == "start_screen") return;
        var rest = path["screens.".Length..];
        var screen = Screens.OrderByDescending(s => s.Name.Length).FirstOrDefault(s => rest.StartsWith(s.Name));
        if (screen is null) return;
        SelectedScreen = screen;
        var wid = rest.Length > screen.Name.Length + 1 ? rest[(screen.Name.Length + 1)..] : null;
        SelectedWidget = screen.Widgets.FirstOrDefault(w => w.Id == wid)
                         ?? (wid is ['#', ..] && int.TryParse(wid[1..], out var i) && i < screen.Widgets.Count ? screen.Widgets[i] : null);
    }

    // ------------------------------------------------------------------ screens

    [RelayCommand]
    private void AddScreen()
    {
        int n = Project.Screens.Count + 1;
        while (Project.Screens.Any(s => s.Name == $"Screen{n}")) n++;
        var first = Project.Screens.FirstOrDefault();
        var screen = new Screen { Name = $"Screen{n}", Width = first?.Width ?? 1024, Height = first?.Height ?? 600 };
        Project.Screens.Add(screen);
        if (Project.StartScreen.Length == 0) Project.StartScreen = screen.Name;
        SelectedScreen = Screens.First(s => s.Screen == screen);
    }

    private bool HasScreen() => SelectedScreen is not null;

    [RelayCommand(CanExecute = nameof(HasScreen))]
    private void DeleteScreen()
    {
        if (SelectedScreen is not { } s) return;
        Project.Screens.Remove(s.Screen);
        if (Project.StartScreen == s.Name) Project.StartScreen = Project.Screens.FirstOrDefault()?.Name ?? "";
    }

    [RelayCommand(CanExecute = nameof(HasScreen))]
    private void SetStartScreen()
    {
        if (SelectedScreen is { } s) Project.StartScreen = s.Name;
        OnPropertyChanged(nameof(IsStartScreen));
    }

    // ------------------------------------------------------------------ widgets

    private static (double W, double H) DefaultSize(string type) => type switch
    {
        "label" => (240, 40),
        "bar" or "gauge" => (80, 240),
        "trend" => (400, 200),
        "numeric_display" or "numeric_entry" or "text_display" or "text_entry" => (180, 60),
        "rectangle" => (200, 120),
        _ => (160, 70),
    };

    [RelayCommand(CanExecute = nameof(HasScreen))]
    private void AddWidget(string type)
    {
        if (SelectedScreen is not { } screen) return;
        int n = 1;
        while (screen.Screen.Widgets.Any(w => w.Id == $"{type}{n}")) n++;
        var (w, h) = DefaultSize(type);
        double offset = 20 + 10 * (screen.Screen.Widgets.Count % 20);
        var props = new JsonObject
        {
            ["id"] = $"{type}{n}",
            ["type"] = type,
            ["x"] = offset,
            ["y"] = offset,
            ["w"] = w,
            ["h"] = h,
        };
        if (type is "label" or "button" or "toggle" or "goto") props["text"] = type == "label" ? "Label" : type.ToUpperInvariant();
        if (type == "button") props["action"] = "momentary";
        if (type == "goto" && Project.Screens.FirstOrDefault(s => s != screen.Screen) is { } target) props["screen"] = target.Name;
        var widget = new Widget(props);
        screen.Screen.Widgets.Add(widget);
        SelectedWidget = screen.ItemFor(widget);
    }

    private bool HasWidgetSelected() => SelectedWidget is not null;

    [RelayCommand(CanExecute = nameof(HasWidgetSelected))]
    private void DeleteWidget()
    {
        if (SelectedScreen is { } s && SelectedWidget is { } w)
        {
            s.Screen.Widgets.Remove(w.Widget);
            SelectedWidget = null;
        }
    }

    [RelayCommand(CanExecute = nameof(HasWidgetSelected))]
    private void DuplicateWidget()
    {
        if (SelectedScreen is not { } s || SelectedWidget is not { } w) return;
        var copy = w.Widget.Clone();
        int n = 2;
        while (s.Screen.Widgets.Any(x => x.Id == $"{w.Id}_{n}")) n++;
        copy.Id = $"{w.Id}_{n}";
        copy.X += 20;
        copy.Y += 20;
        s.Screen.Widgets.Add(copy);
        SelectedWidget = s.ItemFor(copy);
    }

    /// <summary>Move/resize helper for the canvas: snaps to the grid and keeps the widget on screen.</summary>
    public void MoveTo(WidgetItem item, double x, double y)
    {
        if (SelectedScreen is not { } s) return;
        item.X = Math.Clamp(Snap(x), 0, Math.Max(0, s.Width - item.W));
        item.Y = Math.Clamp(Snap(y), 0, Math.Max(0, s.Height - item.H));
    }

    public void ResizeTo(WidgetItem item, double w, double h)
    {
        if (SelectedScreen is not { } s) return;
        item.W = Math.Clamp(Snap(w), 10, Math.Max(10, s.Width - item.X));
        item.H = Math.Clamp(Snap(h), 10, Math.Max(10, s.Height - item.Y));
    }

    private static double Snap(double v) => Math.Round(v / Grid) * Grid;
}
