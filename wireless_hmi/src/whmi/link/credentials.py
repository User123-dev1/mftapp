"""Client credential store (lives on the receiver, never inside a project)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..common.codes import Role
from .crypto import generate_key, parse_key


@dataclass(frozen=True)
class Credential:
    client_id: str
    key: bytes
    role: Role


class CredentialStore:
    def __init__(self, credentials: list[Credential] | None = None) -> None:
        self._by_id: dict[str, Credential] = {}
        for c in credentials or []:
            self.add(c)

    def add(self, cred: Credential) -> None:
        if not cred.client_id or len(cred.client_id.encode()) > 64:
            raise ValueError("client id must be 1..64 bytes")
        self._by_id[cred.client_id] = cred

    def remove(self, client_id: str) -> None:
        self._by_id.pop(client_id, None)

    def get(self, client_id: str) -> Credential | None:
        return self._by_id.get(client_id)

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    @classmethod
    def from_dict(cls, d: dict) -> CredentialStore:
        return cls([Credential(c["id"], parse_key(c["key"]), Role.parse(c.get("role", "viewer")))
                    for c in d.get("clients", [])])

    def to_dict(self) -> dict:
        return {"clients": [{"id": c.client_id, "key": c.key.hex(), "role": c.role.name.lower()}
                            for c in self._by_id.values()]}

    @classmethod
    def load(cls, path: str | Path) -> CredentialStore:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        """Write the store with owner-only permissions (it contains secrets)."""
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        os.replace(tmp, path)

    def create(self, client_id: str, role: Role | str) -> Credential:
        cred = Credential(client_id, generate_key(), Role.parse(role))
        self.add(cred)
        return cred
