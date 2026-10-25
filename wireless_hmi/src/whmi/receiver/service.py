"""The WHMI Receiver: tag database, I/O scan, PLC bridge and link-loss safety."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.codes import Quality, Status
from ..common.datatypes import CoercionError, DataType, coerce
from ..common.project import (
    IO_CATALOG,
    MOMENTARY_MAX_HOLD_MS,
    SOURCE_IO,
    SOURCE_MEMORY,
    SOURCE_PLC,
    SOURCE_SYSTEM,
    CompiledProject,
    IoAddress,
    ProjectError,
    Tag,
)
from ..link.credentials import CredentialStore
from ..link.server import LinkServer, Session
from .config import DeviceConfig
from .io.base import IoDriver
from .io.simulated import SimulatedIo
from .link_service import LinkService
from .plc.base import PlcDriver
from .plc.manager import PlcManager, make_driver
from .tagdb import TagDB

log = logging.getLogger(__name__)

SUPERVISOR_PERIOD = 0.02
RETENTIVE_SAVE_PERIOD = 1.0


def make_io_driver(name: str) -> IoDriver:
    if name == "simulated":
        return SimulatedIo()
    if name == "simulated-loopback":
        return SimulatedIo(loopback=True)
    raise ValueError(f"unknown I/O driver {name!r}")


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@dataclass
class Hold:
    """An active momentary write."""

    session_id: int
    expires: float  # monotonic


class Receiver:
    def __init__(
        self,
        config: DeviceConfig,
        credentials: CredentialStore,
        *,
        io_driver: IoDriver | None = None,
        plc_drivers: dict[str, PlcDriver] | None = None,
    ) -> None:
        self.config = config
        self.credentials = credentials
        self.io = io_driver or make_io_driver(config.io_driver)
        self._plc_drivers = plc_drivers or {}
        self.project: CompiledProject | None = None
        self.tagdb: TagDB | None = None
        self.plcs: dict[str, PlcManager] = {}
        self.link = LinkService(self)
        self.server = LinkServer(
            self.link, credentials, config.receiver_id,
            host=config.listen_host, port=config.listen_port, max_sessions=config.max_sessions,
        )
        self._outputs: dict[IoAddress, float | bool] = {}
        self._io_inputs: list[tuple[Tag, IoAddress]] = []
        self._io_outputs: list[tuple[Tag, IoAddress]] = []
        self._system: dict[str, int] = {}
        self._holds: dict[int, Hold] = {}
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._started = time.monotonic()
        self._retentive_dirty = False
        self.last_scan_ms = 0.0

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.config.data_path.mkdir(parents=True, exist_ok=True)
        if self.config.project_file.exists():
            try:
                compiled = CompiledProject.load(self.config.project_file)
                await self.activate(compiled, persist=False)
            except (ProjectError, ValueError, OSError) as e:
                log.error("stored project could not be loaded, running without a project: %s", e)
        else:
            log.warning("no project downloaded yet")
        await self.server.start()

    async def stop(self) -> None:
        await self.server.stop()
        async with self._lock:
            await self._stop_runtime()
        self.io.close()

    async def activate(self, compiled: CompiledProject, *, persist: bool = True) -> None:
        """Make ``compiled`` the running project (atomically replaces the old one)."""
        async with self._lock:
            await self._stop_runtime()
            if persist:
                self.config.data_path.mkdir(parents=True, exist_ok=True)
                _atomic_write(self.config.project_file, compiled.data)
            self.project = compiled
            self.tagdb = TagDB(compiled)
            self._load_retentive()
            rx = compiled.project.receiver
            self.server.heartbeat_ms = rx.heartbeat_ms
            self.server.watchdog_ms = rx.watchdog_ms
            self._setup_io(compiled)
            self._setup_system_tags(compiled)
            self._setup_plcs(compiled)
            self.tagdb.add_listener(self.link.on_tag_change)
            self._tasks = [
                asyncio.create_task(self._scan_loop(), name="io-scan"),
                asyncio.create_task(self._supervisor_loop(), name="supervisor"),
            ]
            log.info("project %r activated (%s, %d tags)", compiled.name, compiled.hash_hex[:12],
                     len(compiled.project.tags))
        await self.link.project_changed(compiled)

    async def _stop_runtime(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        for m in self.plcs.values():
            await m.stop()
        self.plcs = {}
        if self._retentive_dirty:
            self._save_retentive()
        self._holds.clear()
        if self.project is not None:
            # Program mode while switching projects: all outputs off.
            self._outputs = {}
            try:
                self.io.exchange({})
            except Exception:
                log.exception("failed to clear outputs")

    def _setup_io(self, compiled: CompiledProject) -> None:
        rx = compiled.project.receiver
        self.io.open({slot: IO_CATALOG[cat] for slot, cat in rx.slots.items()})
        self._outputs = {}
        self._io_inputs, self._io_outputs = [], []
        for tag in compiled.project.tags:
            if tag.source != SOURCE_IO:
                continue
            addr = IoAddress.parse(tag.address)
            if addr.kind in ("DO", "AO"):
                self._io_outputs.append((tag, addr))
                value = tag.default_value()
                self._outputs[addr] = self._to_raw(tag, addr, value)
                self.tagdb.update(tag.tag_id, value, Quality.GOOD)
            else:
                self._io_inputs.append((tag, addr))

    def _setup_system_tags(self, compiled: CompiledProject) -> None:
        self._system = {t.address: t.tag_id for t in compiled.project.tags if t.source == SOURCE_SYSTEM}

    def _setup_plcs(self, compiled: CompiledProject) -> None:
        by_conn: dict[str, list[Tag]] = {}
        for tag in compiled.project.tags:
            if tag.source == SOURCE_PLC:
                by_conn.setdefault(tag.connection, []).append(tag)
        for conn in compiled.project.plc_connections:
            driver = self._plc_drivers.get(conn.name) or make_driver(conn)
            mgr = PlcManager(conn, driver, self.tagdb, by_conn.get(conn.name, []), on_state=self._on_plc_state,
                             hmi_present=self._writer_connected)
            self.plcs[conn.name] = mgr
            mgr.start()

    def _writer_connected(self) -> bool:
        return any(s.role.can_write for s in self.server.sessions)

    def _on_plc_state(self, name: str, up: bool) -> None:
        self._set_system(f"plc.{name}.connected", up)

    def _set_system(self, key: str, value: Any) -> None:
        tid = self._system.get(key)
        if tid is not None and self.tagdb is not None:
            self.tagdb.update(tid, value, Quality.GOOD)

    # ------------------------------------------------------------------ I/O

    def _to_raw(self, tag: Tag, addr: IoAddress, value: Any) -> float | bool:
        """Engineering value -> raw output value, clamped to the module's range."""
        if addr.kind == "DO":
            return bool(value)
        assert self.project is not None
        mod = IO_CATALOG[self.project.project.receiver.slots[addr.slot]]
        raw = tag.scaling.to_raw(value) if tag.scaling else value
        return float(min(max(round(raw), mod.raw_min), mod.raw_max))

    @staticmethod
    def _from_raw(tag: Tag, raw: float | bool) -> Any:
        if tag.data_type is DataType.BOOL:
            return bool(raw)
        value = tag.scaling.to_eng(raw) if tag.scaling else raw
        if tag.data_type.is_integer:
            value = round(value)
        return coerce(tag.data_type, value)

    async def _scan_loop(self) -> None:
        assert self.project is not None and self.tagdb is not None
        loop = asyncio.get_running_loop()
        period = self.project.project.receiver.scan_ms / 1000
        next_scan = loop.time()
        last_save = loop.time()
        db = self.tagdb
        while True:
            t0 = time.perf_counter()
            try:
                inputs = self.io.exchange(dict(self._outputs))
            except Exception:
                log.exception("I/O exchange failed")
                inputs = {}
            for tag, addr in self._io_inputs:
                point = inputs.get(addr)
                if point is None or not point.ok:
                    db.update(tag.tag_id, None, Quality.BAD_DEVICE)
                    continue
                try:
                    db.update(tag.tag_id, self._from_raw(tag, point.raw), Quality.GOOD)
                except CoercionError:
                    db.update(tag.tag_id, None, Quality.BAD_CONFIG)
            for tag, addr in self._io_outputs:
                db.update(tag.tag_id, None, Quality.GOOD if self.io.module_ok(addr.slot) else Quality.BAD_DEVICE)
            self.last_scan_ms = (time.perf_counter() - t0) * 1000
            self._update_system_tags()
            now = loop.time()
            if self._retentive_dirty and now - last_save >= RETENTIVE_SAVE_PERIOD:
                last_save = now
                await asyncio.to_thread(self._save_retentive)
            next_scan += period
            now = loop.time()
            if next_scan < now:
                next_scan = now
            await asyncio.sleep(next_scan - now)

    def _update_system_tags(self) -> None:
        sessions = self.server.sessions
        self._set_system("link.clients", min(len(sessions), 0xFFFF))
        self._set_system("link.connected", self._writer_connected())
        self._set_system("uptime", int(time.monotonic() - self._started))
        self._set_system("scan_time", round(self.last_scan_ms, 3))

    # ------------------------------------------------------------------ writes

    async def write_tag(self, session: Session | None, tag_id: int, value: Any, *,
                        hold_ms: int | None = None) -> Status:
        """Validate and perform a write from a client. ``hold_ms`` makes it a momentary write."""
        if self.project is None or self.tagdb is None:
            return Status.BAD_TAG
        tag = self.project.tag(tag_id)
        if tag is None:
            return Status.BAD_TAG
        if session is not None and not session.role.can_write:
            return Status.NOT_PERMITTED
        if tag.source == SOURCE_SYSTEM or not tag.writable:
            return Status.READ_ONLY
        try:
            v = coerce(tag.data_type, value)
        except CoercionError as e:
            return Status.OUT_OF_RANGE if e.out_of_range else Status.TYPE_MISMATCH
        if tag.data_type.is_numeric:
            if (tag.min is not None and v < tag.min) or (tag.max is not None and v > tag.max):
                return Status.OUT_OF_RANGE

        if tag.momentary:
            release = tag.effective_release_value()
            if hold_ms is None and v != release:
                # A plain write could latch a momentary output on: only the release value is allowed.
                return Status.INVALID
            if hold_ms is None or hold_ms == 0 or v == release:
                self._holds.pop(tag.tag_id, None)
                return await self._write_value(tag, release)
            hold_ms = min(hold_ms, MOMENTARY_MAX_HOLD_MS)
            status = await self._write_value(tag, v)
            if status is Status.OK:
                self._holds[tag.tag_id] = Hold(session.id if session else 0, time.monotonic() + hold_ms / 1000)
            return status
        if hold_ms is not None:
            return Status.INVALID  # MOMENTARY on a non-momentary tag
        return await self._write_value(tag, v)

    async def _write_value(self, tag: Tag, value: Any) -> Status:
        assert self.tagdb is not None
        if tag.source == SOURCE_MEMORY:
            self.tagdb.update(tag.tag_id, value, Quality.GOOD)
            if tag.retentive:
                self._retentive_dirty = True
            return Status.OK
        if tag.source == SOURCE_IO:
            addr = IoAddress.parse(tag.address)
            if not self.io.module_ok(addr.slot):
                return Status.COMM_FAILURE
            self._outputs[addr] = self._to_raw(tag, addr, value)
            self.tagdb.update(tag.tag_id, value, Quality.GOOD)
            return Status.OK
        if tag.source == SOURCE_PLC:
            mgr = self.plcs.get(tag.connection)
            if mgr is None:
                return Status.COMM_FAILURE
            return await mgr.write(tag, value)
        return Status.READ_ONLY

    async def _release(self, tag: Tag) -> None:
        status = await self._write_value(tag, tag.effective_release_value())
        if status is not Status.OK:
            log.error("failed to release momentary tag %s: %s", tag.name, status.name)

    async def _supervisor_loop(self) -> None:
        """Expire momentary holds."""
        while True:
            await asyncio.sleep(SUPERVISOR_PERIOD)
            now = time.monotonic()
            expired = [tid for tid, h in self._holds.items() if h.expires <= now]
            for tid in expired:
                del self._holds[tid]
                tag = self.project.tag(tid) if self.project else None
                if tag is not None:
                    log.info("momentary %s released (hold expired)", tag.name)
                    await self._release(tag)

    async def on_session_closed(self, session: Session) -> None:
        """Link-loss safety actions (PROTOCOL.md §6)."""
        if self.project is None:
            return
        owned = [tid for tid, h in self._holds.items() if h.session_id == session.id]
        for tid in owned:
            del self._holds[tid]
            tag = self.project.tag(tid)
            if tag is not None:
                log.warning("momentary %s released: session %s closed", tag.name, session.client_id)
                await self._release(tag)
        if not session.role.can_write:
            return
        writers_left = any(s.role.can_write for s in self.server.sessions if s is not session)
        for tag in self.project.project.tags:
            if tag.failsafe_value is None or not tag.writable:
                continue
            if tag.failsafe_on_any_loss or not writers_left:
                log.warning("fail-safe: %s -> %r (session %s lost)", tag.name, tag.failsafe_value,
                            session.client_id)
                status = await self._write_value(tag, coerce(tag.data_type, tag.failsafe_value))
                if status is not Status.OK:
                    log.error("fail-safe write to %s failed: %s", tag.name, status.name)

    # ------------------------------------------------------------------ retentive memory

    def _load_retentive(self) -> None:
        assert self.project is not None and self.tagdb is not None
        path = self.config.retentive_file
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.error("retentive data unreadable: %s", e)
            return
        for name, value in data.items():
            tag = self.project.tag(name)
            if tag is None or tag.source != SOURCE_MEMORY or not tag.retentive:
                continue
            try:
                self.tagdb.update(tag.tag_id, coerce(tag.data_type, value), Quality.GOOD)
            except CoercionError:
                log.warning("retentive value for %s no longer fits %s", name, tag.data_type.name)

    def _save_retentive(self) -> None:
        if self.project is None or self.tagdb is None:
            return
        self._retentive_dirty = False
        data = {t.name: self.tagdb.get(t.tag_id).value for t in self.project.project.tags
                if t.source == SOURCE_MEMORY and t.retentive}
        try:
            _atomic_write(self.config.retentive_file, json.dumps(data).encode("utf-8"))
        except OSError as e:
            self._retentive_dirty = True
            log.error("failed to save retentive data: %s", e)
