using System.Text.Json.Nodes;
using Whmi.Core;
using Whmi.Core.Model;

namespace Whmi.Tests;

public class DataTypeTests
{
    [Theory]
    [InlineData(DataType.BOOL, 1L, true)]
    [InlineData(DataType.BOOL, "off", false)]
    [InlineData(DataType.SINT, -128L, -128L)]
    [InlineData(DataType.INT, 32767L, 32767L)]
    [InlineData(DataType.DINT, 5.0, 5L)]
    [InlineData(DataType.UDINT, "0x10", 16L)]
    [InlineData(DataType.REAL, 1L, 1.0)]
    [InlineData(DataType.LREAL, "2.5", 2.5)]
    [InlineData(DataType.STRING, "abc", "abc")]
    public void CoerceOk(DataType type, object value, object expected) =>
        Assert.Equal(expected, DataTypes.Coerce(type, value));

    [Theory]
    [InlineData(DataType.SINT, 128L, true)]
    [InlineData(DataType.USINT, -1L, true)]
    [InlineData(DataType.INT, 2.5, false)]
    [InlineData(DataType.BOOL, 2L, false)]
    [InlineData(DataType.DINT, "abc", false)]
    [InlineData(DataType.REAL, 1e39, true)]
    [InlineData(DataType.STRING, 5L, false)]
    public void CoerceRejectsLossy(DataType type, object value, bool outOfRange)
    {
        var e = Assert.Throws<CoercionException>(() => DataTypes.Coerce(type, value));
        Assert.Equal(outOfRange, e.OutOfRange);
    }

    [Fact]
    public void LongStringRejected() =>
        Assert.True(Assert.Throws<CoercionException>(() => DataTypes.Coerce(DataType.STRING, new string('x', 83))).OutOfRange);

    [Fact]
    public void UlintFullRange() =>
        Assert.Equal(ulong.MaxValue, DataTypes.Coerce(DataType.ULINT, "18446744073709551615"));

    [Fact]
    public void RealRoundsToSinglePrecision() => Assert.NotEqual(0.1, DataTypes.Coerce(DataType.REAL, 0.1));

    [Fact]
    public void ScalingRoundTrip()
    {
        var s = new Scaling(0, 32000, 0, 100);
        Assert.Equal(50, s.ToEng(16000));
        Assert.Equal(8000, s.ToRaw(25));
        Assert.Equal(100, new Scaling(0, 32000, 0, 100, Clamp: true).ToEng(40000));
    }

    [Theory]
    [InlineData(0.1, "0.1")]
    [InlineData(50.0, "50.0")]
    [InlineData(1e-05, "1e-05")]
    [InlineData(1.5e-07, "1.5e-07")]
    [InlineData(1e16, "1e+16")]
    [InlineData(1e15, "1000000000000000.0")]
    [InlineData(-2.5, "-2.5")]
    [InlineData(0.0001, "0.0001")]
    public void PythonFloatRepr(double d, string expected) => Assert.Equal(expected, CanonicalJson.PyFloat(d));
}

public class ProjectTests
{
    private static Project BaseProject()
    {
        var p = new Project { Name = "T" };
        p.Receiver.Slots[0].Catalog = "DI8";
        p.Receiver.Slots[1].Catalog = "DO8";
        p.Receiver.Slots[2].Catalog = "AI4";
        return p;
    }

    private static string Errors(Project p) =>
        string.Join("\n", p.Validate().Where(i => i.Severity == Severity.Error).Select(i => $"{i.Path}: {i.Message}"));

    private static Tag Io(string name, DataType type, string address, TagAccess access = TagAccess.Read) =>
        new() { Name = name, DataType = type, Source = TagSource.Io, Address = address, Access = access };

    [Fact]
    public void ExampleBuilds()
    {
        var compiled = Project.Load(TestPaths.Example).Build();
        Assert.Equal(1, compiled.Tag("Line.Start_PB")!.TagId);
        Assert.NotNull(compiled.Tag("_System.Link.Clients"));
        Assert.Same(compiled.Tag("Tank.Level"), compiled.Tag("tank.level"));
    }

    [Fact]
    public void BuildIsDeterministicAndReloadable()
    {
        var a = Project.Load(TestPaths.Example).Build();
        var b = Project.Load(TestPaths.Example).Build();
        Assert.Equal(a.HashHex, b.HashHex);
        var c = CompiledProject.FromBytes(a.Data);
        Assert.Equal(a.HashHex, c.HashHex);
        Assert.Equal(a.Project.Tags.Select(t => t.Name), c.Project.Tags.Select(t => t.Name));
    }

