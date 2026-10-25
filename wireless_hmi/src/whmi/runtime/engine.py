"""HMI runtime engine for a WHMI Panel.

Owns the link to the receiver and keeps an up-to-date, quality-aware tag
cache that the UI layer renders. It handles:

* connect / authenticate / reconnect with backoff
* project sync: pulls the compiled project when the receiver's hash differs
  from the cached one, and re-syncs on PROJECT_CHANGED
* subscriptions and the tag cache (everything turns BAD_NOT_CONNECTED on link loss)
* writes and momentary push-buttons (re-asserted while held)
"""

from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..common.codes import ClientKind, Quality, Status
from ..common.project import MOMENTARY_MAX_HOLD_MS, CompiledProject, ProjectError, Tag
from ..link.client import AuthenticationError, LinkClient, LinkError
from ..link.codec import ProtocolError
from ..link.frames import DEFAULT_PORT
from ..link.messages import TagValue

log = logging.getLogger(__name__)

RECONNECT_MIN = 0.5
RECONNECT_MAX = 5.0
AUTH_RETRY = 10.0


class LinkState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    SYNCING = "syncing"
    ONLINE = "online"
    AUTH_FAILED = "auth_failed"
    NO_PROJECT = "no_project"


@dataclass
class CachedValue:
    value: Any
    quality: Quality
    timestamp_ms: int

    @property
    def good(self) -> bool:
        return self.quality is Quality.GOOD


ValueListener = Callable[[Tag, CachedValue], None]


