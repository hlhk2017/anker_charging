"""传感器实体：功率、电量、信号、固件。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AnkerChargingCoordinator
from .entity import AnkerEntity
from .util import _make_add_new_entities


@dataclass(frozen=True, kw_only=True)
class AnkerSensorDescription(SensorEntityDescription):
    """设备级传感器描述。"""

    value_fn: Callable[[Any], Any] = lambda d: None


DEVICE_SENSORS: tuple[AnkerSensorDescription, ...] = (
    AnkerSensorDescription(
        key="today_energy",
        translation_key="today_energy",
        name="今日输出电量",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda d: (d.ports.get("Total").today_energy if d.ports.get("Total") else None),
    ),
    AnkerSensorDescription(
        key="total_power",
        translation_key="total_power",
        name="当前总功率",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: (d.ports.get("Total").current_power if d.ports.get("Total") else None),
    ),
    AnkerSensorDescription(
        key="rssi",
        translation_key="rssi",
        name="WiFi 信号",
        native_unit_of_measurement="dBm",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.rssi,
    ),
    AnkerSensorDescription(
        key="sw_version",
        translation_key="sw_version",
        name="固件版本",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.sw_version,
    ),
    AnkerSensorDescription(
        key="ip_address",
        name="IP 地址",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.info.get("ip"),
    ),
    AnkerSensorDescription(
        key="ssid",
        name="WiFi 名称",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.info.get("ssid") or d.info.get("wifi_name"),
    ),
    AnkerSensorDescription(
        key="battery",
        name="内部电量",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery",
        value_fn=lambda d: d.info.get("battery"),
    ),
)


def _build_entities(coordinator, sn, device) -> list[SensorEntity]:
    """为一台设备构造全部传感器实体。"""
    entities: list[SensorEntity] = [
        AnkerDeviceSensor(coordinator, sn, description) for description in DEVICE_SENSORS
    ]
    # 每个端口一个功率传感器 + 一个电量传感器
    for port_name in sorted(device.ports):
        if port_name == "Total":
            continue
        entities.append(AnkerPortPowerSensor(coordinator, sn, port_name))
        entities.append(AnkerPortEnergySensor(coordinator, sn, port_name))
    entities.append(AnkerScreensaverSensor(coordinator, sn))
    entities.append(AnkerPowerRangeSensor(coordinator, sn))
    return entities


class AnkerScreensaverSensor(AnkerEntity, SensorEntity):
    """时钟屏保清单（只读，作为设置选项参考）。"""

    _attr_name = "时钟屏保选项"
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_screensaver_options"

    @property
    def native_value(self) -> int | None:
        data = self.device_data
        if data is None:
            return None
        return sum(len(c.get("list", [])) for c in data.screensavers)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.device_data
        if data is None:
            return {}
        themes = []
        for cat in data.screensavers:
            for item in cat.get("list", []):
                themes.append(
                    {
                        "category": cat.get("category_name"),
                        "title": item.get("title"),
                        "real_id": item.get("real_id"),
                    }
                )
        return {"themes": themes}


class AnkerPowerRangeSensor(AnkerEntity, SensorEntity):
    """端口功率档位→协议支持（只读，端口优先分配参考）。"""

    _attr_name = "端口协议支持"
    _attr_icon = "mdi:usb-port"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_power_range_protocols"

    @property
    def native_value(self) -> int | None:
        data = self.device_data
        if data is None:
            return None
        return len(data.power_ranges)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.device_data
        if data is None:
            return {}
        ports = {}
        for item in data.power_ranges:
            port = item.get("port")
            if not port:
                continue
            ports[port] = [
                {
                    "min": m.get("min_power"),
                    "max": m.get("max_power"),
                    "protocols": m.get("protocols", []),
                }
                for m in item.get("power_range_protocols_mapping", [])
            ]
        return {"ports": ports}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """创建传感器实体（并跟踪后续新增的设备）。"""
    coordinator: AnkerChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    add_new = _make_add_new_entities(
        coordinator, async_add_entities, lambda sn, dev: _build_entities(coordinator, sn, dev)
    )
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class AnkerDeviceSensor(AnkerEntity, SensorEntity):
    """设备级传感器。"""

    entity_description: AnkerSensorDescription

    def __init__(
        self,
        coordinator: AnkerChargingCoordinator,
        sn: str,
        description: AnkerSensorDescription,
    ) -> None:
        super().__init__(coordinator, sn)
        self.entity_description = description
        self._attr_unique_id = f"{sn}_{description.key}"

    @property
    def native_value(self) -> Any:
        if (data := self.device_data) is None:
            return None
        return self.entity_description.value_fn(data)


class AnkerPortPowerSensor(AnkerEntity, SensorEntity):
    """端口当前功率。"""

    def __init__(
        self, coordinator: AnkerChargingCoordinator, sn: str, port_name: str
    ) -> None:
        super().__init__(coordinator, sn)
        self._port_name = port_name
        self._attr_unique_id = f"{sn}_{port_name}_power"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = 1

    @property
    def name(self) -> str:
        port = (self.device_data.ports.get(self._port_name) if self.device_data else None)
        return f"{port.display_name if port else self._port_name} 功率"

    @property
    def native_value(self) -> float | None:
        port = (self.device_data.ports.get(self._port_name) if self.device_data else None)
        return port.current_power if port else None


class AnkerPortEnergySensor(AnkerEntity, SensorEntity):
    """端口今日输出电量。"""

    def __init__(
        self, coordinator: AnkerChargingCoordinator, sn: str, port_name: str
    ) -> None:
        super().__init__(coordinator, sn)
        self._port_name = port_name
        self._attr_unique_id = f"{sn}_{port_name}_energy"
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_suggested_display_precision = 3

    @property
    def name(self) -> str:
        port = (self.device_data.ports.get(self._port_name) if self.device_data else None)
        return f"{port.display_name if port else self._port_name} 今日电量"

    @property
    def native_value(self) -> float | None:
        port = (self.device_data.ports.get(self._port_name) if self.device_data else None)
        return port.today_energy if port else None