    [Fact]
    public void IoValidation()
    {
        var p = BaseProject();
        foreach (var t in new[]
        {
            Io("A", DataType.BOOL, "Slot1.DI.0"),
            Io("B", DataType.BOOL, "Slot1.DI.0"),
            Io("C", DataType.BOOL, "Slot1.DI.8"),
            Io("D", DataType.BOOL, "Slot2.DI.0"),
            Io("E", DataType.INT, "Slot2.DO.1"),
            Io("F", DataType.BOOL, "Slot1.DI.2", TagAccess.ReadWrite),
            Io("G", DataType.BOOL, "Slot9.DI.0"),
            Io("H", DataType.BOOL, "Slot1-DI-0"),
        }) p.Tags.Add(t);
        var errs = Errors(p);
        foreach (var name in "BCDEFGH") Assert.Contains($"tags.{name}:", errs);
        Assert.DoesNotContain("tags.A:", errs);
    }

    [Fact]
    public void TagRules()
    {
        var p = BaseProject();
        p.Tags.Add(new Tag { Name = "ok.name_1" });
        p.Tags.Add(new Tag { Name = "1bad" });
        p.Tags.Add(new Tag { Name = "OK.NAME_1" });
        p.Tags.Add(new Tag { Name = "Mom", DataType = DataType.BOOL, Momentary = true });
        p.Tags.Add(new Tag { Name = "Safe", DataType = DataType.BOOL, Access = TagAccess.ReadWrite, Safety = true });
        p.Tags.Add(new Tag { Name = "Init", DataType = DataType.SINT, Initial = 500L });
        p.Tags.Add(new Tag { Name = "Scaled", DataType = DataType.INT, Source = TagSource.Io, Address = "Slot3.AI.0", Scaling = new Scaling(0, 32000, 0, 100) });
        p.Tags.Add(new Tag { Name = "NoConn", Source = TagSource.Plc, Address = "X", Connection = "PLC9" });
        var errs = Errors(p);
        Assert.DoesNotContain("tags.ok.name_1:", errs);
        foreach (var n in new[] { "1bad", "OK.NAME_1", "Mom", "Safe", "Init", "Scaled", "NoConn" })
            Assert.Contains($"tags.{n}:", errs);
    }

    [Fact]
    public void SafetyTagCannotBeBoundToWriteWidget()
    {
        var p = BaseProject();
        p.Tags.Add(Io("EStop", DataType.BOOL, "Slot1.DI.0"));
        p.Tags[0].Safety = true;
        var s = new Screen { Name = "S" };
        s.Widgets.Add(new Widget(new JsonObject { ["id"] = "b", ["type"] = "button", ["x"] = 0, ["y"] = 0, ["w"] = 1, ["h"] = 1, ["tag"] = "EStop", ["action"] = "set" }));
        p.Screens.Add(s);
        Assert.Contains("safety tag", Errors(p));
    }

    [Fact]
    public void BuildThrowsWithAllErrors()
    {
        var p = BaseProject();
        p.Tags.Add(new Tag { Name = "1bad" });
        p.Tags.Add(Io("x", DataType.BOOL, "Slot5.DI.0"));
        var e = Assert.Throws<ProjectException>(() => p.Build());
        Assert.Equal(2, e.Issues.Count(i => i.Severity == Severity.Error));
    }

    [Fact]
    public void SaveLoadRoundTripPreservesEverything()
    {
        var p = Project.Load(TestPaths.Example);
        var dir = TestPaths.NewTempDir();
        var path = Path.Combine(dir, "x.whp");
        p.Save(path);
        var again = Project.Load(path);
        Assert.Equal(p.ToJson(), again.ToJson());
        Assert.Equal(p.Build().HashHex, again.Build().HashHex);
    }

    [Fact]
    public void WidgetKeepsUnknownProperties()
    {
        var w = new Widget(new JsonObject { ["type"] = "label", ["x"] = 1, ["y"] = 2, ["w"] = 3, ["h"] = 4, ["future_prop"] = "keep me" });
        w.X = 15;
        Assert.Equal("keep me", w.Props["future_prop"]!.GetValue<string>());
        Assert.Equal(15, w.X);
    }

    [Fact]
    public void BuildMatchesPythonByteForByte()
    {
        if (!TestPaths.PythonAvailable) return; // Python tools not installed on this machine
        var dir = TestPaths.NewTempDir();
        var output = Path.Combine(dir, "py.whc");
        var r = TestPaths.Python("-m", "whmi.tools.cli", "build", TestPaths.Example, "-o", output);
        Assert.True(r is { Code: 0 }, r?.Err);
        var python = File.ReadAllBytes(output);
        var csharp = Project.Load(TestPaths.Example).Build();
        Assert.Equal(System.Text.Encoding.UTF8.GetString(python), System.Text.Encoding.UTF8.GetString(csharp.Data));
        Assert.Equal(python, csharp.Data);
        // and each side accepts the other's output
        Assert.Equal(csharp.HashHex, CompiledProject.FromBytes(python).HashHex);
    }
}
