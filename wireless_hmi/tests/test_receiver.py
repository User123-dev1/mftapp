"""End-to-end receiver behaviour: tags, PLC bridge, local I/O and link-loss safety."""

import asyncio

import pytest

from whmi.common.codes import Quality, Status
from whmi.common.datatypes import DataType
from whmi.common.project import CompiledProject, Project
from whmi.link.client import LinkError
from whmi.receiver import service as service_mod
from whmi.receiver.plc import manager as manager_mod

from .conftest import EXAMPLE, eventually


def tid(compiled, name):
    return compiled.tag(name).tag_id


async def read1(client, compiled, name):
    [tv] = await client.read([tid(compiled, name)])
    return tv


async def write1(client, compiled, name, value):
    t = compiled.tag(name)
    [(_, status)] = await client.write([(t.tag_id, t.data_type, value)])
    return status


async def test_download_and_fetch_project(rig):
    compiled = Project.load(EXAMPLE).build()
    studio = await rig.connect("studio")
    msg = await studio.download_project(compiled.data)
    assert "DemoLine" in msg
    assert rig.receiver.project.hash == compiled.hash
    assert rig.config.project_file.read_bytes() == compiled.data

    panel = await rig.connect("panel1")
    assert panel.session.project_hash == compiled.hash
    assert await panel.fetch_project() == compiled.data


async def test_download_requires_engineer(rig):
    panel = await rig.connect("panel1")
    with pytest.raises(LinkError, match="NOT_PERMITTED"):
        await panel.download_project(Project.load(EXAMPLE).build().data)


async def test_download_rejects_invalid_project(rig):
    studio = await rig.connect("studio")
    with pytest.raises(LinkError, match="INVALID"):
        await studio.download_project(b'{"format": "whmi-compiled", "tags": [{"name": "1bad"}]}')
    assert rig.receiver.project is None


async def test_project_survives_restart(rig):
    compiled = await rig.load()
    await rig.restart()
    assert rig.receiver.project.hash == compiled.hash


async def test_plc_tags_read_subscribe_write(rig):
    compiled = await rig.load()
    c = await rig.connect("panel1")
    await eventually(lambda: rig.receiver.plcs["PLC1"].connected)
    await eventually(lambda: rig.receiver.tagdb.by_name("Conveyor.Speed_SP").quality is Quality.GOOD)
    tv = await read1(c, compiled, "Conveyor.Speed_SP")
    assert (tv.value, tv.quality) == (50.0, Quality.GOOD)

    updates = []
    c.on_data = updates.extend
    await c.subscribe([tid(compiled, "Line.Running")], 20)
    rig.plc.set("Line_Running", True)
    await eventually(lambda: any(u.tag_id == tid(compiled, "Line.Running") and u.value is True for u in updates))

    assert await write1(c, compiled, "Conveyor.Speed_SP", 75.5) is Status.OK
    assert rig.plc.get("Conveyor.Speed_SP") == 75.5
    assert await write1(c, compiled, "Conveyor.Speed_SP", 150) is Status.OUT_OF_RANGE
    assert await write1(c, compiled, "Line.Running", True) is Status.READ_ONLY


async def test_write_permissions(rig):
    compiled = await rig.load()
    viewer = await rig.connect("viewer")
    assert await write1(viewer, compiled, "Batch.Count", 1) is Status.NOT_PERMITTED
    panel = await rig.connect("panel1")
    assert await write1(panel, compiled, "Safety.EStop_OK", True) is Status.READ_ONLY
    assert await write1(panel, compiled, "_System.Uptime", 0) is Status.READ_ONLY
    [(_, status)] = await panel.write([(tid(compiled, "Batch.Count"), DataType.STRING, "nope")])
    assert status is Status.TYPE_MISMATCH
    [(_, status)] = await panel.write([(9999, DataType.DINT, 1)])
    assert status is Status.BAD_TAG


async def test_plain_write_cannot_latch_momentary(rig):
    compiled = await rig.load()
    c = await rig.connect("panel1")
    assert await write1(c, compiled, "Tank.Jog_Fwd", True) is Status.INVALID
    assert await write1(c, compiled, "Tank.Jog_Fwd", False) is Status.OK


async def test_momentary_expires_without_refresh(rig):
    compiled = await rig.load()
    c = await rig.connect("panel1")
    t = compiled.tag("Tank.Jog_Fwd")
    assert await c.momentary(t.tag_id, t.data_type, True, 200) is Status.OK
    out = rig.io  # output image reaches the simulated module on the next scan
    jog = next(a for a in rig.receiver._outputs if str(a) == "Slot2.DO.1")
    await eventually(lambda: out.outputs.get(jog) is True)
    await eventually(lambda: out.outputs.get(jog) is False, timeout=1.0)


async def test_momentary_released_on_link_loss(rig):
    compiled = await rig.load()
    await eventually(lambda: rig.receiver.plcs["PLC1"].connected)
    c = await rig.connect("panel1")
    t = compiled.tag("Line.Start_PB")
    assert await c.momentary(t.tag_id, t.data_type, True, 2000) is Status.OK
    assert rig.plc.get("HMI_Start_PB") is True
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    c._stream._writer.transport.abort()  # WiFi gone, no goodbye
    await eventually(lambda: rig.plc.get("HMI_Start_PB") is False, timeout=2)
    assert loop.time() - t0 < 1.0  # released on session loss, not by the 2 s hold