class HmiRuntime:
    def __init__(
        self,
        host: str,
        client_id: str,
        key: bytes,
        *,
        port: int = DEFAULT_PORT,
        cache_dir: str | Path | None = None,
        interval_ms: int = 100,
        kind: ClientKind = ClientKind.PANEL,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.key = key
        self.kind = kind
        self.interval_ms = interval_ms
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.project: CompiledProject | None = None
        self.values: dict[int, CachedValue] = {}
        self.state = LinkState.DISCONNECTED
        self.last_error = ""
        self.on_value: list[ValueListener] = []
        self.on_state: list[Callable[[LinkState], None]] = []
        self.on_project: list[Callable[[CompiledProject], None]] = []
        self._client: LinkClient | None = None
        self._project_changed = asyncio.Event()
        self._online = asyncio.Event()
        self._holds: dict[int, asyncio.Task] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._load_cached_project()

    # ------------------------------------------------------------------ public API

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="hmi-runtime")

    async def stop(self) -> None:
        self._stopping = True
        for name in list(self._holds):
            self._holds.pop(name).cancel()
        if self._client:
            await self._client.close()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def wait_online(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._online.wait(), timeout)

    def tag(self, name: str) -> Tag:
        if self.project is None:
            raise KeyError("no project loaded")
        t = self.project.tag(name)
        if t is None:
            raise KeyError(f"unknown tag {name!r}")
        return t

    def get(self, name: str) -> CachedValue:
        t = self.tag(name)
        return self.values.get(t.tag_id) or CachedValue(t.data_type.zero(), Quality.BAD_NOT_CONNECTED, 0)

    def value(self, name: str) -> Any:
        return self.get(name).value

    async def write(self, name: str, value: Any) -> Status:
        t = self.tag(name)
        client = self._require_online()
        try:
            [(_, status)] = await client.write([(t.tag_id, t.data_type, value)])
        except LinkError:
            return Status.COMM_FAILURE
        return status

    async def press(self, name: str, value: Any = True) -> Status:
        """Assert a momentary tag; it is re-asserted until :meth:`release` (or link loss)."""
        t = self.tag(name)
        if not t.momentary:
            return Status.INVALID
        client = self._require_online()
        assert client.session is not None
        hb = client.session.heartbeat_ms
        hold_ms = min(MOMENTARY_MAX_HOLD_MS, max(3 * hb, 300))
        try:
            status = await client.momentary(t.tag_id, t.data_type, value, hold_ms)
        except LinkError:
            return Status.COMM_FAILURE
        if status is Status.OK:
            old = self._holds.pop(t.tag_id, None)
            if old:
                old.cancel()
            self._holds[t.tag_id] = asyncio.create_task(self._refresh_hold(t, value, hold_ms, hb / 1000))
        return status

    async def release(self, name: str) -> Status:
        t = self.tag(name)
        task = self._holds.pop(t.tag_id, None)
        if task:
            task.cancel()
        client = self._client
        if client is None or not client.connected:
            return Status.COMM_FAILURE  # receiver releases on its own when the hold expires
        try:
            return await client.momentary(t.tag_id, t.data_type, t.effective_release_value(), 0)
        except LinkError:
            return Status.COMM_FAILURE

    # ------------------------------------------------------------------ internals

    def _require_online(self) -> LinkClient:
        if self._client is None or not self._client.connected or self.state is not LinkState.ONLINE:
            raise LinkError("not connected to receiver")
        return self._client

    async def _refresh_hold(self, tag: Tag, value: Any, hold_ms: int, period: float) -> None:
        try:
            while True:
                await asyncio.sleep(period)
                client = self._client
                if client is None or not client.connected:
                    return
                status = await client.momentary(tag.tag_id, tag.data_type, value, hold_ms)
                if status is not Status.OK:
                    log.warning("momentary refresh of %s rejected: %s", tag.name, status.name)
                    return
        except (LinkError, asyncio.CancelledError):
            pass
        finally:
            if self._holds.get(tag.tag_id) is asyncio.current_task():
                del self._holds[tag.tag_id]

    def _set_state(self, state: LinkState, error: str = "") -> None:
        self.last_error = error
        if state is self.state:
            return
        self.state = state
        if state is LinkState.ONLINE:
            self._online.set()
        else:
            self._online.clear()
        log.info("link state: %s %s", state.value, error)
        for fn in self.on_state:
            fn(state)

    def _load_cached_project(self) -> None:
        if not self.cache_dir:
            return
        path = self.cache_dir / "project.whc"
        if path.exists():
            try:
                self._set_project(CompiledProject.load(path))
            except (ProjectError, ValueError, OSError) as e:
                log.warning("cached project unusable: %s", e)

    def _set_project(self, project: CompiledProject) -> None:
        self.project = project
        self.values = {t.tag_id: CachedValue(t.data_type.zero(), Quality.BAD_NOT_CONNECTED, 0)
                       for t in project.project.tags}
        for fn in self.on_project:
            fn(project)

    def _mark_disconnected(self) -> None:
        for tid, cv in self.values.items():
            if cv.quality is not Quality.BAD_NOT_CONNECTED:
                cv.quality = Quality.BAD_NOT_CONNECTED
                self._notify(tid, cv)
        for task in self._holds.values():
            task.cancel()
        self._holds.clear()

    def _notify(self, tid: int, cv: CachedValue) -> None:
        if self.project is None or not self.on_value:
            return
        tag = self.project.tag(tid)
        if tag is None:
            return
        for fn in self.on_value:
            fn(tag, cv)

    def _on_data(self, values: list[TagValue]) -> None:
        for v in values:
            cv = self.values.get(v.tag_id)
            if cv is None:
                continue  # stale update from a previous project
            cv.value, cv.quality, cv.timestamp_ms = v.value, v.quality, v.timestamp_ms
            self._notify(v.tag_id, cv)

    async def _sync_project(self, client: LinkClient) -> bool:
        self._set_state(LinkState.SYNCING)
        expected, size, _name = await client.project_info()
        if size == 0:
            self._set_state(LinkState.NO_PROJECT, "receiver has no project")
            return False
        if self.project is None or self.project.hash != expected:
            data = await client.fetch_project()
            project = CompiledProject.from_bytes(data)
            if self.cache_dir:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                project.save(self.cache_dir / "project.whc")
            log.info("project %r synced from receiver (%s)", project.name, project.hash_hex[:12])
            self._set_project(project)
        assert self.project is not None
        await client.subscribe([t.tag_id for t in self.project.project.tags], self.interval_ms)
        return True

    async def run(self) -> None:
        delay = RECONNECT_MIN
        while not self._stopping:
            self._set_state(LinkState.CONNECTING)
            client = LinkClient(self.host, self.client_id, self.key, port=self.port, kind=self.kind)
            client.on_data = self._on_data
            client.on_project_changed = lambda _h: self._project_changed.set()
            try:
                await client.connect()
                self._client = client
                delay = RECONNECT_MIN
                while not self._stopping:
                    self._project_changed.clear()
                    if await self._sync_project(client):
                        self._set_state(LinkState.ONLINE)
                    closed = asyncio.create_task(client.wait_closed())
                    changed = asyncio.create_task(self._project_changed.wait())
                    done, pending = await asyncio.wait({closed, changed}, return_when=asyncio.FIRST_COMPLETED)
                    for p in pending:
                        p.cancel()
                    if closed in done:
                        raise LinkError(f"link lost: {closed.result()}")
                    log.info("receiver project changed; re-syncing")
                    self._mark_disconnected()
            except AuthenticationError as e:
                self._set_state(LinkState.AUTH_FAILED, str(e))
                delay = AUTH_RETRY
            except (LinkError, ProtocolError, ProjectError, ValueError, OSError, asyncio.TimeoutError) as e:
                self._set_state(LinkState.DISCONNECTED, str(e))
            except asyncio.CancelledError:
                raise
            finally:
                self._mark_disconnected()
                self._client = None
                await client.close()
            if self._stopping:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX) if delay < AUTH_RETRY else delay
        self._set_state(LinkState.DISCONNECTED)
