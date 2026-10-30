"""Constants for the Anker Charging (安克充电) integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "anker_charging"

# API 区域配置：中国区 / 国际区
CONF_COUNTRY: Final = "country"
CONF_EMAIL: Final = "email"
CONF_TOKEN: Final = "auth_token"
CONF_USER_ID: Final = "user_id"

COUNTRY_SERVERS: Final[dict[str, str]] = {
    "CN": "aiot-api-cn.anker.com.cn",
    "EU": "ankerpower-api-eu.anker.com",
    "US": "ankerpower-api.anker.com",
}
COUNTRY_IDS: Final[dict[str, str]] = {"CN": "CN", "EU": "DE", "US": "US"}

APP_NAME: Final = "anker_power"
APP_VERSION: Final = "3.25.0"
OS_VERSION: Final = "29"
# 使用真实机型与稳定设备指纹，避免被云端风控判定为异常客户端（HTTP 463）
PHONE_MODEL: Final = "Redmi Note 8 Pro"
OPENUDID: Final = "9b23c596-16d0-41b6-9e35-4df118c9534d"
LANGUAGE: Final = "zh"
TIME_ZONE: Final = "Asia/Shanghai"

# 会话 ECDH 握手用的区域预置密钥（AES-128 的 hex 字符串）
PRESET_KEY_HEX: Final = "e8ad18f61bbd3fbd52d5ed12d14d3b9c"
# /passport/login 密码加密用的内置服务端公钥（P-256 非压缩点）
API_PUBLIC_KEY_HEX: Final = (
    "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a"
    "3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"
)

UPDATE_INTERVAL: Final = timedelta(seconds=60)
REQUEST_TIMEOUT: Final = 30

API_KEY_EXCHANGE: Final = "/openapi/oauth/key/exchange"
API_LOGIN: Final = "/passport/login"
API_DEVICE_LIST: Final = "/power_service/v1/app/get_relate_and_bind_devices"
API_DEVICE_SETTING: Final = "/mini_power/v1/app/setting/get_device_setting"
API_SET_CHARGING_MODE: Final = "/mini_power/v1/app/setting/set_charging_mode_status"
API_SET_COMPATIBILITY: Final = "/mini_power/v1/app/setting/set_compatibility_status"
API_PORT_REMARKS: Final = "/mini_power/v1/app/setting/get_port_remarks"
API_PROTOCOL_STATUS: Final = "/mini_power/v1/app/setting/get_protocol_status"
API_SET_PROTOCOL_STATUS: Final = "/mini_power/v1/app/setting/set_protocol_status"
API_SET_MODE_SUB_STATUS: Final = "/mini_power/v1/app/setting/set_mode_sub_status"
API_POWER_RANGE_PROTOCOLS: Final = (
    "/mini_power/v1/app/setting/get_power_range_support_protocols"
)
API_CLOCK_SCREENSAVERS: Final = "/mini_power/v1/app/style/get_clock_screensavers"
API_SET_PORT_REMARK: Final = "/mini_power/v1/app/setting/set_port_remark"
API_PORT_PROTOCOL_STATUS: Final = "/mini_power/v1/app/setting/get_port_protocol_status"
API_SET_PORT_PROTOCOL_STATUS: Final = (
    "/mini_power/v1/app/setting/set_port_protocol_status"
)

# 实验室功能里的充电模式 -> 中文名
MODE_NAMES: Final[dict[str, str]] = {"ai": "智能模式", "normal": "普通模式"}
# 端口优先分配可选的槽位数量（App 限制最多同时选 2 个端口）
PRIORITY_SLOTS: Final = 2
PORT_PRIORITY_CHOICES: Final = ["C1", "C2", "C3", "C4"]
API_DAY_POWER: Final = "/mini_power/v1/app/power/get_day_power_data"

# 设备设置里的开关字段 -> (中文名, 打开值, 设置接口)
SETTING_SWITCHES: Final[dict[str, dict]] = {
    "compatibility_status": {
        "name": "兼容模式",
        "set_path": API_SET_COMPATIBILITY,
        "icon": "mdi:power-plug",
    },
}
SETTING_SENSORS: Final[dict[str, str]] = {
    "antiloss_mode_status": "防丢失模式",
    "temperature_mode_status": "温度模式",
    "charging_device_identity_status": "充电设备识别",
}

# 端口功率数据里表示“无数据”的哨兵值
NO_DATA_VALUE: Final = -1.0
PORT_ORDER: Final = ["Total", "C1", "C2", "C3", "C4", "A1", "A2"]

# ------------------------------------------------------ MQTT 控制指令编码
# 帧头第 5 字节为子命令（见 protocol.build_frame 的 header）
CMD_SUB_CHARGING_MODE: Final = 0x06      # a2=[01, 模式值]
CMD_SUB_PORT_PRIORITY: Final = 0x0C      # a2=[01, 端口位掩码]
CMD_SUB_BRIGHTNESS: Final = 0x04         # a2=[01, 百分比 0-100]
CMD_SUB_PORT_POWER: Final = 0x07         # a2=[01, 端口索引] a3=[01, 开/关]
# 充电模式值（a2 第二字节）
CHARGING_MODES: Final[dict[int, str]] = {
    1: "智能动态分配",
    2: "端口优先分配",
    3: "双笔电模式",
    4: "涓流模式",
}
CHARGING_MODE_VALUES: Final[dict[str, int]] = {v: k for k, v in CHARGING_MODES.items()}
# 端口优先分配的端口 -> 位下标（位掩码）
PRIORITY_PORT_BITS: Final[dict[str, int]] = {"C1": 0, "C2": 1, "C3": 2, "C4": 3}
# 每端口电源开关的端口索引（subcmd 0x07 的 a2 值）
PORT_POWER_INDEX: Final[dict[str, int]] = {"C1": 0, "C2": 1, "C3": 2, "C4": 3, "A": 4}

# ------------------------------------------------------------------ 选项
CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 600
