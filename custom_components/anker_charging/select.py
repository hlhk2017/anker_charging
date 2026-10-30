"""选择实体：充电模式（4 种）。"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CHARGING_MODES, CHARGING_MODE_VALUES, CMD_SUB_CHARGING_MODE, DOMAIN
from .coordinator import AnkerChargingCoordinator
from .entity import AnkerEntity
from .util import _make_add_new_entities

_LOGGER = logging.getLogger(__name__)


def _build_entities(coordinator, sn, device) -> list[SelectEntity]:
    return [AnkerChargingModeSelect(coordinator, sn)]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AnkerChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    add_new = _make_add_new_entities(
        coordinator, async_add_entities, lambda sn, dev: _build_entities(coordinator, sn, dev)
    )
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class AnkerChargingModeSelect(AnkerEntity, SelectEntity):
    """充电模式选择：智能动态分配 / 端口优先分配 / 双笔电模式 / 涓流模式。"""

    _attr_icon = "mdi:lightning-bolt"
    _attr_name = "充电模式"
    _attr_options = list(CHARGING_MODES.values())

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_charging_mode"

    @property
    def current_option(self) -> str | None:
        return CHARGING_MODES.get(self.coordinator.charging_mode)

    async def async_select_option(self, option: str) -> None:
        value = CHARGING_MODE_VALUES.get(option)
        if value is None:
            raise HomeAssistantError(f"无效的充电模式: {option}")
        if option == self.current_option:
            return
        ok = await self.coordinator.async_send_ctrl(CMD_SUB_CHARGING_MODE, value)
        if not ok:
            raise HomeAssistantError("充电模式指令发送失败（MQTT 未连接）")
        self.coordinator.charging_mode = value
        self.async_write_ha_state()
