using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Whmi.Core;
using Whmi.Core.Model;

namespace Whmi.Studio.ViewModels;

/// <summary>Fixed choice lists for combo boxes.</summary>
public static class Lists
{
    public const string EmptySlot = "(empty)";
    public static IReadOnlyList<DataType> DataTypes { get; } = Core.DataTypes.All;
    public static IReadOnlyList<TagSource> Sources { get; } = [TagSource.Memory, TagSource.Io, TagSource.Plc];
    public static IReadOnlyList<TagAccess> Accesses { get; } = [TagAccess.Read, TagAccess.ReadWrite];
    public static IReadOnlyList<string> Drivers { get; } = PlcConnection.Drivers;
    public static IReadOnlyList<string> WidgetTypes { get; } = Widget.Types;
    public static IReadOnlyList<string> ButtonActions { get; } = Widget.ButtonActions;
    public static IReadOnlyList<string> SlotChoices { get; } = [EmptySlot, .. IoCatalog.Modules.Select(m => m.Catalog)];
}

// ---------------------------------------------------------------------- receiver

public sealed partial class SlotRow : ObservableObject
{
    private readonly IoSlot _slot;

    public SlotRow(IoSlot slot)
    {
        _slot = slot;
        slot.PropertyChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(Choice));
            OnPropertyChanged(nameof(Description));
            OnPropertyChanged(nameof(Points));
        };
    }

    public string Label => $"Slot {_slot.Number}";

    public string Choice
    {
        get => _slot.Catalog.Length == 0 ? Lists.EmptySlot : _slot.Catalog;
        set => _slot.Catalog = value == Lists.EmptySlot || value is null ? "" : value;
    }

    public string Description => _slot.Module?.Description ?? (_slot.Catalog.Length > 0 ? "unknown module" : "");

    /// <summary>Example address of the first point, to show users how to address it.</summary>
    public string Points => _slot.Module is { } m ? $"Slot{_slot.Number}.{m.Kind}.0 … .{m.Channels - 1}" : "";
}

public sealed class ReceiverViewModel : SectionViewModel
{
    public ReceiverViewModel(Project project) : base("Project & I/O", "🖧")
    {
        Project = project;
        Slots = project.Receiver.Slots.Select(s => new SlotRow(s)).ToList();
    }

    public Project Project { get; }
    public ReceiverConfig Receiver => Project.Receiver;
    public IReadOnlyList<SlotRow> Slots { get; }
}

// ---------------------------------------------------------------------- PLC connections

public sealed partial class ConnectionsViewModel : SectionViewModel
{
    public ConnectionsViewModel(Project project) : base("PLC Connections", "🔌") => Project = project;

    public Project Project { get; }
    public ObservableCollection<PlcConnection> Connections => Project.PlcConnections;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(RemoveCommand))]
    private PlcConnection? _selectedConnection;

    [RelayCommand]
    private void Add()
    {
        int n = 1;
        while (Project.FindConnection($"PLC{n}") is not null) n++;
        var conn = new PlcConnection { Name = $"PLC{n}", Host = "192.168.1.10" };
        Connections.Add(conn);
        SelectedConnection = conn;
    }

    private bool CanRemove() => SelectedConnection is not null;

    [RelayCommand(CanExecute = nameof(CanRemove))]
    private void Remove()
    {
        if (SelectedConnection is { } c) Connections.Remove(c);
    }
}
