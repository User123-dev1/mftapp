using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.Globalization;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Whmi.Core;
using Whmi.Core.Model;
using Whmi.Studio.Services;

namespace Whmi.Studio.ViewModels;

/// <summary>One row of the tag database grid. Text columns parse what the user types.</summary>
public sealed partial class TagRow : ObservableObject
{
    public TagRow(Tag tag)
    {
        Tag = tag;
        tag.PropertyChanged += (_, e) =>
        {
            OnPropertyChanged(e.PropertyName);
            OnPropertyChanged(nameof(MinText));
            OnPropertyChanged(nameof(MaxText));
            OnPropertyChanged(nameof(DeadbandText));
            OnPropertyChanged(nameof(InitialText));
            OnPropertyChanged(nameof(FailsafeText));
            OnPropertyChanged(nameof(ScalingText));
            OnPropertyChanged(nameof(AddressHint));
        };
    }

    public Tag Tag { get; }

    public string Name { get => Tag.Name; set => Tag.Name = value.Trim(); }
    public DataType DataType { get => Tag.DataType; set => Tag.DataType = value; }

    public TagSource Source
    {
        get => Tag.Source;
        set
        {
            Tag.Source = value;
            if (value != TagSource.Plc) Tag.Connection = "";
        }
    }

    public string Connection { get => Tag.Connection; set => Tag.Connection = value?.Trim() ?? ""; }
    public string Address { get => Tag.Address; set => Tag.Address = value?.Trim() ?? ""; }
    public TagAccess Access { get => Tag.Access; set => Tag.Access = value; }
    public string Units { get => Tag.Units; set => Tag.Units = value ?? ""; }
    public string Description { get => Tag.Description; set => Tag.Description = value ?? ""; }
    public bool Momentary { get => Tag.Momentary; set => Tag.Momentary = value; }
    public bool Retentive { get => Tag.Retentive; set => Tag.Retentive = value; }
    public bool Safety { get => Tag.Safety; set => Tag.Safety = value; }
    public bool FailsafeOnAnyLoss { get => Tag.FailsafeOnAnyLoss; set => Tag.FailsafeOnAnyLoss = value; }

    public string MinText { get => Num(Tag.Min); set => Tag.Min = ParseNum(value); }
    public string MaxText { get => Num(Tag.Max); set => Tag.Max = ParseNum(value); }
    public string DeadbandText { get => Tag.Deadband == 0 ? "" : Num(Tag.Deadband); set => Tag.Deadband = ParseNum(value) ?? 0; }
    public string InitialText { get => DataTypes.Format(Tag.Initial); set => Tag.Initial = DataTypes.ParseLiteral(value); }
    public string FailsafeText { get => DataTypes.Format(Tag.FailsafeValue); set => Tag.FailsafeValue = DataTypes.ParseLiteral(value); }
    public string ScalingText { get => Services.ScalingText.Format(Tag.Scaling); set => Tag.Scaling = Services.ScalingText.Parse(value); }

    public string AddressHint => Tag.Source switch
    {
        TagSource.Io => "e.g. Slot1.DI.0",
        TagSource.Plc => "Logix tag, e.g. Program:Main.Speed",
        _ => "",
    };

    // live values while Studio monitors a receiver
    [ObservableProperty] private string _liveValue = "";
    [ObservableProperty] private string _liveQuality = "";

    private static string Num(double? d) => d is { } v ? v.ToString("R", CultureInfo.InvariantCulture) : "";

    private static double? ParseNum(string? s) =>
        string.IsNullOrWhiteSpace(s) ? null
        : double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d
        : throw new FormatException($"'{s}' is not a number");
}

public sealed partial class TagsViewModel : SectionViewModel
{
    private readonly MainWindowViewModel _main;
    private readonly IDialogs _dialogs;
    private readonly Dictionary<Tag, TagRow> _rows = [];

    public TagsViewModel(MainWindowViewModel main, Project project, IDialogs dialogs) : base("Tags", "🏷")
    {
        _main = main;
        _dialogs = dialogs;
        Project = project;
        project.Tags.CollectionChanged += OnTagsChanged;
        Rebuild();
    }

    public Project Project { get; }
    public ObservableCollection<TagRow> Rows { get; } = [];
    public ObservableCollection<string> ConnectionNames { get; } = [];

