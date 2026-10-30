"""安克充电（Anker Charging）Home Assistant 集成。"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.helpers.storage import Store

from .api import AnkerApiError, AnkerAuthError, AnkerChargingApi
from .const import (
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import AnkerChargingCoordinator, PortPower

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.LIGHT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立配置条目。"""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
    token = entry.data.get(CONF_TOKEN)
    api = AnkerChargingApi(
        session,
        email=entry.data.get(CONF_EMAIL, ""),
        password=entry.data.get(CONF_PASSWORD, ""),
        country=entry.data.get(CONF_COUNTRY, "CN"),
        auth_token=token or None,
        user_id=entry.data.get(CONF_USER_ID) or None,
        store=store,
    )
    try:
        await api.async_ensure_login()
    except AnkerAuthError as err:
        msg = str(err)
        # 触发登录次数上限 / 临时风控：稍后重试，而不是永久失败
        if "max login limit" in msg.lower() or "limit" in msg.lower():
            raise ConfigEntryNotReady(f"安克账号登录受限，稍后自动重试: {msg}") from err
        raise ConfigEntryAuthFailed(f"安克登录失败: {msg}") from err
    except AnkerApiError as err:
        # 405/463 等风控或网络问题：稍后重试
        raise ConfigEntryNotReady(f"安克云端暂不可用，稍后自动重试: {err}") from err
    seconds = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    coordinator = AnkerChargingCoordinator(
        hass, api, update_interval=timedelta(seconds=seconds)
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # 尽力而为：启动 MQTT 实时通道（缺少 paho 或连接失败不影响主流程）
    mqtt_clients = await _async_start_mqtt(hass, api, coordinator)
    hass.data[DOMAIN][f"{entry.entry_id}_mqtt"] = mqtt_clients
    if mqtt_clients:
        coordinator.mqtt = mqtt_clients[0]
    return True


def _make_state_cb(coordinator, sn):
    def _cb(state: dict) -> None:
        device = (coordinator.data or {}).get(sn)
        if device is None:
            return
        device.apply_mqtt_state(state)
        coordinator.async_update_listeners()

    return _cb


def _make_param_cb(coordinator, sn):
    """param_info 实时遥测：更新每端口电压/电流/功率。"""

    def _cb(payload: dict) -> None:
        if not coordinator.realtime_power_enabled:
            return
        device = (coordinator.data or {}).get(sn)
        if device is None:
            return
        ports = payload.get("ports") or {}
        if not ports:
            return
        total = 0.0
        for port_name, m in ports.items():
            p = device.ports.get(port_name)
            if p is None:
                p = PortPower(name=port_name)
                device.ports[port_name] = p
            p.current_power = m.get("power")
            p.voltage = m.get("voltage")
            p.current = m.get("current")
            p.live = True
            if isinstance(m.get("power"), (int, float)):
                total += m["power"]
        device.mqtt_state["total_power_live"] = round(total, 2)
        # 同步“当前总功率”传感器为实时求和
        tp = device.ports.get("Total")
        if tp is None:
            tp = PortPower(name="Total")
            device.ports["Total"] = tp
        tp.current_power = round(total, 2)
        tp.live = True
        coordinator.async_update_listeners()

    return _cb


def _make_command_cb(coordinator, sn):
    """控制指令回显：把 App/HA 下发的指令即时同步到实体。"""

    def _cb(payload: dict) -> None:
        if (coordinator.data or {}).get(sn) is None:
            return
        try:
            coordinator.apply_command(payload.get("subcmd", -1), payload.get("groups") or {})
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("处理控制回显失败: %s", err)

    return _cb


async def _async_start_mqtt(hass, api, coordinator) -> list:
    clients: list = []
    try:
        from .mqtt import PAHO_AVAILABLE, AnkerMqttClient

        if not PAHO_AVAILABLE:
            _LOGGER.warning("paho-mqtt 不可用，跳过 MQTT 实时通道（不影响 HTTP 轮询）")
            return clients
        info = await api.async_get_mqtt_info()
        if not info.get("endpoint_addr"):
            _LOGGER.debug("未获取到 MQTT 接入点，跳过实时通道")
            return clients
        app_name = info.get("app_name") or "anker_power"
        for sn, dev in (coordinator.data or {}).items():
            client = AnkerMqttClient(
                hass,
                info,
                app_name,
                dev.model,
                sn,
                _make_state_cb(coordinator, sn),
                on_param=_make_param_cb(coordinator, sn),
                on_command=_make_command_cb(coordinator, sn),
            )
            if await hass.async_add_executor_job(client.start):
                clients.append(client)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("初始化 MQTT 实时通道失败（忽略）: %s", err)
    return clients


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更：只调整轮询间隔，不重载集成。"""
    coordinator: AnkerChargingCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is not None:
        coordinator.set_update_interval(
            int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置条目。"""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for client in hass.data[DOMAIN].pop(f"{entry.entry_id}_mqtt", []) or []:
            try:
                client.stop()
            except Exception:  # noqa: BLE001
                pass
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """迁移旧版本配置。"""
    return True
