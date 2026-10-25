"""WHMI project model, validation and build (compile) step.

A project (``*.whp``) is a JSON document authored in WHMI Studio (or with the
``whmi`` CLI). Building it validates everything, assigns each tag a numeric id
used on the wire, adds the receiver's system tags and produces a canonical,
hashed *compiled project* that is downloaded to the receiver.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .datatypes import CoercionError, DataType, Scaling, coerce

PROJECT_FORMAT = "whmi-project"
COMPILED_FORMAT = "whmi-compiled"
FORMAT_VERSION = 1

MAX_TAGS = 65000
TAG_NAME_MAX = 64
_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IO_ADDR_RE = re.compile(r"^Slot(\d+)\.(DI|DO|AI|AO)\.(\d+)$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:]+$")

SOURCE_MEMORY = "memory"
SOURCE_IO = "io"
SOURCE_PLC = "plc"
SOURCE_SYSTEM = "system"
SOURCES = (SOURCE_MEMORY, SOURCE_IO, SOURCE_PLC, SOURCE_SYSTEM)

ACCESS_READ = "read"
ACCESS_READ_WRITE = "read_write"

MOMENTARY_MAX_HOLD_MS = 2000


# --------------------------------------------------------------------------- I/O catalog


@dataclass(frozen=True)
class IoModuleType:
    catalog: str
    kind: str  # DI, DO, AI, AO
    channels: int
    raw_type: DataType
    raw_min: float
    raw_max: float
    description: str

    @property
    def is_output(self) -> bool:
        return self.kind in ("DO", "AO")


IO_CATALOG: dict[str, IoModuleType] = {
    m.catalog: m
    for m in (
        IoModuleType("DI8", "DI", 8, DataType.BOOL, 0, 1, "8-point 24 VDC sinking digital input"),
        IoModuleType("DI16", "DI", 16, DataType.BOOL, 0, 1, "16-point 24 VDC sinking digital input"),
        IoModuleType("DO8", "DO", 8, DataType.BOOL, 0, 1, "8-point 24 VDC sourcing digital output, 0.5 A"),
        IoModuleType("DO16", "DO", 16, DataType.BOOL, 0, 1, "16-point 24 VDC sourcing digital output, 0.5 A"),
        IoModuleType("AI4", "AI", 4, DataType.INT, 0, 32000, "4-channel 4-20 mA analog input, 0..32000 counts"),
        IoModuleType("AI4V", "AI", 4, DataType.INT, 0, 32000, "4-channel 0-10 V analog input, 0..32000 counts"),
        IoModuleType("AO2", "AO", 2, DataType.INT, 0, 32000, "2-channel 4-20 mA analog output, 0..32000 counts"),
        IoModuleType("AO2V", "AO", 2, DataType.INT, 0, 32000, "2-channel 0-10 V analog output, 0..32000 counts"),
    )
}

MAX_SLOTS = 16


@dataclass(frozen=True)
class IoAddress:
    slot: int
    kind: str
    channel: int

    @classmethod
    def parse(cls, text: str) -> IoAddress:
        m = _IO_ADDR_RE.match(text.strip())
        if not m:
            raise ValueError(f"invalid I/O address {text!r} (expected e.g. 'Slot1.DI.0')")
        return cls(int(m.group(1)), m.group(2), int(m.group(3)))

    def __str__(self) -> str:
        return f"Slot{self.slot}.{self.kind}.{self.channel}"


# --------------------------------------------------------------------------- validation


class ProjectError(Exception):
    """Raised when a project cannot be built; carries all validation issues."""

    def __init__(self, issues: list[Issue]):
        self.issues = issues
        errors = [i for i in issues if i.severity == "error"]
        lines = "\n".join(f"  {i}" for i in errors)
        super().__init__(f"project has {len(errors)} error(s):\n{lines}")


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity.upper()}: {self.path}: {self.message}"


class _Collector:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, path: str, msg: str) -> None:
        self.issues.append(Issue("error", path, msg))

    def warn(self, path: str, msg: str) -> None:
        self.issues.append(Issue("warning", path, msg))


def valid_tag_name(name: str) -> bool:
    if not isinstance(name, str) or not 0 < len(name) <= TAG_NAME_MAX:
        return False
    return all(_SEGMENT_RE.match(seg) for seg in name.split("."))


# --------------------------------------------------------------------------- model


@dataclass
class PlcConnection:
    name: str
    driver: str = "logix"
    host: str = ""
    path: str = "1,0"  # backplane, slot of the controller
    poll_ms: int = 100
    timeout_ms: int = 2000
    # Optional DINT tag in the PLC that the receiver increments every heartbeat_ms while at
    # least one operator panel is connected. PLC logic watches it to detect HMI/link loss.
    heartbeat_tag: str = ""
    heartbeat_ms: int = 500

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlcConnection:
        return cls(
            name=d["name"],
            driver=d.get("driver", "logix"),
            host=d.get("host", ""),
            path=str(d.get("path", "1,0")),
            poll_ms=int(d.get("poll_ms", 100)),
            timeout_ms=int(d.get("timeout_ms", 2000)),
            heartbeat_tag=d.get("heartbeat_tag", ""),
            heartbeat_ms=int(d.get("heartbeat_ms", 500)),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "driver": self.driver,
            "host": self.host,
            "path": self.path,
            "poll_ms": self.poll_ms,
            "timeout_ms": self.timeout_ms,
        }
        if self.heartbeat_tag:
            d["heartbeat_tag"] = self.heartbeat_tag
            d["heartbeat_ms"] = self.heartbeat_ms
        return d


PLC_DRIVERS = ("logix", "simulator")


@dataclass
class Tag:
    name: str
    data_type: DataType
    source: str = SOURCE_MEMORY
    address: str = ""  # io: "Slot1.DI.0"; plc: PLC tag name; system: system key
    connection: str = ""  # plc connection name
    access: str = ACCESS_READ
    description: str = ""
    units: str = ""
    initial: Any = None
    retentive: bool = False
    scaling: Scaling | None = None
    min: float | None = None
    max: float | None = None
    deadband: float = 0.0
    momentary: bool = False
    release_value: Any = None
    failsafe_value: Any = None
    failsafe_on_any_loss: bool = False
    safety: bool = False
    tag_id: int = 0  # assigned by build

    @property
    def writable(self) -> bool:
        return self.access == ACCESS_READ_WRITE and not self.safety

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tag:
        src = d.get("source", SOURCE_MEMORY)
        if isinstance(src, dict):  # nested form {"type": "plc", "connection": .., "address": ..}
            source = src.get("type", SOURCE_MEMORY)
            address = src.get("address", "")
            connection = src.get("connection", "")
        else:
            source = src
            address = d.get("address", "")
            connection = d.get("connection", "")
        sc = d.get("scaling")
        return cls(
            name=d["name"],
            data_type=DataType.parse(d.get("data_type", "DINT")),
            source=source,
            address=address,
            connection=connection,
            access=d.get("access", ACCESS_READ),
            description=d.get("description", ""),
            units=d.get("units", ""),
            initial=d.get("initial"),
            retentive=bool(d.get("retentive", False)),
            scaling=Scaling(**sc) if sc else None,
            min=d.get("min"),
            max=d.get("max"),
            deadband=float(d.get("deadband", 0.0)),
            momentary=bool(d.get("momentary", False)),
            release_value=d.get("release_value"),
            failsafe_value=d.get("failsafe_value"),
            failsafe_on_any_loss=bool(d.get("failsafe_on_any_loss", False)),
            safety=bool(d.get("safety", False)),
            tag_id=int(d.get("tag_id", 0)),
        )

    def to_dict(self, *, include_id: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "data_type": self.data_type.name, "source": self.source}
        if self.address:
            d["address"] = self.address
        if self.connection:
            d["connection"] = self.connection
        d["access"] = self.access
        optional = {
            "description": (self.description, ""),
            "units": (self.units, ""),
            "initial": (self.initial, None),
            "retentive": (self.retentive, False),
            "min": (self.min, None),
            "max": (self.max, None),
            "deadband": (self.deadband, 0.0),
            "momentary": (self.momentary, False),
            "release_value": (self.release_value, None),
            "failsafe_value": (self.failsafe_value, None),
            "failsafe_on_any_loss": (self.failsafe_on_any_loss, False),
            "safety": (self.safety, False),
        }
        for key, (value, default) in optional.items():
            if value != default:
                d[key] = value
        if self.scaling:
            d["scaling"] = {
                "raw_min": self.scaling.raw_min,
                "raw_max": self.scaling.raw_max,
                "eng_min": self.scaling.eng_min,
                "eng_max": self.scaling.eng_max,
                "clamp": self.scaling.clamp,
            }
        if include_id:
            d["tag_id"] = self.tag_id
        return d

    # value helpers --------------------------------------------------------

    def default_value(self) -> Any:
        if self.initial is not None:
            return coerce(self.data_type, self.initial)
        return self.data_type.zero()

    def effective_release_value(self) -> Any:
        if self.release_value is not None:
            return coerce(self.data_type, self.release_value)
        return self.data_type.zero()


WIDGET_TYPES = {
    # type: (needs_tag, writes_tag)
    "label": (False, False),
    "rectangle": (False, False),
    "indicator": (True, False),
    "numeric_display": (True, False),
    "text_display": (True, False),
    "bar": (True, False),
    "gauge": (True, False),
    "trend": (True, False),
    "button": (True, True),
    "numeric_entry": (True, True),
    "text_entry": (True, True),
    "toggle": (True, True),
    "goto": (False, False),
}

BUTTON_ACTIONS = ("momentary", "set", "reset", "toggle", "write")


@dataclass
class Screen:
    name: str
    width: int = 1024
    height: int = 600
    background: str = "#1E1E1E"
    widgets: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Screen:
        return cls(
            name=d["name"],
            width=int(d.get("width", 1024)),
            height=int(d.get("height", 600)),
            background=d.get("background", "#1E1E1E"),
            widgets=[dict(w) for w in d.get("widgets", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "background": self.background,
            "widgets": copy.deepcopy(self.widgets),
        }


@dataclass
class ReceiverConfig:
    name: str = "RX1"
    scan_ms: int = 10
    heartbeat_ms: int = 250
    watchdog_ms: int = 1000
    slots: dict[int, str] = field(default_factory=dict)  # slot number -> catalog id

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReceiverConfig:
        return cls(
            name=d.get("name", "RX1"),
            scan_ms=int(d.get("scan_ms", 10)),
            heartbeat_ms=int(d.get("heartbeat_ms", 250)),
            watchdog_ms=int(d.get("watchdog_ms", 1000)),
            slots={int(k): v for k, v in d.get("slots", {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scan_ms": self.scan_ms,
            "heartbeat_ms": self.heartbeat_ms,
            "watchdog_ms": self.watchdog_ms,
            "slots": {str(k): v for k, v in sorted(self.slots.items())},
        }


@dataclass
class Project:
    name: str
    description: str = ""
    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)
    plc_connections: list[PlcConnection] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    screens: list[Screen] = field(default_factory=list)
    start_screen: str = ""

    # ------------------------------------------------------------ (de)serialisation

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Project:
        fmt = d.get("format", PROJECT_FORMAT)
        if fmt not in (PROJECT_FORMAT, COMPILED_FORMAT):
            raise ValueError(f"not a WHMI project (format={fmt!r})")
        if int(d.get("format_version", FORMAT_VERSION)) > FORMAT_VERSION:
            raise ValueError("project was created by a newer WHMI version")
        return cls(
            name=d.get("name", "Untitled"),
            description=d.get("description", ""),
            receiver=ReceiverConfig.from_dict(d.get("receiver", {})),
            plc_connections=[PlcConnection.from_dict(c) for c in d.get("plc_connections", [])],
            tags=[Tag.from_dict(t) for t in d.get("tags", [])],
            screens=[Screen.from_dict(s) for s in d.get("screens", [])],
            start_screen=d.get("start_screen", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PROJECT_FORMAT,
            "format_version": FORMAT_VERSION,
            "name": self.name,
            "description": self.description,
            "receiver": self.receiver.to_dict(),
            "plc_connections": [c.to_dict() for c in self.plc_connections],
            "tags": [t.to_dict() for t in self.tags if t.source != SOURCE_SYSTEM],
            "screens": [s.to_dict() for s in self.screens],
            "start_screen": self.start_screen,
        }

    @classmethod
    def load(cls, path: str | Path) -> Project:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ lookup helpers

    def tag(self, name: str) -> Tag | None:
        for t in self.tags:
            if t.name == name:
                return t
        return None

    def connection(self, name: str) -> PlcConnection | None:
        for c in self.plc_connections:
            if c.name == name:
                return c
        return None

    # ------------------------------------------------------------ validation

    def validate(self) -> list[Issue]:
        c = _Collector()
        self._validate_receiver(c)
        self._validate_connections(c)
        self._validate_tags(c)
        self._validate_screens(c)
        return c.issues

    def _validate_receiver(self, c: _Collector) -> None:
        r = self.receiver
        if not r.name or not _SEGMENT_RE.match(r.name):
            c.error("receiver.name", "must be an identifier")
        if not 1 <= r.scan_ms <= 1000:
            c.error("receiver.scan_ms", "must be 1..1000")
        if not 50 <= r.heartbeat_ms <= 5000:
            c.error("receiver.heartbeat_ms", "must be 50..5000")
        if r.watchdog_ms < 2 * r.heartbeat_ms:
            c.error("receiver.watchdog_ms", "must be at least 2 x heartbeat_ms")
        if r.watchdog_ms > 30000:
            c.error("receiver.watchdog_ms", "must be <= 30000")
        for slot, cat in r.slots.items():
            if not 1 <= slot <= MAX_SLOTS:
                c.error(f"receiver.slots.{slot}", f"slot must be 1..{MAX_SLOTS}")
            if cat not in IO_CATALOG:
                c.error(f"receiver.slots.{slot}", f"unknown module {cat!r}; known: {', '.join(IO_CATALOG)}")

    def _validate_connections(self, c: _Collector) -> None:
        seen: set[str] = set()
        for i, conn in enumerate(self.plc_connections):
            p = f"plc_connections[{i}]"
            if not _SEGMENT_RE.match(conn.name or ""):
                c.error(p, f"invalid connection name {conn.name!r}")
            if conn.name in seen:
                c.error(p, f"duplicate connection name {conn.name!r}")
            seen.add(conn.name)
            if conn.driver not in PLC_DRIVERS:
                c.error(p, f"unknown driver {conn.driver!r}; known: {', '.join(PLC_DRIVERS)}")
            if conn.driver == "logix" and (not conn.host or not _HOST_RE.match(conn.host)):
                c.error(p, "logix connection needs a valid host/IP address")
            if not 10 <= conn.poll_ms <= 60000:
                c.error(p, "poll_ms must be 10..60000")
            if not 100 <= conn.timeout_ms <= 60000:
                c.error(p, "timeout_ms must be 100..60000")
            if conn.heartbeat_tag and not 100 <= conn.heartbeat_ms <= 10000:
                c.error(p, "heartbeat_ms must be 100..10000")

    def _validate_tags(self, c: _Collector) -> None:
        names: set[str] = set()
        io_used: dict[str, str] = {}
        user_tags = [t for t in self.tags if t.source != SOURCE_SYSTEM]
        if len(user_tags) > MAX_TAGS:
            c.error("tags", f"too many tags ({len(user_tags)} > {MAX_TAGS})")
        for t in user_tags:
            p = f"tags.{t.name}"
            if not valid_tag_name(t.name):
                c.error(p, "invalid tag name (segments must be identifiers, separated by '.')")
            if t.name.lower() in names:
                c.error(p, "duplicate tag name (names are case-insensitive)")
            names.add(t.name.lower())
            if t.source not in (SOURCE_MEMORY, SOURCE_IO, SOURCE_PLC):
                c.error(p, f"invalid source {t.source!r}")
            if t.access not in (ACCESS_READ, ACCESS_READ_WRITE):
                c.error(p, f"invalid access {t.access!r}")

            if t.source == SOURCE_IO:
                self._validate_io_tag(c, p, t, io_used)
            elif t.source == SOURCE_PLC:
                if not t.connection:
                    c.error(p, "plc tag needs a connection")
                elif self.connection(t.connection) is None:
                    c.error(p, f"unknown connection {t.connection!r}")
                if not t.address:
                    c.error(p, "plc tag needs an address (PLC tag name)")

            if t.scaling is not None:
                if not t.data_type.is_float:
                    c.error(p, "scaled tags must be REAL or LREAL (engineering units)")
                if t.source == SOURCE_MEMORY:
                    c.warn(p, "scaling has no effect on memory tags")

            for key in ("initial", "release_value", "failsafe_value"):
                v = getattr(t, key)
                if v is not None:
                    try:
                        coerce(t.data_type, v)
                    except CoercionError as e:
                        c.error(p, f"{key}: {e}")
            if t.min is not None and t.max is not None and t.min > t.max:
                c.error(p, "min must be <= max")
            if (t.min is not None or t.max is not None) and not t.data_type.is_numeric:
                c.error(p, "min/max only apply to numeric tags")
            if t.deadband < 0:
                c.error(p, "deadband must be >= 0")

            if t.momentary:
                if t.access != ACCESS_READ_WRITE:
                    c.error(p, "momentary tags must be read_write")
                if t.data_type is DataType.STRING:
                    c.error(p, "momentary tags cannot be STRING")
            if t.failsafe_value is not None and t.access != ACCESS_READ_WRITE:
                c.warn(p, "failsafe_value on a read-only tag is ignored")
            if t.safety and t.access == ACCESS_READ_WRITE:
                c.error(p, "safety tags are display-only: access must be 'read'. "
                        "Safety functions must be hardwired (IEC 60204-1 / ISO 13849)")

    def _validate_io_tag(self, c: _Collector, p: str, t: Tag, io_used: dict[str, str]) -> None:
        try:
            addr = IoAddress.parse(t.address)
        except ValueError as e:
            c.error(p, str(e))
            return
        cat = self.receiver.slots.get(addr.slot)
        if cat is None:
            c.error(p, f"slot {addr.slot} has no module configured")
            return
        mod = IO_CATALOG.get(cat)
        if mod is None:
            return  # reported in receiver validation
        if mod.kind != addr.kind:
            c.error(p, f"slot {addr.slot} holds a {mod.kind} module ({cat}), not {addr.kind}")
            return
        if not 0 <= addr.channel < mod.channels:
            c.error(p, f"channel {addr.channel} out of range 0..{mod.channels - 1} for {cat}")
        key = str(addr)
        if key in io_used:
            c.error(p, f"I/O point {key} already used by tag {io_used[key]}")
        io_used[key] = t.name
        if mod.kind in ("DI", "DO") and t.data_type is not DataType.BOOL:
            c.error(p, f"{mod.kind} points must be BOOL tags")
        if mod.kind in ("AI", "AO") and not t.data_type.is_numeric:
            c.error(p, f"{mod.kind} points must be numeric tags")
        if not mod.is_output and t.access == ACCESS_READ_WRITE:
            c.error(p, f"{mod.kind} points are inputs and cannot be read_write")

    def _validate_screens(self, c: _Collector) -> None:
        names: set[str] = set()
        for s in self.screens:
            if not s.name or s.name in names:
                c.error(f"screens.{s.name}", "screen names must be unique and non-empty")
            names.add(s.name)
        if self.screens and self.start_screen and self.start_screen not in names:
            c.error("start_screen", f"unknown screen {self.start_screen!r}")
        if self.screens and not self.start_screen:
            c.warn("start_screen", "not set; the first screen will be used")
        bindable = {t.name: t for t in self.tags}
        bindable.update({t.name: t for t in system_tags(self)})
        for s in self.screens:
            ids: set[str] = set()
            for i, w in enumerate(s.widgets):
                wid = w.get("id") or f"#{i}"
                p = f"screens.{s.name}.{wid}"
                if wid in ids:
                    c.error(p, "duplicate widget id")
                ids.add(wid)
                wtype = w.get("type")
                if wtype not in WIDGET_TYPES:
                    c.error(p, f"unknown widget type {wtype!r}")
                    continue
                for dim in ("x", "y", "w", "h"):
                    if not isinstance(w.get(dim), (int, float)):
                        c.error(p, f"missing/invalid {dim}")
                needs_tag, writes = WIDGET_TYPES[wtype]
                if wtype == "goto" and w.get("screen") not in names:
                    c.error(p, f"goto target {w.get('screen')!r} does not exist")
                if not needs_tag:
                    continue
                tag = bindable.get(w.get("tag", ""))
                if tag is None:
                    c.error(p, f"bound tag {w.get('tag')!r} does not exist")
                    continue
                if not writes:
                    continue
                if tag.safety:
                    c.error(p, f"tag {tag.name} is a safety tag and cannot be bound to a write widget")
                elif tag.access != ACCESS_READ_WRITE:
                    c.error(p, f"tag {tag.name} is read-only")
                if wtype == "button":
                    action = w.get("action", "momentary")
                    if action not in BUTTON_ACTIONS:
                        c.error(p, f"invalid button action {action!r}")
                    if action == "momentary" and not tag.momentary:
                        c.error(p, f"momentary button needs tag {tag.name} to be flagged momentary")
                    if action in ("set", "reset", "toggle") and tag.data_type is not DataType.BOOL:
                        c.error(p, f"action {action!r} needs a BOOL tag")
                if wtype == "toggle" and tag.data_type is not DataType.BOOL:
                    c.error(p, "toggle needs a BOOL tag")

    # ------------------------------------------------------------ build

    def build(self) -> CompiledProject:
        issues = self.validate()
        if any(i.severity == "error" for i in issues):
            raise ProjectError(issues)
        tags = [copy.deepcopy(t) for t in self.tags if t.source != SOURCE_SYSTEM]
        tags.extend(system_tags(self))
        for i, t in enumerate(tags, start=1):
            t.tag_id = i
        doc = self.to_dict()
        doc["format"] = COMPILED_FORMAT
        doc["tags"] = [t.to_dict(include_id=True) for t in tags]
        if self.screens and not doc["start_screen"]:
            doc["start_screen"] = self.screens[0].name
        return CompiledProject.from_document(doc, warnings=[i for i in issues if i.severity == "warning"])


def system_tags(project: Project) -> list[Tag]:
    """Diagnostic tags produced by the receiver itself."""
    tags = [
        Tag("_System.Link.Clients", DataType.UINT, SOURCE_SYSTEM, "link.clients",
            description="Number of authenticated WHMI-Link sessions"),
        Tag("_System.Link.Connected", DataType.BOOL, SOURCE_SYSTEM, "link.connected",
            description="At least one operator/engineer session is connected"),
        Tag("_System.Uptime", DataType.UDINT, SOURCE_SYSTEM, "uptime", units="s",
            description="Receiver uptime"),
        Tag("_System.ScanTime", DataType.REAL, SOURCE_SYSTEM, "scan_time", units="ms", deadband=0.5,
            description="Last I/O scan duration"),
    ]
    for conn in project.plc_connections:
        tags.append(Tag(f"_System.PLC.{conn.name}.Connected", DataType.BOOL, SOURCE_SYSTEM,
                        f"plc.{conn.name}.connected", description=f"PLC connection {conn.name} is up"))
    return tags


def canonical_json(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class CompiledProject:
    """An immutable, validated project with tag ids, as stored on the receiver."""

    def __init__(self, data: bytes, project: Project, warnings: list[Issue] | None = None):
        self.data = data
        self.hash = hashlib.sha256(data).digest()
        self.project = project
        self.warnings = warnings or []
        self.tags_by_id: dict[int, Tag] = {t.tag_id: t for t in project.tags}
        self.tags_by_name: dict[str, Tag] = {t.name: t for t in project.tags}
        self._lower: dict[str, Tag] = {t.name.lower(): t for t in project.tags}

    @classmethod
    def from_document(cls, doc: dict[str, Any], warnings: list[Issue] | None = None) -> CompiledProject:
        data = canonical_json(doc)
        return cls(data, Project.from_dict(doc), warnings)

    @classmethod
    def from_bytes(cls, data: bytes) -> CompiledProject:
        """Load a compiled project, re-validating it fully (it may come off the network)."""
        doc = json.loads(data.decode("utf-8"))
        if doc.get("format") != COMPILED_FORMAT:
            raise ValueError("not a compiled WHMI project")
        project = Project.from_dict(doc)
        issues = project.validate()
        if any(i.severity == "error" for i in issues):
            raise ProjectError(issues)
        ids = [t.tag_id for t in project.tags]
        if len(set(ids)) != len(ids) or any(not 1 <= i <= 0xFFFF for i in ids):
            raise ValueError("compiled project has invalid tag ids")
        return cls(data, project, [i for i in issues if i.severity == "warning"])

    @classmethod
    def load(cls, path: str | Path) -> CompiledProject:
        return cls.from_bytes(Path(path).read_bytes())

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.data)

    @property
    def name(self) -> str:
        return self.project.name

    @property
    def hash_hex(self) -> str:
        return self.hash.hex()

    def tag(self, key: int | str) -> Tag | None:
        if isinstance(key, int):
            return self.tags_by_id.get(key)
        return self.tags_by_name.get(key) or self._lower.get(key.lower())