async def test_failsafe_when_last_writer_leaves(rig):
    compiled = await rig.load()
    p1 = await rig.connect("panel1")
    p2 = await rig.connect("panel2")
    viewer = await rig.connect("viewer")
    assert await write1(p1, compiled, "Tank.Pump_Run", True) is Status.OK
    assert await write1(p1, compiled, "Tank.Valve_Cmd", 40) is Status.OK
    db = rig.receiver.tagdb
    await p1.close()
    await asyncio.sleep(0.2)
    assert db.by_name("Tank.Pump_Run").value is True  # panel2 still connected
    await p2.close()
    await eventually(lambda: db.by_name("Tank.Pump_Run").value is False)
    assert db.by_name("Tank.Valve_Cmd").value == 0.0
    assert viewer.connected  # viewers don't hold the machine up


async def test_analog_scaling(rig):
    compiled = await rig.load()
    c = await rig.connect("panel1")
    rig.io.set_input("Slot3.AI.0", 16000)
    await eventually(lambda: rig.receiver.tagdb.by_name("Tank.Level").value == 50.0)
    assert await write1(c, compiled, "Tank.Valve_Cmd", 25) is Status.OK
    valve = next(a for a in rig.receiver._outputs if str(a) == "Slot4.AO.0")
    await eventually(lambda: rig.io.outputs.get(valve) == 8000.0)


async def test_io_module_fault_marks_bad_device(rig):
    await rig.load()
    db = rig.receiver.tagdb
    rig.io.set_fault(1)
    await eventually(lambda: db.by_name("Safety.Door_Closed").quality is Quality.BAD_DEVICE)
    rig.io.set_fault(1, False)
    await eventually(lambda: db.by_name("Safety.Door_Closed").quality is Quality.GOOD)


async def test_plc_comm_loss_and_recovery(rig, monkeypatch):
    monkeypatch.setattr(manager_mod, "RECONNECT_MIN", 0.05)
    await rig.load()
    db = rig.receiver.tagdb
    await eventually(lambda: db.by_name("_System.PLC.PLC1.Connected").value is True)
    rig.plc.online = False
    await eventually(lambda: db.by_name("Conveyor.Speed").quality is Quality.BAD_COMM)
    assert db.by_name("_System.PLC.PLC1.Connected").value is False
    assert db.by_name("Tank.Level").quality is Quality.GOOD  # local I/O unaffected
    rig.plc.online = True
    await eventually(lambda: db.by_name("Conveyor.Speed").quality is Quality.GOOD)


async def test_missing_plc_tag_is_bad_config(rig):
    project = Project.load(EXAMPLE)
    project.tag("Line.Running").address = "Does_Not_Exist"
    await rig.load(project)
    db = rig.receiver.tagdb
    await eventually(lambda: db.by_name("Line.Running").quality is Quality.BAD_CONFIG)
    await eventually(lambda: db.by_name("Conveyor.Speed").quality is Quality.GOOD)


async def test_deadband_suppresses_small_changes(rig):
    compiled = await rig.load()
    c = await rig.connect("panel1")
    await eventually(lambda: rig.receiver.tagdb.by_name("Conveyor.Speed").quality is Quality.GOOD)
    updates = []
    c.on_data = lambda vs: updates.extend(v.value for v in vs)
    await c.subscribe([tid(compiled, "Conveyor.Speed")], 20)
    await eventually(lambda: updates == [0.0])
    rig.plc.set("Conveyor.Speed_Act", 0.05)
    await asyncio.sleep(0.4)
    assert updates == [0.0]
    rig.plc.set("Conveyor.Speed_Act", 0.5)
    await eventually(lambda: updates[-1] == 0.5)


async def test_retentive_memory(rig, monkeypatch):
    monkeypatch.setattr(service_mod, "RETENTIVE_SAVE_PERIOD", 0.05)
    compiled = await rig.load()
    c = await rig.connect("panel1")
    assert await write1(c, compiled, "Batch.Count", 42) is Status.OK
    assert await write1(c, compiled, "Batch.Operator_Note", "shift B") is Status.OK
    await eventually(lambda: rig.config.retentive_file.exists())
    await rig.restart()
    db = rig.receiver.tagdb
    assert db.by_name("Batch.Count").value == 42
    assert db.by_name("Batch.Operator_Note").value == ""  # not retentive


async def test_system_tags(rig):
    await rig.load()
    db = rig.receiver.tagdb
    await rig.connect("viewer")
    await eventually(lambda: db.by_name("_System.Link.Clients").value == 1)
    assert db.by_name("_System.Link.Connected").value is False
    await rig.connect("panel1")
    await eventually(lambda: db.by_name("_System.Link.Connected").value is True)


async def test_project_change_notifies_clients(rig):
    await rig.load()
    c = await rig.connect("panel1")
    changed = asyncio.Event()
    c.on_project_changed = lambda h: changed.set()
    project = Project.load(EXAMPLE)
    project.description = "v2"
    new = await rig.load(project)
    await asyncio.wait_for(changed.wait(), 2)
    assert CompiledProject.from_bytes(await c.fetch_project()).hash == new.hash


async def test_plc_heartbeat_only_while_operator_connected(rig):
    rig.plc.set("HMI_Heartbeat", 0)
    project = Project.load(EXAMPLE)
    project.connection("PLC1").heartbeat_ms = 100
    await rig.load(project)
    await eventually(lambda: rig.receiver.plcs["PLC1"].connected)
    await asyncio.sleep(0.3)
    assert rig.plc.get("HMI_Heartbeat") == 0  # nobody connected: PLC sees a frozen counter
    c = await rig.connect("panel1")
    await eventually(lambda: rig.plc.get("HMI_Heartbeat") >= 3)
    await c.close()
    await asyncio.sleep(0.25)
    frozen = rig.plc.get("HMI_Heartbeat")
    await asyncio.sleep(0.4)
    assert rig.plc.get("HMI_Heartbeat") == frozen
