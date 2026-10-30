"""Anker A2345 MQTT 协议帧编解码（纯本地，无副作用）。"""
from __future__ import annotations
import base64
import json
import os
import time
from typing import Iterable, Sequence


def xor8(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


def parse_frame(frame: bytes) -> dict:
    """解析 ff09 帧，返回分组列表。同时适用于 cmd 帧与 param 帧。"""
    if len(frame) < 6 or frame[0] != 0xFF or frame[1] != 0x09:
        raise ValueError("bad magic: " + frame[:4].hex())
    length = int.from_bytes(frame[2:4], "little")
    body = frame[:-1]
    checksum = frame[-1]
    groups = []
    i = 4
    while i < len(body):
        tag = body[i]
        if tag == 0xFE:
            break
        if 0xA0 <= tag <= 0xAF and i + 1 < len(body):
            gl = body[i + 1]
            val = body[i + 2 : i + 2 + gl]
            groups.append({"tag": tag, "len": gl, "value": val.hex()})
            i += 2 + gl
        else:
            i += 1
    return {
        "length": length,
        "total_len": len(frame),
        "groups": groups,
        "tail": body[i:].hex(),
        "checksum_ok": xor8(body) == checksum,
    }


# param_info 中 a2..a7 依次对应端口
PARAM_PORT_ORDER = ("C1", "C2", "C3", "C4", "A1", "A2")


def decode_param_ports(parsed: dict) -> dict[str, dict]:
    """把 param 帧 a2..a7 解码为每端口 {voltage, current, power}。

    每个 8 字节分组格式：[0x04][flag][u16LE 电压mV][u16LE 电流mA][u16LE 功率0.01W]。
    """
    groups = {g["tag"]: bytes.fromhex(g["value"]) for g in parsed.get("groups", [])}
    out: dict[str, dict] = {}
    for idx, port in enumerate(PARAM_PORT_ORDER):
        b = groups.get(0xA2 + idx)
        if not b or len(b) < 8:
            continue
        mv = int.from_bytes(b[2:4], "little")
        ma = int.from_bytes(b[4:6], "little")
        w = int.from_bytes(b[6:8], "little")
        out[port] = {
            "voltage": round(mv / 1000, 2),
            "current": round(ma / 1000, 3),
            "power": round(w / 100, 2),
        }
    return out


def build_groups(groups: Iterable[Sequence]) -> bytes:
    out = bytearray()
    for tag, val in groups:
        if isinstance(val, int):
            val = val.to_bytes(1, "little")
        out.append(tag)
        out.append(len(val))
        out.extend(val)
    return bytes(out)


def build_frame(groups, header: bytes = b"\x03\x00\x0f\x02\x0b", ts: int | None = None) -> bytes:
    """构造 cmd/param 帧（含 XOR 校验）。header 默认取 query 帧头。"""
    if ts is None:
        ts = int(time.time())
    body = build_groups(groups)
    tail = b"\xfe\x05\x03" + ts.to_bytes(4, "little")
    inner = header + body + tail
    total_len = 2 + 2 + len(inner) + 1
    frame = bytes([0xFF, 0x09]) + total_len.to_bytes(2, "little") + inner
    return frame + bytes([xor8(frame)])


def build_envelope(
    thing_name: str,
    uid: str,
    groups,
    *,
    pn: str,
    sn: str,
    sess_id: str = "1",
    msg_seq: int = 1,
    seed: str = "1",
    cmd: int = 17,
    header: bytes = b"\x03\x00\x0f\x02\x0b",
    ts: int | None = None,
    client_id: str | None = None,
) -> str:
    """构造发往 cmd/.../req 的 JSON 文本。"""
    if ts is None:
        ts = int(time.time())
    frame = build_frame(groups, header=header, ts=ts)
    if client_id is None:
        client_id = "android-anker_power-" + uid + "-" + os.urandom(16).hex()
    payload = json.dumps(
        {"device_sn": sn, "account_id": uid, "data": base64.b64encode(frame).decode()},
        separators=(",", ":"),
    )
    head = {
        "version": "1.0.0.1",
        "client_id": client_id,
        "sess_id": sess_id,
        "msg_seq": msg_seq,
        "cmd": cmd,
        "cmd_status": 2,
        "sign_code": 1,
        "seed": seed,
        "timestamp": ts,
        "device_pn": pn,
        "device_sn": sn,
    }
    return json.dumps({"head": head, "payload": payload}, separators=(",", ":"))
