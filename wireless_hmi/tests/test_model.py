import json
from pathlib import Path

import pytest

from whmi.common.datatypes import CoercionError, DataType, Scaling, coerce
from whmi.common.project import CompiledProject, Project, ProjectError, Tag

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo_line.whp"


# ---------------------------------------------------------------- data types

@pytest.mark.parametrize("dtype,value,expected", [
    (DataType.BOOL, 1, True), (DataType.BOOL, "off", False), (DataType.SINT, -128, -128),
    (DataType.INT, 32767, 32767), (DataType.DINT, 5.0, 5), (DataType.UDINT, "0x10", 16),
    (DataType.REAL, 1, 1.0), (DataType.LREAL, "2.5", 2.5), (DataType.STRING, "abc", "abc"),
])
def test_coerce_ok(dtype, value, expected):
    assert coerce(dtype, value) == expected


@pytest.mark.parametrize("dtype,value,out_of_range", [
    (DataType.SINT, 128, True), (DataType.USINT, -1, True), (DataType.INT, 2.5, False),
    (DataType.BOOL, 2, False), (DataType.STRING, "x" * 83, True), (DataType.DINT, "abc", False),
    (DataType.REAL, 1e39, True), (DataType.STRING, 5, False),
])
def test_coerce_rejects_lossy(dtype, value, out_of_range):
    with pytest.raises(CoercionError) as e:
        coerce(dtype, value)
    assert e.value.out_of_range is out_of_range


def test_real_is_rounded_to_single_precision():
    assert coerce(DataType.REAL, 0.1) != 0.1
    assert abs(coerce(DataType.REAL, 0.1) - 0.1) < 1e-7


def test_scaling_roundtrip_and_clamp():
    s = Scaling(0, 32000, 0, 100)
    assert s.to_eng(16000) == 50
    assert s.to_raw(25) == 8000
    assert Scaling(0, 32000, 0, 100, clamp=True).to_eng(40000) == 100


# ---------------------------------------------------------------- project

def base_project() -> Project:
    p = Project(name="T")
    p.receiver.slots = {1: "DI8", 2: "DO8", 3: "AI4"}
    return p


def errors(p: Project) -> list[str]:
    return [f"{i.path}: {i.message}" for i in p.validate() if i.severity == "error"]


def test_example_project_builds():
    compiled = Project.load(EXAMPLE).build()
    assert compiled.tag("Line.Start_PB").tag_id == 1
    assert compiled.tag("_System.Link.Clients") is not None
    assert compiled.tag("tank.level") is compiled.tag("Tank.Level")  # case-insensitive lookup


def test_build_is_deterministic_and_reloadable():
    a = Project.load(EXAMPLE).build()
    b = Project.load(EXAMPLE).build()
    assert a.hash == b.hash
    c = CompiledProject.from_bytes(a.data)
    assert c.hash == a.hash
    assert [t.name for t in c.project.tags] == [t.name for t in a.project.tags]


def test_io_validation():
    p = base_project()
    p.tags = [
        Tag("A", DataType.BOOL, "io", "Slot1.DI.0"),
        Tag("B", DataType.BOOL, "io", "Slot1.DI.0"),  # duplicate point
        Tag("C", DataType.BOOL, "io", "Slot1.DI.8"),  # channel out of range
        Tag("D", DataType.BOOL, "io", "Slot2.DI.0"),  # wrong module kind
        Tag("E", DataType.INT, "io", "Slot2.DO.1"),  # DO must be BOOL
        Tag("F", DataType.BOOL, "io", "Slot1.DI.2", access="read_write"),  # input can't be written
        Tag("G", DataType.BOOL, "io", "Slot9.DI.0"),  # empty slot
        Tag("H", DataType.BOOL, "io", "Slot1-DI-0"),  # bad syntax
    ]
    errs = "\n".join(errors(p))
    for name in "BCDEFGH":
        assert f"tags.{name}:" in errs
    assert "tags.A:" not in errs


def test_tag_rules():
    p = base_project()
    p.tags = [
        Tag("ok.name_1", DataType.DINT),
        Tag("1bad", DataType.DINT),
        Tag("OK.NAME_1", DataType.DINT),  # duplicate, case-insensitive
        Tag("Mom", DataType.BOOL, momentary=True),  # momentary must be read_write
        Tag("Safe", DataType.BOOL, access="read_write", safety=True),
        Tag("Init", DataType.SINT, initial=500),
        Tag("Scaled", DataType.INT, "io", "Slot3.AI.0", scaling=Scaling(0, 32000, 0, 100)),
        Tag("NoConn", DataType.DINT, "plc", "X", connection="PLC9"),
    ]
    errs = "\n".join(errors(p))
    assert "tags.ok.name_1:" not in errs
    for name in ("1bad", "OK.NAME_1", "Mom", "Safe", "Init", "Scaled", "NoConn"):
        assert f"tags.{name}:" in errs, name


def test_safety_tag_cannot_be_bound_to_write_widget():
    p = base_project()
    p.tags = [Tag("EStop", DataType.BOOL, "io", "Slot1.DI.0", safety=True)]
    p.screens = [p_screen([{"id": "b", "type": "button", "x": 0, "y": 0, "w": 1, "h": 1, "tag": "EStop",
                            "action": "set"}])]
    errs = "\n".join(errors(p))
    assert "safety tag" in errs


def test_momentary_button_needs_momentary_tag():
    p = base_project()
    p.tags = [Tag("Run", DataType.BOOL, access="read_write")]
    p.screens = [p_screen([{"id": "b", "type": "button", "x": 0, "y": 0, "w": 1, "h": 1, "tag": "Run"}])]
    assert any("momentary" in e for e in errors(p))


def p_screen(widgets):
    from whmi.common.project import Screen

    return Screen("S", widgets=widgets)


def test_build_raises_with_all_issues():
    p = base_project()
    p.tags = [Tag("1bad", DataType.DINT), Tag("x", DataType.BOOL, "io", "Slot5.DI.0")]
    with pytest.raises(ProjectError) as e:
        p.build()
    assert len([i for i in e.value.issues if i.severity == "error"]) == 2


def test_from_bytes_rejects_tampered_or_invalid():
    compiled = Project.load(EXAMPLE).build()
    doc = json.loads(compiled.data)
    doc["tags"][1]["tag_id"] = doc["tags"][0]["tag_id"]
    with pytest.raises(ValueError):
        CompiledProject.from_bytes(json.dumps(doc).encode())
    with pytest.raises(ValueError):
        CompiledProject.from_bytes(EXAMPLE.read_bytes())  # source project, not compiled


def test_save_load_roundtrip(tmp_path):
    p = Project.load(EXAMPLE)
    p.save(tmp_path / "x.whp")
    assert Project.load(tmp_path / "x.whp").to_dict() == p.to_dict()
