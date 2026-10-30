"""实体基类。"""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AnkerChargingCoordinator, AnkerDeviceData


class AnkerEntity(CoordinatorEntity[AnkerChargingCoordinator]):
    """所有安克实体的基类。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn

    @property
    def device_data(self) -> AnkerDeviceData | None:
        return self.coordinator.data.get(self._sn)

    @property
    def device_info(self) -> DeviceInfo:
        data = self.device_data
        return DeviceInfo(
            identifiers={(DOMAIN, self._sn)},
            name=data.name if data else self._sn,
            manufacturer="Anker 安克",
            model=data.model if data else None,
            sw_version=data.sw_version if data else None,
            configuration_url=f"https://{self.coordinator.api.host}",
        )
