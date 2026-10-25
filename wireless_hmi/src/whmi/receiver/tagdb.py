"""Live tag database of the receiver."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..common.codes import Quality
from ..common.project import SOURCE_MEMORY, SOURCE_SYSTEM, CompiledProject, Tag
from ..link.messages import TagValue


def now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class TagState:
    tag: Tag
    value: Any
    quality: Quality
    timestamp_ms: int

    def to_tag_value(self) -> TagValue:
        return TagValue(self.tag.tag_id, self.quality, self.timestamp_ms, self.tag.data_type, self.value)


Listener = Callable[[TagState], None]


class TagDB:
    def __init__(self, project: CompiledProject) -> None:
        self.project = project
        self.states: dict[int, TagState] = {}
        ts = now_ms()
        for tag in project.project.tags:
            if tag.source in (SOURCE_MEMORY, SOURCE_SYSTEM):
                self.states[tag.tag_id] = TagState(tag, tag.default_value(), Quality.GOOD, ts)
            else:
                self.states[tag.tag_id] = TagState(tag, tag.data_type.zero(), Quality.BAD_WAITING, ts)
        self._listeners: list[Listener] = []

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def get(self, tag_id: int) -> TagState | None:
        return self.states.get(tag_id)

    def by_name(self, name: str) -> TagState | None:
        tag = self.project.tag(name)
        return self.states.get(tag.tag_id) if tag else None

    def update(self, tag_id: int, value: Any = None, quality: Quality = Quality.GOOD,
               timestamp_ms: int | None = None) -> bool:
        """Set a tag's value/quality. ``value=None`` keeps the last value. Returns True if changed."""
        st = self.states[tag_id]
        new_value = st.value if value is None else value
        if new_value == st.value and quality == st.quality:
            return False
        st.value = new_value
        st.quality = quality
        st.timestamp_ms = timestamp_ms if timestamp_ms is not None else now_ms()
        for fn in self._listeners:
            fn(st)
        return True

    def set_quality(self, tag_ids: list[int], quality: Quality) -> None:
        for tid in tag_ids:
            self.update(tid, None, quality)
