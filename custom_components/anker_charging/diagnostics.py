"""诊断信息导出（设置 → 设备与服务 → 安克充电 → 下载诊断）。"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_EMAIL, DOMAIN
from .coordinator import AnkerChargingCoordinator

REDACT = {CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """返回排障用的数据结构（密码已脱敏）。"""
    coordinator: AnkerChargingCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    devices: dict[str, Any] = {}
    if coordinator and coordinator.data:
        for sn, data in coordinator.data.items():
            devices[sn] = {
                "info": data.info,
                "setting": data.setting,
                "ports": {
                    name: {
                        "current_power_w": port.current_power,
                        "today_energy_kwh": port.today_energy,
                        "remark": port.remark,
                    }
                    for name, port in data.ports.items()
                },
            }
    return {
        "entry": {
            "title": entry.title,
            "data": {
                key: ("**REDACTED**" if key in REDACT else value)
                for key, value in entry.data.items()
            },
            "options": dict(entry.options),
        },
        "account": {
            "email": entry.data.get(CONF_EMAIL),
            "uid": getattr(coordinator.api, "uid", None) if coordinator else None,
            "host": getattr(coordinator.api, "host", None) if coordinator else None,
        },
        "last_update_success": coordinator.last_update_success if coordinator else None,
        "devices": devices,
    }
