"""Threat-intelligence lookups against the indicators table (block and allow lists)."""

from __future__ import annotations

import sqlite3
from typing import Optional

from .domains import registrable_domain


class Intel:
    """Loaded once per scan so every check uses the same snapshot."""

    def __init__(self, conn: sqlite3.Connection):
        self.block = {}
        self.allow = set()
        for r in conn.execute("SELECT kind, value, action, note, source FROM indicators WHERE active = 1"):
            key = (r["kind"], r["value"].lower())
            if r["action"] == "block":
                self.block[key] = r["note"] or r["source"] or ""
            else:
                self.allow.add(key)

    def blocked(self, kind: str, value: str) -> Optional[str]:
        """Note for a blocked indicator, None when not blocked (an empty note still means blocked)."""
        return self.block.get((kind, (value or "").lower()))

    def sender_blocked(self, address: str) -> Optional[str]:
        domain = address.rsplit("@", 1)[-1]
        for kind, value in (("sender", address), ("domain", domain), ("domain", registrable_domain(domain))):
            note = self.blocked(kind, value)
            if note is not None:
                return note
        return None

    def sender_allowed(self, address: str) -> bool:
        domain = address.rsplit("@", 1)[-1]
        return any(k in self.allow for k in (("sender", address.lower()), ("domain", domain.lower()),
                                             ("domain", registrable_domain(domain))))
