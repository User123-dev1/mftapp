"""Receiver device configuration (per-device settings, not part of a project)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..link.frames import DEFAULT_PORT


@dataclass
class DeviceConfig:
    receiver_id: str = "WHMI-RX"
    listen_host: str = "0.0.0.0"
    listen_port: int = DEFAULT_PORT
    data_dir: str = "/var/lib/whmi"
    io_driver: str = "simulated"  # "simulated" | "simulated-loopback" | hardware drivers later
    max_sessions: int = 16
    max_project_bytes: int = 8 * 1024 * 1024

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def project_file(self) -> Path:
        return self.data_path / "project.whc"

    @property
    def retentive_file(self) -> Path:
        return self.data_path / "retentive.json"

    @property
    def credentials_file(self) -> Path:
        return self.data_path / "credentials.json"

    @classmethod
    def load(cls, path: str | Path) -> DeviceConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown receiver config keys: {', '.join(sorted(unknown))}")
        return cls(**data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
