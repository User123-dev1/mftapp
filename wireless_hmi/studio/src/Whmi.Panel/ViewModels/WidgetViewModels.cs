using System.Globalization;
using Avalonia;
using Avalonia.Media;
using CommunityToolkit.Mvvm.ComponentModel;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Runtime;

namespace Whmi.Panel.ViewModels;

/// <summary>Something the touch layer can press (see Controls/TouchArea).</summary>
public interface IPressable
{
    bool IsEnabled { get; }
    void Press();
    void Release(bool inside);
}

/// <summary>Base for all widget view models: geometry, bound tag, live value and quality.</summary>
public abstract partial class WidgetViewModel : ObservableObject
{
    protected WidgetViewModel(Widget widget, PanelViewModel panel)
    {
        Widget = widget;
        Panel = panel;
        Tag = panel.Runtime?.FindTag(widget.Tag);
    }

    public Widget Widget { get; }
    public PanelViewModel Panel { get; }
    public Tag? Tag { get; }
    public double X => Widget.X;
    public double Y => Widget.Y;
    public double W => Widget.W;
    public double H => Widget.H;
    public double FontSize => Math.Clamp(Math.Min(H * 0.34, W * 0.16), 11, 44);

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsStale), nameof(Opacity))]
    private CachedValue? _value;

    /// <summary>The value can't be trusted (no link, PLC down, I/O fault): never show it as live.</summary>
    public bool IsStale => Tag is not null && (Value is null || Value.Quality.IsBad());
    public double Opacity => IsStale ? 0.45 : 1.0;

    public void Refresh(CachedValue value)
    {
        Value = value;
        OnValueChanged();
    }

    protected virtual void OnValueChanged() => OnPropertyChanged(string.Empty);

    protected double Number =>
        Value?.Value switch
        {
            bool b => b ? 1 : 0,
            null or string => 0,
            var v => Convert.ToDouble(v, CultureInfo.InvariantCulture),
        };

    protected bool IsOn => Value?.Value switch
    {
        bool b => b,
        string s => s.Length > 0,
        null => false,
        _ => Number != 0,
    };

    protected string FormatValue()
    {
        if (Tag is null) return "?";
        if (IsStale) return "---";
        var text = PyFormat.Format(Widget.Format, Value!.Value);
        return Widget.Format is null && Tag.Units.Length > 0 ? $"{text} {Tag.Units}" : text;
    }

    protected static IBrush Brush(string? color, string fallback) =>
        Color.TryParse(color ?? fallback, out var c) || Color.TryParse(fallback, out c) ? new SolidColorBrush(c) : Brushes.Gray;
}

public sealed class LabelWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p)
{
    public string Text => Widget.Text ?? "";
    public IBrush Foreground => Brush(Widget.Color, "#F2F2F2");
}

public sealed class RectangleWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p)
{
    public IBrush Fill => Brush(Widget.Color, "#333333");
}

public sealed class IndicatorWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p)
{
    public string Text => IsStale ? "---" : IsOn ? Widget.OnText ?? "ON" : Widget.OffText ?? "OFF";
    public IBrush Fill => IsStale ? Brush(null, "#3A3A3A") : IsOn ? Brush(Widget.OnColor, "#2E7D32") : Brush(Widget.OffColor, "#4A4A4A");
}

/// <summary>numeric_display and text_display.</summary>
public sealed class DisplayWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p)
{
    public string Text => FormatValue();
    public string Caption => Widget.Text ?? Tag?.Description ?? "";
    public bool HasCaption => Caption.Length > 0 && H >= 56;
}

public sealed class BarWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p)
{
    private double Min => Widget.Min ?? Tag?.Min ?? 0;
    private double Max => Widget.Max ?? Tag?.Max ?? 100;
    public double Fraction => IsStale || Max <= Min ? 0 : Math.Clamp((Number - Min) / (Max - Min), 0, 1);
    public double FillHeight => Math.Max(0, (H - 4) * Fraction);
    public string Text => FormatValue();
}

public sealed class GaugeWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p)
{
    private double Min => Widget.Min ?? Tag?.Min ?? 0;
    private double Max => Widget.Max ?? Tag?.Max ?? 100;
    public double Size => Math.Min(W, H) - 12;
    public double Sweep => IsStale || Max <= Min ? 0 : 270 * Math.Clamp((Number - Min) / (Max - Min), 0, 1);
    public string Text => FormatValue();
}

public sealed partial class TrendWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p)
{
    public const int Samples = 120;
    private readonly Queue<double?> _samples = new();

    public IList<Point> Points { get; private set; } = [];
    public string Text => FormatValue();

    protected override void OnValueChanged() => OnPropertyChanged(nameof(Text));

    /// <summary>Called on a timer: appends the current value (or a gap when stale).</summary>
    public void Sample()
    {
        _samples.Enqueue(IsStale ? null : Number);
        while (_samples.Count > Samples) _samples.Dequeue();
        var values = _samples.Where(v => v is not null).Select(v => v!.Value).ToList();
        double min = Widget.Min ?? (values.Count > 0 ? values.Min() : 0);
        double max = Widget.Max ?? (values.Count > 0 ? values.Max() : 1);
        if (max <= min) max = min + 1;
        var pts = new List<Point>();
        int i = Samples - _samples.Count;
        foreach (var v in _samples)
        {
            if (v is { } d) pts.Add(new Point(W * i / (Samples - 1), (H - 4) - (H - 8) * (d - min) / (max - min)));
            i++;
        }
        Points = pts;
        OnPropertyChanged(nameof(Points));
    }
}