    [ObservableProperty] private string _filter = "";

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(DeleteCommand), nameof(DuplicateCommand))]
    private TagRow? _selectedRow;

    [ObservableProperty] private bool _showLive;

    public string CountText => Rows.Count == Project.Tags.Count
        ? $"{Project.Tags.Count} tags"
        : $"{Rows.Count} of {Project.Tags.Count} tags";

    partial void OnFilterChanged(string value) => Rebuild();

    public TagRow RowFor(Tag t)
    {
        if (!_rows.TryGetValue(t, out var row)) _rows[t] = row = new TagRow(t);
        return row;
    }

    public IEnumerable<TagRow> AllRows => Project.Tags.Select(RowFor);

    private void OnTagsChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        foreach (var t in e.OldItems?.OfType<Tag>() ?? []) _rows.Remove(t);
        Rebuild();
    }

    private bool Matches(Tag t) =>
        Filter.Length == 0
        || t.Name.Contains(Filter, StringComparison.OrdinalIgnoreCase)
        || t.Address.Contains(Filter, StringComparison.OrdinalIgnoreCase)
        || t.Description.Contains(Filter, StringComparison.OrdinalIgnoreCase);

    private void Rebuild()
    {
        var selected = SelectedRow;
        Rows.Clear();
        foreach (var t in Project.Tags.Where(Matches)) Rows.Add(RowFor(t));
        if (selected is not null && Rows.Contains(selected)) SelectedRow = selected;
        OnPropertyChanged(nameof(CountText));
    }

    public void RefreshLookups()
    {
        var names = Project.PlcConnections.Select(c => c.Name).ToList();
        if (!names.SequenceEqual(ConnectionNames))
        {
            ConnectionNames.Clear();
            foreach (var n in names) ConnectionNames.Add(n);
        }
    }

    public void Select(string tagName)
    {
        var tag = Project.FindTag(tagName);
        if (tag is null) return;
        if (!Matches(tag)) Filter = "";
        SelectedRow = RowFor(tag);
    }

    private string UniqueName(string stem)
    {
        if (Project.FindTag(stem) is null) return stem;
        int n = 2;
        while (Project.Tags.Any(t => t.Name.Equals($"{stem}_{n}", StringComparison.OrdinalIgnoreCase))) n++;
        return $"{stem}_{n}";
    }

    [RelayCommand]
    private void Add()
    {
        var tag = new Tag { Name = UniqueName("NewTag"), DataType = DataType.DINT, Access = TagAccess.ReadWrite };
        int index = SelectedRow is { } r ? Project.Tags.IndexOf(r.Tag) + 1 : Project.Tags.Count;
        Project.Tags.Insert(index, tag);
        SelectedRow = RowFor(tag);
    }

    private bool HasSelection() => SelectedRow is not null;

    [RelayCommand(CanExecute = nameof(HasSelection))]
    private void Duplicate()
    {
        if (SelectedRow is not { } row) return;
        var copy = row.Tag.Clone();
        copy.TagId = 0;
        copy.Name = UniqueName(row.Tag.Name);
        if (copy.Source == TagSource.Io) copy.Address = ""; // an I/O point can only be used once
        Project.Tags.Insert(Project.Tags.IndexOf(row.Tag) + 1, copy);
        SelectedRow = RowFor(copy);
    }

    [RelayCommand(CanExecute = nameof(HasSelection))]
    private void Delete()
    {
        if (SelectedRow is not { } row) return;
        int index = Project.Tags.IndexOf(row.Tag);
        Project.Tags.Remove(row.Tag);
        SelectedRow = Rows.Count == 0 ? null : Rows[Math.Min(index, Rows.Count - 1)];
    }

    [RelayCommand]
    private async Task ImportCsvAsync()
    {
        var path = await _dialogs.OpenFileAsync("Import tags from CSV", "CSV", "*.csv");
        if (path is not null) ImportCsv(await File.ReadAllTextAsync(path));
    }

    /// <summary>Add or update tags by name from CSV text.</summary>
    public void ImportCsv(string csv)
    {
        try
        {
            var tags = TagCsv.Import(csv);
            int added = 0, updated = 0;
            foreach (var t in tags)
            {
                var existing = Project.Tags.FirstOrDefault(x => x.Name.Equals(t.Name, StringComparison.OrdinalIgnoreCase));
                if (existing is null)
                {
                    Project.Tags.Add(t);
                    added++;
                }
                else
                {
                    Project.Tags[Project.Tags.IndexOf(existing)] = t;
                    updated++;
                }
            }
            _main.StatusText = $"CSV import: {added} added, {updated} updated";
        }
        catch (FormatException e)
        {
            _main.StatusText = $"CSV import failed: {e.Message}";
        }
    }

    [RelayCommand]
    private async Task ExportCsvAsync()
    {
        var path = await _dialogs.SaveFileAsync("Export tags to CSV", $"{Project.Name}_tags.csv", "CSV", "csv");
        if (path is null) return;
        await File.WriteAllTextAsync(path, TagCsv.Export(Project.Tags));
        _main.StatusText = $"Exported {Project.Tags.Count} tags to {path}";
    }
}
