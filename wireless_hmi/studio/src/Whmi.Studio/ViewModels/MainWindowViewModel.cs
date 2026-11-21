using System.Collections.ObjectModel;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Whmi.Core.Model;
using Whmi.Studio.Services;

namespace Whmi.Studio.ViewModels;

public abstract partial class SectionViewModel(string title, string glyph) : ObservableObject
{
    public string Title { get; } = title;
    public string Glyph { get; } = glyph;
}

public sealed class IssueItem(Issue issue)
{
    public Issue Issue { get; } = issue;
    public string Glyph => Issue.Severity == Severity.Error ? "⛔" : "⚠";
    public string Path => Issue.Path;
    public string Message => Issue.Message;
}

public partial class MainWindowViewModel : ObservableObject
{
    private readonly IDialogs _dialogs;
    private ProjectWatcher? _watcher;
    private bool _validationQueued;

    public MainWindowViewModel(IDialogs dialogs)
    {
        _dialogs = dialogs;
        Online = new OnlineViewModel(this, dialogs);
        LoadProject(NewProjectTemplate(), null);
    }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Title))]
    private Project _project = null!;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Title))]
    private string? _filePath;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Title))]
    private bool _isDirty;

    [ObservableProperty] private string _statusText = "Ready";
    [ObservableProperty] private SectionViewModel? _currentSection;
    [ObservableProperty] private string _issueSummary = "";

    public ReceiverViewModel Receiver { get; private set; } = null!;
    public ConnectionsViewModel Connections { get; private set; } = null!;
    public TagsViewModel Tags { get; private set; } = null!;
    public ScreensViewModel Screens { get; private set; } = null!;
    public OnlineViewModel Online { get; }
    public ObservableCollection<SectionViewModel> Sections { get; } = [];
    public ObservableCollection<IssueItem> Issues { get; } = [];
    public IReadOnlyList<Issue> LastIssues { get; private set; } = [];

    public string Title =>
        $"WHMI Studio - {Project?.Name}{(IsDirty ? " *" : "")}{(FilePath is null ? "" : $"  [{System.IO.Path.GetFileName(FilePath)}]")}";

    public static Project NewProjectTemplate()
    {
        var p = new Project { Name = "NewProject" };
        p.Receiver.Slots[0].Catalog = "DI8";
        p.Receiver.Slots[1].Catalog = "DO8";
        p.Receiver.Slots[2].Catalog = "AI4";
        p.Receiver.Slots[3].Catalog = "AO2";
        p.Screens.Add(new Screen { Name = "Main" });
        p.StartScreen = "Main";
        return p;
    }

    public void LoadProject(Project project, string? path)
    {
        _watcher?.Dispose();
        var previous = CurrentSection?.Title;
        Project = project;
        FilePath = path;
        Receiver = new ReceiverViewModel(project);
        Connections = new ConnectionsViewModel(project);
        Tags = new TagsViewModel(this, project, _dialogs);
        Screens = new ScreensViewModel(project);
        Sections.Clear();
        foreach (var s in new SectionViewModel[] { Receiver, Connections, Tags, Screens, Online }) Sections.Add(s);
        OnPropertyChanged(nameof(Receiver));
        OnPropertyChanged(nameof(Connections));
        OnPropertyChanged(nameof(Tags));
        OnPropertyChanged(nameof(Screens));
        CurrentSection = Sections.FirstOrDefault(s => s.Title == previous) ?? Tags;
        _watcher = new ProjectWatcher(project);
        _watcher.Changed += OnProjectChanged;
        ValidateNow();
        IsDirty = false;
        Online.OnProjectLoaded();
    }

    private void OnProjectChanged()
    {
        IsDirty = true;
        OnPropertyChanged(nameof(Title));
        if (_validationQueued) return;
        _validationQueued = true;
        if (Dispatcher.UIThread.CheckAccess())
            Dispatcher.UIThread.Post(ValidateNow, DispatcherPriority.Background);
        else
            ValidateNow();
    }

    public void ValidateNow()
    {
        _validationQueued = false;
        LastIssues = Project.Validate();
        Issues.Clear();
        foreach (var i in LastIssues.OrderBy(i => i.Severity)) Issues.Add(new IssueItem(i));
        int errors = LastIssues.Count(i => i.Severity == Severity.Error);
        int warnings = LastIssues.Count - errors;
        IssueSummary = errors == 0 && warnings == 0 ? "No problems" : $"{errors} error(s), {warnings} warning(s)";
        Screens.RefreshLookups();
        Tags.RefreshLookups();
    }

    // ------------------------------------------------------------------ navigation

    public void Navigate(IssueItem? item)
    {
        if (item is null) return;
        var path = item.Path;
        if (path.StartsWith("tags."))
        {
            CurrentSection = Tags;
            Tags.Select(path["tags.".Length..]);
        }
        else if (path.StartsWith("screens.") || path == "start_screen")
        {
            CurrentSection = Screens;
            Screens.SelectByPath(path);
        }
        else if (path.StartsWith("plc_connections"))
        {
            CurrentSection = Connections;
            if (int.TryParse(path.Split('[', ']').ElementAtOrDefault(1), out var idx) && idx < Project.PlcConnections.Count)
                Connections.SelectedConnection = Project.PlcConnections[idx];
        }
        else
        {
            CurrentSection = Receiver;
        }
    }

    // ------------------------------------------------------------------ file commands

    private async Task<bool> ConfirmDiscardAsync() =>
        !IsDirty || await _dialogs.ConfirmAsync("Unsaved changes", $"Discard unsaved changes to '{Project.Name}'?");

    [RelayCommand]
    private async Task NewAsync()
    {
        if (!await ConfirmDiscardAsync()) return;
        LoadProject(NewProjectTemplate(), null);
        StatusText = "New project";
    }

    [RelayCommand]
    private async Task OpenAsync()
    {
        if (!await ConfirmDiscardAsync()) return;
        var path = await _dialogs.OpenFileAsync("Open WHMI project", "WHMI project", "*.whp");
        if (path is not null) OpenPath(path);
    }

    public void OpenPath(string path)
    {
        try
        {
            LoadProject(Project.Load(path), path);
            StatusText = $"Opened {path}";
        }
        catch (Exception e) when (e is IOException or FormatException or System.Text.Json.JsonException or UnauthorizedAccessException)
        {
            StatusText = $"Cannot open {path}: {e.Message}";
        }
    }

    [RelayCommand]
    private async Task SaveAsync()
    {
        if (FilePath is null) await SaveAsAsync();
        else SaveTo(FilePath);
    }

    [RelayCommand]
    private async Task SaveAsAsync()
    {
        var path = await _dialogs.SaveFileAsync("Save WHMI project", $"{Project.Name}.whp", "WHMI project", "whp");
        if (path is not null) SaveTo(path);
    }

    public void SaveTo(string path)
    {
        try
        {
            Project.Save(path);
            FilePath = path;
            IsDirty = false;
            StatusText = $"Saved {path}";
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            StatusText = $"Save failed: {e.Message}";
        }
    }

    [RelayCommand]
    private void Validate()
    {
        ValidateNow();
        StatusText = $"Validation: {IssueSummary}";
    }

    /// <summary>Build the project, reporting problems. Returns null if it has errors.</summary>
    public CompiledProject? TryBuild()
    {
        ValidateNow();
        try
        {
            var compiled = Project.Build();
            StatusText = $"Build OK: {compiled.Project.Tags.Count} tags, sha256 {compiled.HashHex[..12]}";
            return compiled;
        }
        catch (ProjectException e)
        {
            StatusText = $"Build failed: {e.Issues.Count(i => i.Severity == Severity.Error)} error(s), see Problems";
            return null;
        }
    }

    [RelayCommand]
    private async Task BuildAsync()
    {
        var compiled = TryBuild();
        if (compiled is null) return;
        var path = await _dialogs.SaveFileAsync("Save compiled project", $"{Project.Name}.whc", "Compiled project", "whc");
        if (path is null) return;
        compiled.Save(path);
        StatusText = $"Built {path} (sha256 {compiled.HashHex[..12]})";
    }
}