/// <summary>Push-button: momentary / set / reset / toggle / write.</summary>
public sealed partial class ButtonWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p), IPressable
{
    [ObservableProperty] private bool _isPressed;

    public double EnabledOpacity => IsEnabled ? 1 : 0.45;

    public string Action => Widget.Action ?? "momentary";
    public string Text => Widget.Text ?? Tag?.Name ?? "";
    public IBrush Fill => Brush(Widget.Color, "#3A6EA5");
    public bool IsEnabled => Tag is not null && Tag.Writable && Panel.CanWrite;
    public double PressOpacity => IsPressed ? 0.35 : 0;
    public bool ShowActive => Action == "toggle" && !IsStale && IsOn;

    partial void OnIsPressedChanged(bool value) => OnPropertyChanged(nameof(PressOpacity));

    public void Press()
    {
        if (!IsEnabled) return;
        IsPressed = true;
        if (Action == "momentary") Panel.Run(Panel.Runtime!.PressAsync(Tag!.Name), $"{Text}");
    }

    public void Release(bool inside)
    {
        if (!IsPressed) return;
        IsPressed = false;
        if (Tag is null) return;
        var rt = Panel.Runtime!;
        switch (Action)
        {
            case "momentary":
                Panel.Run(rt.ReleaseAsync(Tag.Name), Text); // always released, even when the finger slid off
                break;
            case "set" when inside:
                Panel.Run(rt.WriteAsync(Tag.Name, true), Text);
                break;
            case "reset" when inside:
                Panel.Run(rt.WriteAsync(Tag.Name, false), Text);
                break;
            case "toggle" when inside:
                Panel.Run(rt.WriteAsync(Tag.Name, !IsOn), Text);
                break;
            case "write" when inside && Widget.Props["value"] is { } v:
                Panel.Run(rt.WriteAsync(Tag.Name, Serialization.ToScalar(v) ?? 0L), Text);
                break;
        }
    }
}

public sealed partial class ToggleWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p), IPressable
{
    [ObservableProperty] private bool _isPressed;

    public double EnabledOpacity => IsEnabled ? 1 : 0.45;

    public string Text => Widget.Text ?? Tag?.Name ?? "";
    public string StateText => IsStale ? "---" : IsOn ? "ON" : "OFF";
    public IBrush Fill => !IsStale && IsOn ? Brush(Widget.OnColor ?? Widget.Color, "#2E7D32") : Brush(Widget.OffColor, "#4A4A4A");
    public bool IsEnabled => Tag is not null && Tag.Writable && Panel.CanWrite && !IsStale;

    public void Press() => IsPressed = IsEnabled;

    public void Release(bool inside)
    {
        if (!IsPressed) return;
        IsPressed = false;
        if (inside && Tag is not null) Panel.Run(Panel.Runtime!.WriteAsync(Tag.Name, !IsOn), Text);
    }
}

/// <summary>numeric_entry and text_entry: tap to open the keypad / text editor.</summary>
public sealed partial class EntryWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p), IPressable
{
    [ObservableProperty] private bool _isPressed;

    public double EnabledOpacity => IsEnabled ? 1 : 0.45;

    public string Text => FormatValue();
    public bool IsEnabled => Tag is not null && Tag.Writable && Panel.CanWrite;

    public void Press() => IsPressed = IsEnabled;

    public void Release(bool inside)
    {
        if (!IsPressed) return;
        IsPressed = false;
        if (inside && Tag is not null) Panel.OpenKeypad(Tag, Widget.Type == "text_entry");
    }
}

public sealed partial class GotoWidgetViewModel(Widget w, PanelViewModel p) : WidgetViewModel(w, p), IPressable
{
    [ObservableProperty] private bool _isPressed;

    public string Text => Widget.Text ?? Widget.TargetScreen ?? "";
    public IBrush Fill => Brush(Widget.Color, "#444444");
    public bool IsEnabled => true;

    public void Press() => IsPressed = true;

    public void Release(bool inside)
    {
        if (!IsPressed) return;
        IsPressed = false;
        if (inside && Widget.TargetScreen is { } target) Panel.ShowScreen(target);
    }
}

public sealed class ScreenViewModel
{
    public ScreenViewModel(Screen screen, PanelViewModel panel)
    {
        Screen = screen;
        Widgets = screen.Widgets.Select(w => Create(w, panel)).ToList();
    }

    public Screen Screen { get; }
    public string Name => Screen.Name;
    public double Width => Screen.Width;
    public double Height => Screen.Height;
    public IBrush Background => Color.TryParse(Screen.Background, out var c) ? new SolidColorBrush(c) : Brushes.Black;
    public IReadOnlyList<WidgetViewModel> Widgets { get; }

    private static WidgetViewModel Create(Widget w, PanelViewModel p) => w.Type switch
    {
        "label" => new LabelWidgetViewModel(w, p),
        "rectangle" => new RectangleWidgetViewModel(w, p),
        "indicator" => new IndicatorWidgetViewModel(w, p),
        "numeric_display" or "text_display" => new DisplayWidgetViewModel(w, p),
        "bar" => new BarWidgetViewModel(w, p),
        "gauge" => new GaugeWidgetViewModel(w, p),
        "trend" => new TrendWidgetViewModel(w, p),
        "button" => new ButtonWidgetViewModel(w, p),
        "toggle" => new ToggleWidgetViewModel(w, p),
        "numeric_entry" or "text_entry" => new EntryWidgetViewModel(w, p),
        "goto" => new GotoWidgetViewModel(w, p),
        _ => new LabelWidgetViewModel(w, p), // unknown types render as an empty label
    };
}
