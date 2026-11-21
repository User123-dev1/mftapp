using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Whmi.Panel.ViewModels;

namespace Whmi.Panel.Controls;

/// <summary>
/// Touch/mouse surface for pressable widgets. A press is always paired with exactly one release:
/// on lift, when the finger slides off, or when the pointer capture is lost (another window,
/// a dialog, the OS taking the touch). A momentary output can never be left held by the UI.
/// </summary>
public class TouchArea : Border
{
    private IPointer? _pointer;

    private IPressable? Target => DataContext as IPressable;

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        if (_pointer is not null || Target is not { IsEnabled: true } target) return;
        _pointer = e.Pointer;
        e.Pointer.Capture(this);
        target.Press();
        e.Handled = true;
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        if (_pointer != e.Pointer) return;
        if (!new Rect(Bounds.Size).Contains(e.GetPosition(this))) End(inside: false); // slid off
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        if (_pointer != e.Pointer) return;
        End(new Rect(Bounds.Size).Contains(e.GetPosition(this)));
        e.Handled = true;
    }

    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e)
    {
        base.OnPointerCaptureLost(e);
        if (_pointer == e.Pointer) End(inside: false);
    }

    protected override void OnDetachedFromVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnDetachedFromVisualTree(e);
        End(inside: false); // screen changed while held
    }

    private void End(bool inside)
    {
        var pointer = _pointer;
        if (pointer is null) return;
        _pointer = null;
        Target?.Release(inside);
        if (pointer.Captured == this) pointer.Capture(null);
    }
}
