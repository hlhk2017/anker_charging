"""开关实体：充电模式、兼容模式、实验室功能（充电协议）。"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import AnkerApiError
from .const import (
    CMD_SUB_CHARGING_MODE,
    CMD_SUB_PORT_PRIORITY,
    DOMAIN,
    MODE_NAMES,
    PRIORITY_PORT_BITS,
    SETTING_SWITCHES,
)
from .coordinator import AnkerChargingCoordinator
from .entity import AnkerEntity
from .util import _make_add_new_entities

_LOGGER = logging.getLogger(__name__)


def _build_entities(coordinator, sn, device) -> list[SwitchEntity]:
    """为一台设备构造全部开关实体。"""
    entities: list[SwitchEntity] = [
        AnkerSettingSwitch(coordinator, sn, field)
        for field in SETTING_SWITCHES
        if field in device.setting
    ]
    # 实验室功能：仅保留总开关（各“智能/普通模式 协议”子开关已按用户要求移除）
    protocol = device.protocol or {}
    if protocol.get("modes"):
        entities.append(AnkerProtocolMasterSwitch(coordinator, sn))
    # 端口优先分配：C1-C4 四个互斥开关（最多同时选 2 个；打开任一即切到“端口优先分配”模式）
    entities.extend(
        AnkerPortPrioritySwitch(coordinator, sn, port)
        for port in ("C1", "C2", "C3", "C4")
    )
    # 每端口独立电源开关（C1-C4 及 USB-A）
    entities.extend(
        AnkerPortPowerSwitch(coordinator, sn, port)
        for port in ("C1", "C2", "C3", "C4", "A")
    )
    # 实时功率开关：控制是否用 MQTT param_info 实时刷新功率
    entities.append(AnkerRealtimePowerSwitch(coordinator, sn))
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AnkerChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    add_new = _make_add_new_entities(
        coordinator, async_add_entities, lambda sn, dev: _build_entities(coordinator, sn, dev)
    )
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class AnkerSettingSwitch(AnkerEntity, SwitchEntity):
    """设备设置里的开关项。"""

    def __init__(
        self, coordinator: AnkerChargingCoordinator, sn: str, field: str
    ) -> None:
        super().__init__(coordinator, sn)
        self._field = field
        self._spec = SETTING_SWITCHES[field]
        self._attr_unique_id = f"{sn}_{field}"
        self._attr_name = self._spec["name"]
        self._attr_icon = self._spec.get("icon")

    @property
    def is_on(self) -> bool:
        data = self.device_data
        if data is None:
            return False
        return bool(data.setting.get(self._field))

    async def _write(self, value: int) -> None:
        try:
            await self.coordinator.api.async_set_setting(self._sn, self._field, value)
        except AnkerApiError as err:
            raise HomeAssistantError(f"设置{self._attr_name}失败: {err}") from err
        if (data := self.device_data) is not None:
            data.setting[self._field] = value
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(0)


class AnkerProtocolMasterSwitch(AnkerEntity, SwitchEntity):
    """实验室功能总开关（关闭后内部协议开关失效）。"""

    _attr_icon = "mdi:flask-outline"

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_protocol_status"
        self._attr_name = "实验室功能"

    @property
    def is_on(self) -> bool:
        data = self.device_data
        if data is None:
            return False
        return bool((data.protocol or {}).get("protocol_status"))

    async def _write(self, enabled: bool) -> None:
        try:
            await self.coordinator.api.async_set_protocol_status(self._sn, enabled)
        except AnkerApiError as err:
            raise HomeAssistantError(f"设置实验室功能失败: {err}") from err
        if (data := self.device_data) is not None:
            data.protocol["protocol_status"] = enabled
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(False)


class AnkerProtocolItemSwitch(AnkerEntity, SwitchEntity):
    """实验室功能里，某个充电模式下单个充电协议的开关。"""

    _attr_icon = "mdi:protocol"

    def __init__(
        self,
        coordinator: AnkerChargingCoordinator,
        sn: str,
        mode: str,
        protocol_key: str,
    ) -> None:
        super().__init__(coordinator, sn)
        self._mode = mode
        self._protocol_key = protocol_key
        self._attr_unique_id = f"{sn}_protocol_{mode}_{protocol_key}"
        self._attr_name = f"{MODE_NAMES.get(mode, mode)} {protocol_key}"

    @property
    def _mode_block(self) -> dict:
        data = self.device_data
        if data is None:
            return {}
        for block in (data.protocol or {}).get("modes") or []:
            if block.get("mode") == self._mode:
                return block
        return {}

    @property
    def is_on(self) -> bool:
        for item in self._mode_block.get("protocols") or []:
            if item.get("protocol_key") == self._protocol_key:
                return bool(item.get("status"))
        return False

    async def _write(self, status: bool) -> None:
        try:
            await self.coordinator.api.async_set_mode_sub_status(
                self._sn, self._mode, self._protocol_key, status
            )
        except AnkerApiError as err:
            raise HomeAssistantError(f"设置{self._attr_name}失败: {err}") from err
        for item in self._mode_block.get("protocols") or []:
            if item.get("protocol_key") == self._protocol_key:
                item["status"] = status
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(False)

class AnkerPortPrioritySwitch(AnkerEntity, SwitchEntity):
    """端口优先分配下的端口互斥开关（C1-C4，最多同时选 2 个）。"""

    _attr_icon = "mdi:priority-high"

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str, port: str) -> None:
        super().__init__(coordinator, sn)
        self._port = port
        self._attr_unique_id = f"{sn}_priority_port_{port}"
        self._attr_name = f"端口优先 {port}"

    @property
    def is_on(self) -> bool:
        return self._port in self.coordinator.priority_ports

    async def _apply(self, ports: set[str]) -> None:
        mask = 0
        for p in ports:
            mask |= 1 << PRIORITY_PORT_BITS[p]
        ok = await self.coordinator.async_send_ctrl(CMD_SUB_PORT_PRIORITY, mask)
        if not ok:
            raise HomeAssistantError("端口优先指令发送失败（MQTT 未连接）")
        self.coordinator.priority_ports = set(ports)
        self.coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs) -> None:
        current = set(self.coordinator.priority_ports)
        if self._port in current:
            return
        if len(current) >= 2:
            raise HomeAssistantError("端口优先分配最多同时选择 2 个端口")
        current.add(self._port)
        await self._apply(current)
        # 打开任一端口优先开关即自动切到“端口优先分配”模式(2)
        if self.coordinator.charging_mode != 2:
            ok = await self.coordinator.async_send_ctrl(CMD_SUB_CHARGING_MODE, 2)
            if ok:
                self.coordinator.charging_mode = 2
                self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs) -> None:
        current = set(self.coordinator.priority_ports)
        current.discard(self._port)
        await self._apply(current)

class AnkerRealtimePowerSwitch(AnkerEntity, SwitchEntity):
    """“实时功率”开关：开启后由 MQTT param_info 实时刷新功率；关闭则仅用 HTTP 轮询。"""

    _attr_icon = "mdi:flash"

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_realtime_power"
        self._attr_name = "实时功率"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.realtime_power_enabled)

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.realtime_power_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.realtime_power_enabled = False
        self.async_write_ha_state()

class AnkerPortPowerSwitch(AnkerEntity, SwitchEntity):
    """每端口独立电源开关（C1-C4）。subcmd 0x07 控制端口输出开/关。"""

    _attr_icon = "mdi:power-socket"

    def __init__(self, coordinator: AnkerChargingCoordinator, sn: str, port: str) -> None:
        super().__init__(coordinator, sn)
        self._port = port
        self._attr_unique_id = f"{sn}_port_power_{port}"
        if port == "A":
            self._attr_name = "USB-A 输出"
            self._attr_icon = "mdi:usb-port"
        else:
            self._attr_name = f"端口 {port} 输出"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.port_power.get(self._port, True))

    async def _set(self, on: bool) -> None:
        ok = await self.coordinator.async_send_port_power(self._port, on)
        if not ok:
            raise HomeAssistantError("端口电源指令发送失败（MQTT 未连接）")
        self.coordinator.port_power[self._port] = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)
