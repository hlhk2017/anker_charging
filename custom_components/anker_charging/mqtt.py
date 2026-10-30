"""安克设备 MQTT 实时通道（AWS IoT 双向 TLS）。

设备会向 dt/{app}/{pn}/{sn}/state_info 周期性推送明文 JSON（rssi/ssid/ip/battery 等），
向 .../param_info 推送二进制遥测帧，向 cmd/{app}/{pn}/{sn}/req 接收控制指令。
本模块做“尽力而为”的实时增强：缺少 paho-mqtt 或连接失败时静默跳过，绝不影响主流程。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Callable

from .protocol import build_envelope, decode_param_ports, parse_frame

_LOGGER = logging.getLogger(__name__)

try:  # paho 可能未安装
    import paho.mqtt.client as mqtt  # type: ignore

    _HAS_PAHO = True
except Exception:  # noqa: BLE001
    mqtt = None  # type: ignore
    _HAS_PAHO = False

PAHO_AVAILABLE = _HAS_PAHO


class AnkerMqttClient:
    """一台设备的 MQTT 实时订阅 + 控制客户端。"""

    def __init__(
        self,
        hass,
        info: dict[str, Any],
        app_name: str,
        pn: str,
        sn: str,
        on_state: Callable[[dict[str, Any]], None],
        on_param: Callable[[dict[str, Any]], None] | None = None,
        on_command: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._hass = hass
        self._info = info
        self._app = app_name or "anker_power"
        self._pn = pn
        self._sn = sn
        self._on_state = on_state
        self._on_param = on_param
        self._on_command = on_command
        self._client: Any = None
        self._loop = hass.loop
        self._msg_count = 0
        self._param_count = 0
        self._seq = 0
        self.last_param: dict[str, Any] | None = None

    def start(self) -> bool:
        if not _HAS_PAHO:
            _LOGGER.warning("未安装 paho-mqtt，跳过 MQTT 实时通道")
            return False
        try:
            import tempfile

            tmp = tempfile.mkdtemp(prefix="anker_mqtt_")
            ca = os.path.join(tmp, "ca.pem")
            crt = os.path.join(tmp, "cert.pem")
            key = os.path.join(tmp, "key.pem")
            with open(ca, "w") as f:
                f.write(self._info["aws_root_ca1_pem"])
            with open(crt, "w") as f:
                f.write(self._info["certificate_pem"])
            with open(key, "w") as f:
                f.write(self._info["private_key"])
            self._tmp = tmp

            cid = self._info.get("thing_name", "anker") + "-ha-" + self._sn[-6:]
            client = mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311, clean_session=True)
            client.tls_set(ca_certs=ca, certfile=crt, keyfile=key)
            client.tls_insecure_set(False)
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.on_disconnect = self._on_disconnect
            client.connect_async(self._info["endpoint_addr"], 8883, 60)
            client.loop_start()
            self._client = client
            _LOGGER.info("安克 MQTT 实时通道已启动 (%s)", self._info.get("endpoint_addr"))
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("启动 MQTT 失败（忽略）: %s", err)
            return False

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            base = "dt/" + self._app + "/" + self._pn + "/" + self._sn
            client.subscribe(base + "/state_info", qos=0)
            client.subscribe(base + "/param_info", qos=0)
            # 同时订阅控制指令回显，用于把 App 操作即时同步到实体状态
            client.subscribe("cmd/" + self._app + "/" + self._pn + "/" + self._sn + "/req", qos=0)
            _LOGGER.info("安克 MQTT 已连接并订阅 %s/{state_info,param_info} + cmd/req", base)
        else:
            _LOGGER.warning("安克 MQTT 连接失败 rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc, properties=None):
        _LOGGER.debug("安克 MQTT 断开 rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        self._msg_count += 1
        raw = msg.payload.decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("安克 MQTT 非 JSON 消息 %s: %s", msg.topic, raw[:200])
            return
        if not isinstance(payload, dict):
            return
        head = payload.get("head") or {}
        body = payload.get("payload")
        try:
            data = json.loads(body) if isinstance(body, str) else (body or {})
        except Exception:  # noqa: BLE001
            data = {}
        if not isinstance(data, dict):
            return

        # 控制指令回显（App/HA 下发的 cmd）：解析后即时更新实体状态
        if msg.topic.endswith("/req"):
            try:
                frame = base64.b64decode(data["data"])
                parsed = parse_frame(frame)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("cmd 帧解析失败: %s", err)
                return
            subcmd = frame[8] if len(frame) > 8 else -1
            groups = {g["tag"]: bytes.fromhex(g["value"]) for g in parsed["groups"]}
            _LOGGER.debug(
                "安克 MQTT 收到控制指令 subcmd=0x%02x groups=%s",
                subcmd & 0xFF,
                {hex(k): v.hex() for k, v in groups.items()},
            )
            if self._on_command is not None:
                try:
                    self._loop.call_soon_threadsafe(
                        self._on_command, {"subcmd": subcmd, "groups": groups}
                    )
                except Exception:  # noqa: BLE001
                    pass
            return

        if isinstance(data.get("data"), str):
            self._param_count += 1
            parsed = None
            try:
                frame = base64.b64decode(data["data"])
                parsed = parse_frame(frame)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("param 帧解析失败: %s", err)
            self.last_param = parsed
            if parsed is not None:
                summary = ",".join(
                    f"{int(g['tag']):02x}={g['value']}" for g in parsed["groups"]
                )
                if self._param_count <= 5 or self._param_count % 60 == 0:
                    _LOGGER.info(
                        "安克 MQTT param_info #%s (cmd=%s, seq=%s, ok=%s): %s",
                        self._param_count,
                        head.get("cmd"),
                        head.get("msg_seq"),
                        parsed["checksum_ok"],
                        summary[:300],
                    )
                else:
                    _LOGGER.debug("安克 MQTT param_info #%s: %s", self._param_count, summary[:300])
                if self._on_param is not None:
                    try:
                        ports = decode_param_ports(parsed)
                        self._loop.call_soon_threadsafe(
                            self._on_param, {"parsed": parsed, "ports": ports}
                        )
                    except Exception:  # noqa: BLE001
                        pass
            return

        _LOGGER.info("安克 MQTT state_info: %s", json.dumps(data, ensure_ascii=False)[:400])
        self._loop.call_soon_threadsafe(self._on_state, data)

    def publish_command(
        self,
        groups,
        *,
        header: bytes = b"\x03\x00\x0f\x02\x0b",
        sess_id: str = "1",
        msg_seq: int | None = None,
        seed: str | None = None,
    ) -> bool:
        """向 cmd/{app}/{pn}/{sn}/req 发布一条控制指令。groups=[(tag:int, bytes)]。"""
        if self._client is None:
            _LOGGER.warning("MQTT 未连接，无法发送指令")
            return False
        if msg_seq is None:
            self._seq += 1
            msg_seq = self._seq
        if seed is None:
            seed = os.urandom(16).hex()
        uid = self._info.get("user_id") or self._info.get("uid") or ""
        envelope = build_envelope(
            self._info.get("thing_name", ""),
            uid,
            groups,
            pn=self._pn,
            sn=self._sn,
            sess_id=sess_id,
            msg_seq=msg_seq,
            seed=seed,
            header=header,
        )
        topic = "cmd/" + self._app + "/" + self._pn + "/" + self._sn + "/req"
        info = self._client.publish(topic, envelope, qos=1)
        ok = info.rc == 0
        _LOGGER.info("安克 MQTT 发送指令 %s (seq=%s, rc=%s): %s", topic, msg_seq, info.rc, envelope[:200])
        return ok

    def stop(self) -> None:
        try:
            if self._client is not None:
                self._client.loop_stop()
                self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            import shutil

            shutil.rmtree(getattr(self, "_tmp", ""), ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
