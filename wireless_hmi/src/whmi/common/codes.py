"""Quality, status and role codes shared by the receiver, runtime and protocol."""

from __future__ import annotations

import enum


class Quality(enum.IntEnum):
    GOOD = 0x00
    UNCERTAIN = 0x40
    BAD = 0x80
    BAD_COMM = 0x81
    BAD_CONFIG = 0x82
    BAD_NOT_CONNECTED = 0x83
    BAD_DEVICE = 0x84
    BAD_WAITING = 0x85

    @property
    def is_bad(self) -> bool:
        return bool(self & 0x80)

    @property
    def is_good(self) -> bool:
        return self is Quality.GOOD


class Status(enum.IntEnum):
    OK = 0
    BAD_TAG = 1
    READ_ONLY = 2
    TYPE_MISMATCH = 3
    OUT_OF_RANGE = 4
    NOT_PERMITTED = 5
    COMM_FAILURE = 6
    BUSY = 7
    INVALID = 8
    INTERNAL = 9


class Role(enum.IntEnum):
    VIEWER = 1
    OPERATOR = 2
    ENGINEER = 3

    @classmethod
    def parse(cls, name: str | int | Role) -> Role:
        if isinstance(name, Role):
            return name
        if isinstance(name, int):
            return cls(name)
        try:
            return cls[name.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown role {name!r}") from None

    @property
    def can_write(self) -> bool:
        return self >= Role.OPERATOR


class ClientKind(enum.IntEnum):
    PANEL = 1
    STUDIO = 2
    TOOL = 3
