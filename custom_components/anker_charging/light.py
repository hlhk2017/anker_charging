"""灯光实体：屏幕亮度。"""
from __future__ import annotations

import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_SUB_BRIGHTNESS, DOMAIN
from .coordinator import AnkerChargingCoordinator
from .entity import AnkerEntity
from .util import _make_add_new_entities

_LOGGER = logging.getLogger(__name__)


def _build_entities(coordinator, sn, device) -> list[LightEntity]:
    return [AnkerScreenLight(coordinator, sn)]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AnkerChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    add_new = _make_add_new_entities(
        coordinator, async_add_entities, lambda sn, dev: _build_entities(coordinator, sn, dev)
    )
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class AnkerScreenLight(AnkerEntity, LightEntity):
    """设备屏幕亮度（0-100% 映射为 HA 亮度 0-255）。"""

    _attr_icon = "mdi:brightness-6"
    _attr_name = "屏幕亮度"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_screen_brightness"

    @property
    def is_on(self) -> bool:
        return (self.coordinator.brightness_pct or 0) > 0

    @property
    def brightness(self) -> int:
        return round((self.coordinator.brightness_pct or 0) * 255 / 100)

    async def _apply_pct(self, pct: int) -> None:
        pct = max(0, min(100, int(pct)))
        ok = await self.coordinator.async_send_ctrl(CMD_SUB_BRIGHTNESS, pct)
        if not ok:
            raise HomeAssistantError("屏幕亮度指令发送失败（MQTT 未连接）")
        self.coordinator.brightness_pct = pct
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            pct = round(int(kwargs[ATTR_BRIGHTNESS]) * 100 / 255)
        else:
            pct = self.coordinator.brightness_pct or 70
            if pct <= 0:
                pct = 70
        await self._apply_pct(pct)

    async def async_turn_off(self, **kwargs) -> None:
        await self._apply_pct(0)
