"""Anker Charging 云端 API 客户端。

按安克充电 App(com.anker.charging) 的传输协议独立重实现：

* 握手: 用区域预置 AES-128 密钥加密本地 ECDH(P-256) 公钥，换取服务端公钥，
  双方算出 32 字节共享密钥，取前 16 字节作为会话 AES-128-CBC 密钥。
* 请求: 请求体 = base64( iv(16) || AES-128-CBC(会话密钥, iv, PKCS7(JSON)) )
* 签名: x-signature = HMAC-SHA256(key=签名密钥的 hex 字符串, msg="ts+once+body_b64")
* 登录: 密码用内置服务端公钥做 ECDH，AES-256-CBC(shared, iv=shared[:16]) 加密。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from .const import (
    API_CLOCK_SCREENSAVERS,
    API_DAY_POWER,
    API_DEVICE_LIST,
    API_DEVICE_SETTING,
    API_KEY_EXCHANGE,
    API_LOGIN,
    API_PORT_PROTOCOL_STATUS,
    API_PORT_REMARKS,
    API_POWER_RANGE_PROTOCOLS,
    API_PROTOCOL_STATUS,
    API_PUBLIC_KEY_HEX,
    API_SET_MODE_SUB_STATUS,
    API_SET_PORT_PROTOCOL_STATUS,
    API_SET_PORT_REMARK,
    API_SET_PROTOCOL_STATUS,
    APP_NAME,
    APP_VERSION,
    COUNTRY_IDS,
    COUNTRY_SERVERS,
    LANGUAGE,
    OPENUDID,
    OS_VERSION,
    PHONE_MODEL,
    PRESET_KEY_HEX,
    REQUEST_TIMEOUT,
    SETTING_SWITCHES,
    TIME_ZONE,
)

_LOGGER = logging.getLogger(__name__)

CURVE = ec.SECP256R1()


class AnkerApiError(Exception):
    """API 调用异常。"""


class AnkerAuthError(AnkerApiError):
    """认证失败。"""


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    padder = PKCS7(block * 8).padder()
    return padder.update(data) + padder.finalize()


def _pkcs7_unpad(data: bytes, block: int = 16) -> bytes:
    unpadder = PKCS7(block * 8).unpadder()
    return unpadder.update(data) + unpadder.finalize()


def _aes_cbc(key: bytes, iv: bytes, data: bytes, encrypt: bool) -> bytes:
    """AES-CBC + PKCS7。"""
    if encrypt:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        enc = cipher.encryptor()
        return enc.update(_pkcs7_pad(data)) + enc.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor()
    return _pkcs7_unpad(dec.update(data) + dec.finalize())


def _key_from_private(private_key: ec.EllipticCurvePrivateKey, peer_hex: str) -> bytes:
    """ECDH 共享密钥。"""
    peer = ec.EllipticCurvePublicKey.from_encoded_point(
        CURVE, bytes.fromhex(peer_hex)
    )
    return private_key.exchange(ec.ECDH(), peer)


class AnkerChargingApi:
    """安克充电云端接口封装。"""

    def __init__(
        self,
        websession: aiohttp.ClientSession,
        email: str,
        password: str,
        country: str = "CN",
        auth_token: str | None = None,
        user_id: str | None = None,
        store: Any | None = None,
    ) -> None:
        self._session = websession
        self._store = store
        self._email = email
        self._password = password
        self._country = country
        self._host = COUNTRY_SERVERS.get(country, COUNTRY_SERVERS["CN"])
        self._country_id = COUNTRY_IDS.get(country, "CN")
        # 会话状态
        self._private_key = ec.generate_private_key(CURVE)
        self._key_ident = secrets.token_hex(16)
        self._session_key: bytes | None = None
        self._auth_token = auth_token
        self._uid = user_id
        self._gtoken = (
            hashlib.md5(user_id.encode()).hexdigest() if user_id else None
        )
        self._token_expires_at: float | None = None

    # ------------------------------------------------------- 会话持久化（避免重复登录）
    async def async_restore(self) -> bool:
        """从 HA 存储恢复上次登录的令牌，避免每次启动/重启都重新登录。"""
        if self._store is None:
            return False
        try:
            data = await self._store.async_load()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("读取安克会话缓存失败: %s", err)
            return False
        if not isinstance(data, dict):
            return False
        token = data.get("auth_token")
        uid = data.get("user_id")
        exp = data.get("token_expires_at")
        if not token or not uid:
            return False
        if exp and float(exp) - time.time() < 3600:
            return False
        self._auth_token = token
        self._uid = uid
        self._gtoken = hashlib.md5(str(uid).encode()).hexdigest()
        self._token_expires_at = float(exp) if exp else None
        _LOGGER.debug("已恢复安克登录会话，跳过登录")
        return True

    async def async_save(self) -> None:
        """把登录令牌写入 HA 存储，供下次启动复用。"""
        if self._store is None:
            return
        try:
            await self._store.async_save(
                {
                    "auth_token": self._auth_token,
                    "user_id": self._uid,
                    "token_expires_at": self._token_expires_at,
                }
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("保存安克会话缓存失败: %s", err)

    async def async_ensure_login(self) -> None:
        """优先复用已有/缓存令牌，仅在完全无令牌时才登录（降低触发登录上限的风险）。"""
        # 若外部直接提供了令牌（令牌配置模式），直接使用，绝不登录
        if self._auth_token and self._uid:
            return
        if await self.async_restore():
            return
        await self.async_login()

    # ---------------------------------------------------------------- 基础工具
    @property
    def uid(self) -> str | None:
        return self._uid

    @property
    def host(self) -> str:
        return self._host

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "app-name": APP_NAME,
            "model-type": "PHONE",
            "model_type": "PHONE",
            "app-version": APP_VERSION,
            "app_version": APP_VERSION,
            "os-type": "android",
            "os_type": "android",
            "os-version": OS_VERSION,
            "os_version": OS_VERSION,
            "phone_model": PHONE_MODEL,
            "phone-model": PHONE_MODEL,
            "country": self._country,
            "timezone": TIME_ZONE,
            "language": LANGUAGE,
            "openudid": OPENUDID,
            "accept": "application/json",
            "user-agent": "ktor-client",
            "content-type": "application/json",
        }
        if self._auth_token:
            headers["x-auth-token"] = self._auth_token
        if self._uid:
            headers["uid"] = self._uid
        if self._gtoken:
            headers["gtoken"] = self._gtoken
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _sign(sign_key_hex: str, ts: str, once: str, body_b64: str) -> str:
        msg = f"{ts}+{once}+{body_b64}".encode()
        return hmac.new(sign_key_hex.encode(), msg, hashlib.sha256).hexdigest()

    def _signed_headers(
        self, sign_key_hex: str, body_b64: str, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        ts = str(int(time.time()))
        once = secrets.token_hex(16)
        headers = {
            "x-encryption-info": "algo_ecdh",
            "x-replay-info": "replay",
            "x-app-key": "",
            "x-key-ident": self._key_ident,
            "x-request-once": once,
            "x-request-ts": ts,
            "x-signature": self._sign(sign_key_hex, ts, once, body_b64),
        }
        if extra:
            headers.update(extra)
        return headers

    # ------------------------------------------------------------------ 预置密钥
    @staticmethod
    def _preset_encrypt(plain: str) -> str:
        preset = bytes.fromhex(PRESET_KEY_HEX)
        iv = secrets.token_bytes(16)
        return base64.b64encode(
            iv + _aes_cbc(preset, iv, plain.encode(), True)
        ).decode()

    @staticmethod
    def _preset_decrypt(blob_b64: str) -> bytes:
        preset = bytes.fromhex(PRESET_KEY_HEX)
        blob = base64.b64decode(blob_b64)
        return _aes_cbc(preset, blob[:16], blob[16:], False)

    # -------------------------------------------------------------------- 网络层
    async def _post(
        self, path: str, headers: dict[str, str], body: str | None
    ) -> dict[str, Any]:
        url = f"https://{self._host}{path}"
        data = body.encode() if body is not None else b""
        headers = dict(headers)
        headers["content-length"] = str(len(data))
        try:
            async with self._session.post(
                url, headers=headers, data=data, timeout=REQUEST_TIMEOUT
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise AnkerApiError(
                        f"HTTP {resp.status} {path}: {text[:200]}"
                    )
        except aiohttp.ClientError as err:
            raise AnkerApiError(f"网络错误 {path}: {err}") from err
        try:
            return json.loads(text)
        except ValueError as err:
            raise AnkerApiError(f"响应非 JSON {path}: {text[:200]}") from err

    # ------------------------------------------------------------------ 握手/登录
    async def async_handshake(self) -> None:
        """用 ECDH 协商会话密钥。"""
        local_pub = self._private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        ).hex()
        wrapped = self._preset_encrypt(local_pub)
        body = json.dumps({"client_public_key": wrapped}, separators=(",", ":"))
        headers = self._headers(
            self._signed_headers(
                PRESET_KEY_HEX, wrapped, {"x-auth-ts": str(int(time.time()))}
            )
        )
        result = await self._post(API_KEY_EXCHANGE, headers, body)
        server_pub = (result.get("data") or {}).get("server_public_key")
        if not server_pub:
            raise AnkerAuthError(f"密钥交换失败: {str(result)[:200]}")
        shared = _key_from_private(
            self._private_key, self._preset_decrypt(server_pub).decode()
        )
        self._session_key = shared[:16]
        _LOGGER.debug("安克会话密钥协商成功 (%s)", self._host)

    async def async_login(self) -> dict[str, Any]:
        """用手机号/邮箱 + 密码登录。"""
        # 密码用内置服务端公钥做 ECDH，再 AES-256-CBC 加密
        temp_key = ec.generate_private_key(CURVE)
        shared = _key_from_private(temp_key, API_PUBLIC_KEY_HEX)
        iv = shared[:16]
        encrypted_pwd = base64.b64encode(
            _aes_cbc(shared, iv, self._password.encode(), True)
        ).decode()
        temp_pub = temp_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        ).hex()
        now = time.localtime()
        body = json.dumps(
            {
                "ab": self._country_id,
                "client_secret_info": {"public_key": temp_pub},
                "enc": 0,
                "email": self._email,
                "password": encrypted_pwd,
                "time_zone": int(time.timezone * -1000),
                "transaction": str(int(time.time() * 1000)),
            },
            separators=(",", ":"),
        )
        result = await self._post(API_LOGIN, self._headers(), body)
        data = result.get("data") or {}
        token = data.get("auth_token")
        uid = data.get("user_id")
        if not token or not uid:
            raise AnkerAuthError(
                f"登录失败: {result.get('msg') or str(result)[:200]}"
            )
        self._auth_token = token
        self._uid = uid
        self._gtoken = hashlib.md5(uid.encode()).hexdigest()
        if expires := data.get("token_expires_at"):
            try:
                self._token_expires_at = float(expires)
            except (TypeError, ValueError):
                self._token_expires_at = None
        await self.async_save()
        return data

    async def _ensure_session(self) -> None:
        if self._session_key is None:
            await self.async_handshake()

    async def _ensure_token(self) -> None:
        if not self._auth_token or not self._uid:
            await self.async_login()
        elif (
            self._token_expires_at
            and self._token_expires_at - time.time() < 300
        ):
            _LOGGER.debug("安克 token 即将过期，重新登录")
            await self.async_login()

    # -------------------------------------------------------------------- 业务层
    async def async_request(self, path: str, payload: dict | None = None) -> Any:
        """带加密与签名的业务请求，返回解密后的 data。"""
        try:
            return await self._async_request_once(path, payload)
        except AnkerApiError as err:
            # 网关/token 失效时重新登录一次再重试（token 有效期约 7 天）
            if "HTTP 401" not in str(err) and "HTTP 403" not in str(err):
                raise
            _LOGGER.debug("安克 token 失效（%s），重新登录后重试", err)
            self._auth_token = None
            self._uid = None
            self._session_key = None
            self._private_key = ec.generate_private_key(CURVE)
            return await self._async_request_once(path, payload)

    async def _async_request_once(
        self, path: str, payload: dict | None = None
    ) -> Any:
        """单次加密请求。"""
        await self._ensure_session()
        await self._ensure_token()
        assert self._session_key is not None
        iv = secrets.token_bytes(16)
        plain = json.dumps(payload or {}, separators=(",", ":")).encode()
        body = base64.b64encode(
            iv + _aes_cbc(self._session_key, iv, plain, True)
        ).decode()
        headers = self._headers(
            self._signed_headers(self._session_key.hex(), body)
        )
        result = await self._post(path, headers, body)
        data = result.get("data")
        if isinstance(data, str) and data:
            blob = base64.b64decode(data)
            try:
                return json.loads(
                    _aes_cbc(self._session_key, blob[:16], blob[16:], False)
                )
            except Exception as err:  # noqa: BLE001
                raise AnkerApiError(f"响应解密失败 {path}: {err}") from err
        return result

    async def async_get_devices(self) -> list[dict[str, Any]]:
        data = await self.async_request(API_DEVICE_LIST, {})
        if isinstance(data, dict):
            return data.get("data") or data.get("device_list") or []
        if isinstance(data, list):
            return data
        return []

    async def async_get_device_setting(self, sn: str) -> dict[str, Any]:
        data = await self.async_request(API_DEVICE_SETTING, {"device_sn": sn})
        return (data or {}).get("device_setting") or {}

    async def async_get_port_remarks(self, sn: str) -> list[dict[str, Any]]:
        data = await self.async_request(API_PORT_REMARKS, {"device_sn": sn})
        return (data or {}).get("port_remarks") or []

    async def async_get_day_power(
        self, sn: str, model: str, date: str
    ) -> list[dict[str, Any]]:
        data = await self.async_request(
            API_DAY_POWER,
            {
                "device_sn": sn,
                "device_model": model,
                "date": date,
                "day_type": 1,
            },
        )
        return (data or {}).get("output_power_list") or []

    async def async_set_setting(self, sn: str, field: str, value: int) -> None:
        """写入设备设置开关（charging_mode_status / compatibility_status）。"""
        spec = SETTING_SWITCHES.get(field)
        if spec is None:
            raise AnkerApiError(f"不支持的设置项: {field}")
        await self.async_request(
            spec["set_path"], {"device_sn": sn, field: int(value)}
        )

    async def async_get_mqtt_info(self) -> dict[str, Any]:
        """获取本账号的 MQTT 证书与接入点（AWS IoT 双向 TLS）。"""
        data = await self.async_request(
            "/v1/openapi/devicemanage/get_user_mqtt_info", {}
        )
        return (data or {}) if isinstance(data, dict) else {}

    # ------------------------------------------------ 实验室功能（充电协议）
    async def async_get_protocol_status(self, sn: str) -> dict[str, Any]:
        """读取实验室功能状态：总开关 + 两种充电模式下各协议的开关。

        返回形如::

            {"protocol_status": False,
             "modes": [{"mode": "ai",
                        "protocols": [{"protocol_key": "UFCS", "status": True}, ...]},
                       ...]}
        """
        data = await self.async_request(API_PROTOCOL_STATUS, {"device_sn": sn})
        return data or {}

    async def async_set_protocol_status(self, sn: str, enabled: bool) -> None:
        """实验室功能总开关。"""
        await self.async_request(
            API_SET_PROTOCOL_STATUS,
            {"device_sn": sn, "protocol_status": bool(enabled)},
        )

    async def async_set_mode_sub_status(
        self, sn: str, mode: str, protocol_key: str, status: bool
    ) -> None:
        """开关某个充电模式下的单个充电协议。"""
        await self.async_request(
            API_SET_MODE_SUB_STATUS,
            {
                "device_sn": sn,
                "mode": mode,
                "protocol_key": protocol_key,
                "status": bool(status),
            },
        )

    async def async_get_power_range_protocols(
        self, device_model: str
    ) -> list[dict[str, Any]]:
        """读取每个端口的功率区间 -> 支持协议映射。"""
        data = await self.async_request(
            API_POWER_RANGE_PROTOCOLS, {"device_model": device_model}
        )
        return (data or {}).get("port_list") or []

    # ------------------------------------------------ 屏幕 / 屏保
    async def async_get_clock_screensavers(
        self, sn: str, product_code: str
    ) -> list[dict[str, Any]]:
        """读取可用的时钟屏保主题（按分类）。"""
        data = await self.async_request(
            API_CLOCK_SCREENSAVERS, {"device_sn": sn, "product_code": product_code}
        )
        return (data or {}).get("category") or []

    # ------------------------------------------------ 端口
    async def async_set_port_remark(
        self, sn: str, port_name: str, remark: str
    ) -> None:
        """设置端口备注名。"""
        await self.async_request(
            API_SET_PORT_REMARK,
            {"device_sn": sn, "port_name": port_name, "remark": remark},
        )

    async def async_get_port_protocol_status(
        self, sn: str, version: str = "1"
    ) -> dict[str, Any]:
        """读取端口协议优先级状态（部分机型不支持，失败时返回空）。"""
        try:
            data = await self.async_request(
                API_PORT_PROTOCOL_STATUS, {"device_sn": sn, "version": version}
            )
        except AnkerApiError as err:
            _LOGGER.debug("读取 %s 端口协议状态失败: %s", sn, err)
            return {}
        return data or {}

    async def async_set_port_protocol_status(
        self,
        sn: str,
        port_list: list[dict[str, Any]],
        type_: str = "on",
        version: str = "1",
    ) -> None:
        """设置端口协议优先级（port_list 每项含 title / ports / protocols / tlv）。"""
        await self.async_request(
            API_SET_PORT_PROTOCOL_STATUS,
            {
                "device_sn": sn,
                "type": type_,
                "version": version,
                "port_list": port_list,
            },
        )
