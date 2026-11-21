using System.Collections;
using System.Collections.Specialized;
using System.ComponentModel;
using Whmi.Core.Model;

namespace Whmi.Studio.Services;

/// <summary>Raises <see cref="Changed"/> when anything in a project changes (for dirty flag and live validation).</summary>
public sealed class ProjectWatcher : IDisposable
{
    private readonly Project _project;
    private readonly HashSet<INotifyPropertyChanged> _objects = [];

    public ProjectWatcher(Project project)
    {
        _project = project;
        Attach(project);
        Attach(project.Receiver);
        foreach (var slot in project.Receiver.Slots) Attach(slot);
        AttachCollection(project.Tags);
        AttachCollection(project.PlcConnections);
        AttachCollection(project.Screens);
        foreach (var s in project.Screens) AttachCollection(s.Widgets);
    }

    public event Action? Changed;

    private void Attach(INotifyPropertyChanged o)
    {
        if (_objects.Add(o)) o.PropertyChanged += OnPropertyChanged;
    }

    private void AttachCollection(INotifyCollectionChanged c)
    {
        c.CollectionChanged += OnCollectionChanged;
        foreach (var item in (IEnumerable)c) AttachItem(item);
    }

    private void AttachItem(object? item)
    {
        if (item is INotifyPropertyChanged npc) Attach(npc);
        if (item is Screen s) AttachCollection(s.Widgets);
    }

    private void OnPropertyChanged(object? sender, PropertyChangedEventArgs e) => Changed?.Invoke();

    private void OnCollectionChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        foreach (var item in e.NewItems ?? Array.Empty<object>()) AttachItem(item);
        Changed?.Invoke();
    }

    public void Dispose()
    {
        foreach (var o in _objects) o.PropertyChanged -= OnPropertyChanged;
        _objects.Clear();
        _project.Tags.CollectionChanged -= OnCollectionChanged;
        _project.PlcConnections.CollectionChanged -= OnCollectionChanged;
        _project.Screens.CollectionChanged -= OnCollectionChanged;
        foreach (var s in _project.Screens) s.Widgets.CollectionChanged -= OnCollectionChanged;
    }
}
