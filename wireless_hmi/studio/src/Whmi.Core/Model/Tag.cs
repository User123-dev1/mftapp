namespace Whmi.Core.Model;

public enum TagSource { Memory, Io, Plc, System }

public enum TagAccess { Read, ReadWrite }

public sealed class Tag : ModelBase
{
    private string _name = "";
    private DataType _dataType = DataType.DINT;
    private TagSource _source = TagSource.Memory;
    private string _address = "";
    private string _connection = "";
    private TagAccess _access = TagAccess.Read;
    private string _description = "";
    private string _units = "";
    private object? _initial;
    private bool _retentive;
    private Scaling? _scaling;
    private double? _min;
    private double? _max;
    private double _deadband;
    private bool _momentary;
    private object? _releaseValue;
    private object? _failsafeValue;
    private bool _failsafeOnAnyLoss;
    private bool _safety;
    private int _tagId;

    public string Name { get => _name; set => Set(ref _name, value); }
    public DataType DataType { get => _dataType; set => Set(ref _dataType, value); }
    public TagSource Source { get => _source; set => Set(ref _source, value); }

    /// <summary>io: "Slot1.DI.0"; plc: the PLC tag name; system: the system key.</summary>
    public string Address { get => _address; set => Set(ref _address, value); }

    public string Connection { get => _connection; set => Set(ref _connection, value); }
    public TagAccess Access { get => _access; set => Set(ref _access, value); }
    public string Description { get => _description; set => Set(ref _description, value); }
    public string Units { get => _units; set => Set(ref _units, value); }

    /// <summary>bool, long, double or string as read from JSON; <c>null</c> = not set.</summary>
    public object? Initial { get => _initial; set => Set(ref _initial, value); }

    public bool Retentive { get => _retentive; set => Set(ref _retentive, value); }
    public Scaling? Scaling { get => _scaling; set => Set(ref _scaling, value); }
    public double? Min { get => _min; set => Set(ref _min, value); }
    public double? Max { get => _max; set => Set(ref _max, value); }
    public double Deadband { get => _deadband; set => Set(ref _deadband, value); }
    public bool Momentary { get => _momentary; set => Set(ref _momentary, value); }
    public object? ReleaseValue { get => _releaseValue; set => Set(ref _releaseValue, value); }
    public object? FailsafeValue { get => _failsafeValue; set => Set(ref _failsafeValue, value); }
    public bool FailsafeOnAnyLoss { get => _failsafeOnAnyLoss; set => Set(ref _failsafeOnAnyLoss, value); }

    /// <summary>Display-only safety signal (e.g. E-stop healthy). Can never be written.</summary>
    public bool Safety { get => _safety; set => Set(ref _safety, value); }

    /// <summary>Wire id, assigned by <see cref="Project.Build"/>.</summary>
    public int TagId { get => _tagId; set => Set(ref _tagId, value); }

    public bool Writable => Access == TagAccess.ReadWrite && !Safety;

    public Tag Clone() => Serialization.TagFromJson(Serialization.TagToJson(this, includeId: true));

    public override string ToString() => Name;
}
