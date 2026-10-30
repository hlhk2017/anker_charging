# Anker Charging 安克充电（Home Assistant 自定义集成）

对接安克（Anker）充电设备的云服务 `aiot-api-cn.anker.com.cn`，把充电器的运行状态、端口功率/电量
以及可设置的开关接入 Home Assistant。适用于 **Home Assistant 2026.10 及以上**。

本集成的通信协议是通过分析抓包文件 + 逆向安克充电 App（`com.anker.charging` v3.25.0）得到并**独立重实现**的，
不使用任何第三方 SDK。

## 安装

1. 把 `anker_charging` 目录放进 Home Assistant 配置目录的 `custom_components/` 下
   （本项目已部署到 `X:\ha-test2\custom_components\anker_charging`）。
2. 重启 Home Assistant。
3. 「设置 → 设备与服务 → 添加集成」，搜索 **安克充电 / Anker Charging**。
4. 填写 **手机号（或邮箱）**、**密码**、**区域**（中国区选 CN）。
   - 账号只开通了验证码登录时，请先在安克 App 里设置一次密码。
   - 登录成功后会把 token 缓存在配置条目里，过期会自动重新登录。

## 支持的实体

每台充电器作为一个设备，自动创建以下实体：

| 实体 | 类型 | 说明 |
| --- | --- | --- |
| 在线 | binary_sensor | 设备 WiFi 是否在线（来自设备列表 `wifi_online`） |
| 充电中 | binary_sensor | 设备是否处于充电状态 |
| 今日输出电量 | sensor | 今日总输出电量（kWh，由功率曲线积分得到） |
| 当前总功率 | sensor | 最近一个采样点的总输出功率（W） |
| WiFi 信号 | sensor | WiFi RSSI（dBm） |
| 固件版本 | sensor | 设备固件版本（诊断） |
| 充电模式 | switch | `charging_mode_status`（可写） |
| 兼容模式 | switch | `compatibility_status`（可写） |
| <端口> 功率 | sensor | 每个端口（C1–C4、A1、A2）的当前功率（W） |
| <端口> 今日电量 | sensor | 每个端口的今日输出电量（kWh）；有备注的端口会显示为 `C2 (work phone)` |

> 端口列表来源于云端返回的功率曲线（`port_name`：Total / C1 / C2 / C3 / C4 / A1 / A2）。
> 功率曲线每 10 分钟一个点，当天的数据点中 `-1` 表示该时段无数据，已自动忽略。

## 数据刷新

默认 **60 秒**轮询一次云端，依次拉取：设备列表 → 设备设置 → 端口备注 → 今日功率曲线。

## 协议要点（实现细节）

* **握手**：用区域预置 AES-128 密钥加密本地 ECDH(P-256) 公钥，POST `/openapi/oauth/key/exchange`，
  取回并用预置密钥解密服务端公钥，双方算出共享密钥，取前 16 字节作为会话 AES-128-CBC 密钥。
* **请求**：请求体 = `base64(iv(16) || AES-128-CBC(会话密钥, iv, PKCS7(JSON)))`。
* **签名**：`x-signature = HMAC-SHA256(key=签名密钥的 hex 字符串, msg="ts+once+body_b64")`；
  配合 `x-encryption-info: algo_ecdh`、`x-key-ident`、`x-request-once`、`x-request-ts`。
* **登录**：密码用内置服务端公钥做 ECDH，再 `AES-256-CBC(shared, iv=shared[:16])` 加密；
  `gtoken = md5(user_id)`，与 `x-auth-token`、`uid` 一起放在请求头。
* **注意**：中国区网关（APISIX）会对没有加密请求体的调用返回 403，因此所有请求都带加密体（即使是 `{}`）。

## 已知限制

* 实时数据依赖云端轮询（60 秒），不是推送。设备自身支持 MQTT（AWS IoT 双向 TLS，
  主题 `dt/anker_power/<型号>/<SN>/`，控制主题 `cmd/anker_power/<型号>/<SN>/req`），
  后续可加 MQTT 推送以获得秒级实时数据。
* 目前可写的设置项为「充电模式」「兼容模式」；防丢失/温度模式等接口参数尚未确认。
* 不同机型的设备设置字段可能不同，集成会自动跳过设备未返回的字段。

## 选项

「设置 → 设备与服务 → 安克充电 → 配置」可调整 **刷新间隔**（15–600 秒，默认 60 秒）。

## 诊断

「安克充电 → 下载诊断」可导出设备信息、设备设置、各端口功率/电量（密码已脱敏），便于排障。

## 验证记录（本机实测）

* HA 2026.10.1：组件正常加载，配置流程可打开，无导入错误。
* 登录链路：错误密码时服务端返回「电子邮箱或密码不正确」，集成正确提示“账号或密码错误”。
* 数据链路：用真实云端数据 + 真实实体代码跑通，单台 250W Prime Charger 生成 **20 个实体**，
  取值示例：今日输出 0.082 kWh、C1 端口 8.9 W、WiFi −37 dBm、固件 v2.1.1.6，
  端口备注生效（`C2 (work phone)`）。

## 排障记录

* **`ImportError: cannot import name 'UnitOfSignalStrength'`**（HA 2026.10 报错）
  已修复：单位改用字符串常量（dBm / W / kWh），不再依赖 `UnitOf*` 枚举，兼容各版本 HA。
  若你更新过本集成文件，请在「设置 → 设备与服务 → 安克充电」里重新加载（或重启 HA）。

## 实验室功能（充电协议开关）

集成会读取「实验室功能」状态，并为以下项创建开关实体：

* `switch.<设备>_shi_yan_shi_gong_neng` —— 实验室功能总开关
* `switch.<设备>_zhi_neng_mo_shi_<协议>` / `switch.<设备>_pu_tong_mo_shi_<协议>` —— 智能模式 / 普通模式下各充电协议（UFCS、SCP、5-11V PPS、5-16V PPS、4.5-21V PPS 等）的开关

对应云端接口：`get_protocol_status` / `set_protocol_status` / `set_mode_sub_status`。

## 会话缓存（避免频繁登录）

登录令牌会缓存到 HA 存储（`.storage/anker_charging.<entry_id>`），重启后自动复用，避免每次启动都登录（安克账号有登录次数上限）。若遇到「登录受限」，集成会以“重试”状态等待，稍后自动恢复。

## 配套卡片

已安装前端卡片到 `/config/www/anker-charging-card.js` 并注册为 Lovelace 资源（模块）。

添加方式：仪表盘 → 编辑 → 添加卡片 → 搜索「安克充电卡片」，或手动 YAML：

```yaml
type: custom:anker-charging-card
device: 250w_prime_charger      # 实体名前缀
title: 250W Prime Charger
ports: [c1, c2, c3, c4]
```
