"""数据协调器：轮询安克云并汇总每台设备的状态。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AnkerApiError, AnkerChargingApi
from .const import DOMAIN, NO_DATA_VALUE, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class PortPower:
    """单个端口的功率统计。"""

    name: str
    current_power: float | None = None   # W
    today_energy: float | None = None    # kWh
    remark: str | None = None
    voltage: float | None = None         # V（MQTT 实时）
    current: float | None = None         # A（MQTT 实时）
    live: bool = False                   # 是否来自 MQTT 实时数据

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.remark})" if self.remark else self.name


@dataclass
class AnkerDeviceData:
    """一台设备的完整状态。"""

    sn: str
    info: dict[str, Any] = field(default_factory=dict)
    setting: dict[str, Any] = field(default_factory=dict)
    ports: dict[str, PortPower] = field(default_factory=dict)
    protocol: dict[str, Any] = field(default_factory=dict)
    mqtt_state: dict[str, Any] = field(default_factory=dict)
    screensavers: list[Any] = field(default_factory=list)
    power_ranges: list[Any] = field(default_factory=list)

    def apply_mqtt_state(self, state: dict[str, Any]) -> None:
        """把 MQTT state_info 的实时字段合并进设备信息。"""
        if not isinstance(state, dict):
            return
        self.mqtt_state.update(state)
        for key in ("rssi", "ssid", "bssid", "ip", "battery", "version"):
            if key in state:
                self.info[key] = state[key]

    @property
    def name(self) -> str:
        return self.info.get("alias_name") or self.info.get("device_name") or self.sn

    @property
    def model(self) -> str:
        return self.info.get("product_code") or ""

    @property
    def online(self) -> bool:
        return bool(self.info.get("wifi_online"))

    @property
    def rssi(self) -> int | None:
        try:
            return int(self.info.get("rssi"))
        except (TypeError, ValueError):
            return None

    @property
    def sw_version(self) -> str | None:
        return self.info.get("device_sw_version")

    @property
    def charging(self) -> bool:
        return bool(self.info.get("charge"))


def _port_energy(list_item: dict[str, Any]) -> tuple[float | None, float | None]:
    """从功率曲线算出当前功率(W)与今日电量(kWh)。"""
    points = list_item.get("power_points") or []
    interval = float(list_item.get("interval") or 600)
    valid = [float(p) for p in points if isinstance(p, (int, float)) and float(p) > 0]
    if not valid:
        return None, 0.0
    current = valid[-1]
    energy_wh = sum(valid) * interval / 3600
    return round(current, 1), round(energy_wh / 1000, 3)


class AnkerChargingCoordinator(DataUpdateCoordinator[dict[str, AnkerDeviceData]]):
    """账号级协调器，轮询所有设备。"""

    def __init__(
        self,
        hass: HomeAssistant,
        api: AnkerChargingApi,
        update_interval: timedelta | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} 数据更新",
            update_interval=update_interval or UPDATE_INTERVAL,
        )
        self.api = api
        self._screensaver_cache: list | None = None
        self._power_range_cache: dict[str, list] = {}
        # MQTT 控制通道与本地控制态（乐观状态）
        self.mqtt = None
        self.charging_mode: int = 1          # 1 智能 / 2 端口优先 / 3 双笔电 / 4 涓流
        self.priority_ports: set[str] = set()  # 端口优先选中的端口（最多 2）
        self.brightness_pct: int = 70          # 屏幕亮度百分比
        self.realtime_power_enabled: bool = True  # 是否启用 MQTT 实时功率解析
        self.port_power: dict[str, bool] = {p: True for p in ("C1", "C2", "C3", "C4", "A")}

    async def async_send_groups(self, subcmd: int, groups: list) -> bool:
        """下发一条 MQTT 控制指令（自定义 groups）。"""
        if self.mqtt is None:
            _LOGGER.warning("MQTT 控制通道未就绪，无法下发指令")
            return False
        import functools

        header = bytes([0x03, 0x00, 0x0F, 0x02, subcmd & 0xFF])
        try:
            return bool(
                await self.hass.async_add_executor_job(
                    functools.partial(self.mqtt.publish_command, groups, header=header)
                )
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("发送 MQTT 控制指令失败: %s", err)
            return False

    async def async_send_ctrl(self, subcmd: int, value: int) -> bool:
        """下发一条 MQTT 控制指令（子命令 + 单字节值，a2=[01,value]）。"""
        groups = [(0xA1, bytes([0x22])), (0xA2, bytes([0x01, value & 0xFF]))]
        return await self.async_send_groups(subcmd, groups)

    async def async_send_port_power(self, port: str, on: bool) -> bool:
        """每端口电源开/关：subcmd 0x07, a2=[01,端口索引], a3=[01,开/关]。"""
        from .const import CMD_SUB_PORT_POWER, PORT_POWER_INDEX

        idx = PORT_POWER_INDEX.get(port)
        if idx is None:
            return False
        groups = [
            (0xA1, bytes([0x22])),
            (0xA2, bytes([0x01, idx])),
            (0xA3, bytes([0x01, 1 if on else 0])),
        ]
        return await self.async_send_groups(CMD_SUB_PORT_POWER, groups)

    def apply_command(self, subcmd: int, groups: dict) -> None:
        """收到控制指令回显（App 或 HA），即时把设备状态同步到实体。

        groups: {tag:int -> bytes}
        """
        from .const import (
            CMD_SUB_BRIGHTNESS,
            CMD_SUB_CHARGING_MODE,
            CMD_SUB_PORT_POWER,
            CMD_SUB_PORT_PRIORITY,
            PORT_POWER_INDEX,
            PRIORITY_PORT_BITS,
        )

        a2 = groups.get(0xA2) or b""
        a3 = groups.get(0xA3) or b""
        changed = False
        if subcmd == CMD_SUB_CHARGING_MODE and len(a2) >= 2:
            if self.charging_mode != a2[1]:
                self.charging_mode = a2[1]
                changed = True
        elif subcmd == CMD_SUB_PORT_PRIORITY and len(a2) >= 2:
            mask = a2[1]
            ports = {p for p, b in PRIORITY_PORT_BITS.items() if (mask >> b) & 1}
            if ports != self.priority_ports:
                self.priority_ports = ports
                changed = True
        elif subcmd == CMD_SUB_BRIGHTNESS and len(a2) >= 2:
            if self.brightness_pct != a2[1]:
                self.brightness_pct = a2[1]
                changed = True
        elif subcmd == CMD_SUB_PORT_POWER and len(a2) >= 2 and len(a3) >= 2:
            idx = a2[1]
            on = bool(a3[1])
            rev = {v: k for k, v in PORT_POWER_INDEX.items()}
            if idx in rev and self.port_power.get(rev[idx]) != on:
                self.port_power[rev[idx]] = on
                changed = True
        if changed:
            _LOGGER.debug("控制回显同步: subcmd=0x%02x -> 状态已更新", subcmd)
            self.async_update_listeners()

    def set_update_interval(self, seconds: int) -> None:
        """动态调整轮询间隔（选项变更时调用）。"""
        self.update_interval = timedelta(seconds=seconds)

    async def _async_update_data(self) -> dict[str, AnkerDeviceData]:
        try:
            devices = await self.api.async_get_devices()
        except AnkerApiError as err:
            raise UpdateFailed(f"获取设备列表失败: {err}") from err

        result: dict[str, AnkerDeviceData] = {}
        prev = self.data or {}
        today = (dt_util.now() or datetime.now()).strftime("%Y-%m-%d")
        for info in devices:
            sn = info.get("device_sn")
            if not sn:
                continue
            # 原地复用设备对象，保证 MQTT 回调持有的引用始终有效
            data = prev.get(sn)
            if data is None:
                data = AnkerDeviceData(sn=sn)
            data.info = info
            data.ports = {}  # 端口随后由 HTTP/MQTT 重新填充
            try:
                data.setting = await self.api.async_get_device_setting(sn)
            except AnkerApiError as err:
                _LOGGER.debug("读取 %s 设置失败: %s", sn, err)
            try:
                for item in await self.api.async_get_port_remarks(sn):
                    if name := item.get("port_name"):
                        data.ports.setdefault(name, PortPower(name=name)).remark = (
                            item.get("remark")
                        )
            except AnkerApiError as err:
                _LOGGER.debug("读取 %s 端口备注失败: %s", sn, err)
            try:
                for item in await self.api.async_get_day_power(
                    sn, data.model, today
                ):
                    name = item.get("port_name")
                    if not name:
                        continue
                    port = data.ports.setdefault(name, PortPower(name=name))
                    port.current_power, port.today_energy = _port_energy(item)
            except AnkerApiError as err:
                _LOGGER.debug("读取 %s 功率数据失败: %s", sn, err)
            try:
                data.protocol = await self.api.async_get_protocol_status(sn)
            except AnkerApiError as err:
                _LOGGER.debug("读取 %s 实验室功能状态失败: %s", sn, err)
            # 屏保清单（只取一次）与端口协议支持（按型号取一次）
            if self._screensaver_cache is None:
                try:
                    resp = await self.api.async_get_clock_screensavers(
                        sn, data.model
                    )
                    self._screensaver_cache = resp or []
                except AnkerApiError as err:
                    _LOGGER.debug("读取时钟屏保失败: %s", err)
                    self._screensaver_cache = []
            data.screensavers = self._screensaver_cache or []
            if data.model and data.model not in self._power_range_cache:
                try:
                    resp = await self.api.async_get_power_range_protocols(
                        data.model
                    )
                    self._power_range_cache[data.model] = resp or []
                except AnkerApiError as err:
                    _LOGGER.debug("读取端口协议支持失败: %s", err)
                    self._power_range_cache[data.model] = []
            data.power_ranges = self._power_range_cache.get(data.model, [])
            result[sn] = data
        return result
