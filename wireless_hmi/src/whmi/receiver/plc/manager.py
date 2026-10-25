"""Runs one PLC connection: polling, writes, reconnect and tag quality."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any, Callable

from ...common.codes import Quality, Status
from ...common.datatypes import CoercionError, coerce
from ...common.project import PlcConnection, Tag
from ..tagdb import TagDB
from .base import PlcCommError, PlcDriver

log = logging.getLogger(__name__)

RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0
HEARTBEAT_MAX = 2**31 - 1


def make_driver(conn: PlcConnection, simulators: dict[str, PlcDriver] | None = None) -> PlcDriver:
    if conn.driver == "logix":
        from .logix import LogixPlc

        return LogixPlc(conn.host, conn.path, timeout_ms=conn.timeout_ms)
    if conn.driver == "simulator":
        if simulators and conn.name in simulators:
            return simulators[conn.name]
        from .simulator import SimulatedPlc

        return SimulatedPlc(auto_create=True)
    raise ValueError(f"unknown PLC driver {conn.driver!r}")


class PlcManager:
    def __init__(self, conn: PlcConnection, driver: PlcDriver, tagdb: TagDB, tags: list[Tag],
                 on_state: Callable[[str, bool], None] | None = None,
                 hmi_present: Callable[[], bool] | None = None) -> None:
        self.conn = conn
        self.hmi_present = hmi_present or (lambda: False)
        self.heartbeat = 0
        self.driver = driver
        self.tagdb = tagdb
        self.tags = tags
        self.on_state = on_state
        self.connected = False
        self.last_error = ""
        self._addresses = sorted({t.address for t in tags})
        self._by_address: dict[str, list[Tag]] = {}
        for t in tags:
            self._by_address.setdefault(t.address, []).append(t)
        # One worker thread per PLC: the driver is never used concurrently.
        self._executor = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix=f"plc-{conn.name}")
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def _call(self, fn: Callable, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"plc-{self.conn.name}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        try:
            await self._call(self.driver.close)
        except Exception:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _set_connected(self, up: bool, error: str = "") -> None:
        self.last_error = error
        if up == self.connected:
            return
        self.connected = up
        if up:
            log.info("PLC %s connected", self.conn.name)
        else:
            log.warning("PLC %s disconnected: %s", self.conn.name, error)
            self.tagdb.set_quality([t.tag_id for t in self.tags], Quality.BAD_COMM)
        if self.on_state:
            self.on_state(self.conn.name, up)

    async def _run(self) -> None:
        delay = RECONNECT_MIN
        self.tagdb.set_quality([t.tag_id for t in self.tags], Quality.BAD_COMM)
        while not self._stopping:
            try:
                await self._call(self.driver.connect)
                self._set_connected(True)
                delay = RECONNECT_MIN
                await self._poll_loop()
            except PlcCommError as e:
                self._set_connected(False, str(e))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # driver bug: don't kill the receiver
                log.exception("PLC %s driver failure", self.conn.name)
                self._set_connected(False, repr(e))
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    async def _poll_loop(self) -> None:
        loop = asyncio.get_running_loop()
        period = self.conn.poll_ms / 1000
        next_poll = loop.time()
        next_heartbeat = loop.time()
        while not self._stopping:
            if self.conn.heartbeat_tag and loop.time() >= next_heartbeat:
                next_heartbeat = loop.time() + self.conn.heartbeat_ms / 1000
                if self.hmi_present():
                    await self._send_heartbeat()
            if self._addresses:
                results = await self._call(self.driver.read, self._addresses)
                for addr, res in results.items():
                    for tag in self._by_address.get(addr, ()):
                        self._apply(tag, res.value, res.error)
            next_poll += period
            now = loop.time()
            if next_poll < now:  # overrun: don't try to catch up
                next_poll = now
            await asyncio.sleep(next_poll - now)

    async def _send_heartbeat(self) -> None:
        self.heartbeat = self.heartbeat + 1 if self.heartbeat < HEARTBEAT_MAX else 0
        result = await self._call(self.driver.write, [(self.conn.heartbeat_tag, self.heartbeat)])
        error = result.get(self.conn.heartbeat_tag)
        if error and self.last_error != error:
            self.last_error = error
            log.error("PLC %s heartbeat tag %s: %s", self.conn.name, self.conn.heartbeat_tag, error)

    def _apply(self, tag: Tag, raw: Any, error: str | None) -> None:
        if error is not None:
            if self.tagdb.get(tag.tag_id).quality is not Quality.BAD_CONFIG:
                log.warning("PLC %s tag %s (%s): %s", self.conn.name, tag.name, tag.address, error)
            self.tagdb.update(tag.tag_id, None, Quality.BAD_CONFIG)
            return
        try:
            value = tag.scaling.to_eng(raw) if tag.scaling else raw
            value = coerce(tag.data_type, value)
        except (CoercionError, TypeError) as e:
            if self.tagdb.get(tag.tag_id).quality is not Quality.BAD_CONFIG:
                log.warning("PLC %s tag %s: value %r does not fit %s: %s", self.conn.name, tag.name, raw,
                            tag.data_type.name, e)
            self.tagdb.update(tag.tag_id, None, Quality.BAD_CONFIG)
            return
        self.tagdb.update(tag.tag_id, value, Quality.GOOD)

    async def write(self, tag: Tag, value: Any) -> Status:
        """Write an engineering value (already coerced) to the PLC."""
        if not self.connected:
            return Status.COMM_FAILURE
        raw = tag.scaling.to_raw(value) if tag.scaling else value
        try:
            result = await self._call(self.driver.write, [(tag.address, raw)])
        except PlcCommError as e:
            self._set_connected(False, str(e))
            return Status.COMM_FAILURE
        except ValueError:
            return Status.OUT_OF_RANGE
        error = result.get(tag.address)
        if error:
            log.warning("PLC %s write %s=%r failed: %s", self.conn.name, tag.address, raw, error)
            return Status.BAD_TAG if "exist" in error.lower() else Status.COMM_FAILURE
        # Reflect the write immediately; the next poll confirms it.
        self.tagdb.update(tag.tag_id, value, Quality.GOOD)
        return Status.OK
