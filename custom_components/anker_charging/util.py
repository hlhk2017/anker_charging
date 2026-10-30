"""平台层公共工具。"""
from __future__ import annotations

from typing import Any, Callable


def _make_add_new_entities(coordinator, async_add_entities, build):
    """返回一个监听器：为新出现的设备补建实体（设备动态增减时无需重启 HA）。"""
    known: set[str] = set()

    def _add_new_entities() -> None:
        new_entities = []
        for sn, device in (coordinator.data or {}).items():
            if sn in known:
                continue
            known.add(sn)
            new_entities.extend(build(sn, device))
        if new_entities:
            async_add_entities(new_entities)

    return _add_new_entities
