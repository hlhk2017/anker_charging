"""二进制传感器：在线状态、充电状态。"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AnkerChargingCoordinator
from .entity import AnkerEntity
from .util import _make_add_new_entities


def _build_entities(coordinator, sn, device) -> list[BinarySensorEntity]:
    return [
        AnkerOnlineBinarySensor(coordinator, sn),
        AnkerChargingBinarySensor(coordinator, sn),
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AnkerChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    add_new = _make_add_new_entities(
        coordinator, async_add_entities, lambda sn, dev: _build_entities(coordinator, sn, dev)
    )
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class AnkerOnlineBinarySensor(AnkerEntity, BinarySensorEntity):
    """设备是否在线。"""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_online"
        self._attr_name = "在线"

    @property
    def is_on(self) -> bool:
        return bool(self.device_data and self.device_data.online)


class AnkerChargingBinarySensor(AnkerEntity, BinarySensorEntity):
    """设备是否正在充电。"""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_charging"
        self._attr_name = "充电中"

    @property
    def is_on(self) -> bool:
        return bool(self.device_data and self.device_data.charging)