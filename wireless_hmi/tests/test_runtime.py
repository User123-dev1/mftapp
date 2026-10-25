"""HMI panel runtime engine against a live receiver."""

import asyncio

import pytest

from whmi.common.codes import Quality, Status
from whmi.common.project import Project
from whmi.runtime import engine as engine_mod
from whmi.runtime.engine import HmiRuntime, LinkState

from .conftest import EXAMPLE, eventually


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(engine_mod, "RECONNECT_MIN", 0.05)
    monkeypatch.setattr(engine_mod, "RECONNECT_MAX", 0.2)


@pytest.fixture
async def panel(rig, tmp_path):
    rt = HmiRuntime("127.0.0.1", "panel1", rig.keys["panel1"], port=rig.port, cache_dir=tmp_path / "panel",
                    interval_ms=20)
    rt.start()
    yield rt
    await rt.stop()


async def test_syncs_project_and_tracks_values(rig, panel):
    compiled = await rig.load()
    await panel.wait_online(3)
    assert panel.project.hash == compiled.hash
    assert (rig.tmp_path / "panel" / "project.whc").exists()
    rig.io.set_input("Slot1.DI.1", True)
    await eventually(lambda: panel.get("Safety.Door_Closed").value is True)
    assert panel.get("Safety.Door_Closed").good


async def test_waits_for_project(rig, panel):
    await eventually(lambda: panel.state is LinkState.NO_PROJECT)
    await rig.load()
    await panel.wait_online(3)


async def test_write_and_momentary(rig, panel):
    await rig.load()
    await panel.wait_online(3)
    assert await panel.write("Batch.Count", 7) is Status.OK
    await eventually(lambda: panel.value("Batch.Count") == 7)

    assert await panel.press("Tank.Jog_Fwd") is Status.OK
    await asyncio.sleep(1.2)  # well past one hold period: the runtime keeps re-asserting
    assert rig.receiver.tagdb.by_name("Tank.Jog_Fwd").value is True
    assert await panel.release("Tank.Jog_Fwd") is Status.OK
    await eventually(lambda: rig.receiver.tagdb.by_name("Tank.Jog_Fwd").value is False)
    assert await panel.press("Batch.Count") is Status.INVALID  # not a momentary tag


async def test_link_loss_marks_values_and_reconnects(rig, panel):
    await rig.load()
    await panel.wait_online(3)
    await eventually(lambda: panel.get("Tank.Level").good)
    for s in list(rig.receiver.server.sessions):
        s.stream._writer.transport.abort()
    await eventually(lambda: panel.get("Tank.Level").quality is Quality.BAD_NOT_CONNECTED)
    await eventually(lambda: panel.state is LinkState.ONLINE)
    await eventually(lambda: panel.get("Tank.Level").good)


async def test_resyncs_when_project_changes(rig, panel):
    await rig.load()
    await panel.wait_online(3)
    project = Project.load(EXAMPLE)
    project.description = "v2"
    new = await rig.load(project)
    await eventually(lambda: panel.project.hash == new.hash and panel.state is LinkState.ONLINE)


async def test_uses_cached_project_offline(rig, tmp_path):
    compiled = await rig.load()
    rt = HmiRuntime("127.0.0.1", "panel1", rig.keys["panel1"], port=rig.port, cache_dir=tmp_path / "c")
    rt.start()
    await rt.wait_online(3)
    await rt.stop()
    offline = HmiRuntime("127.0.0.1", "panel1", rig.keys["panel1"], port=1, cache_dir=tmp_path / "c")
    assert offline.project.hash == compiled.hash
    assert offline.get("Tank.Level").quality is Quality.BAD_NOT_CONNECTED


async def test_bad_credentials_state(rig, tmp_path):
    rt = HmiRuntime("127.0.0.1", "panel1", bytes(32), port=rig.port)
    rt.start()
    try:
        await eventually(lambda: rt.state is LinkState.AUTH_FAILED, timeout=4)
    finally:
        await rt.stop()
