/* 安克充电 Lovelace 卡片：https://github.com/  —— 纯前端展示 */
class AnkerChargingCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.device) throw new Error("需要配置 device（实体名前缀，如 250w_prime_charger）");
    this._config = config;
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }
  getCardSize() { return 5; }
  _eid(suffix) { return this._config.entity_prefix ? `${this._config.entity_prefix}${suffix}` : null; }
  _st(id) {
    const o = this._hass && this._hass.states[id];
    return o ? o.state : "—";
  }
  _build() {
    const d = this._config.device;
    const ports = (this._config.ports || ["c1", "c2", "c3", "c4"]).map((p) => ({ key: p, label: p.toUpperCase() }));
    this.innerHTML = `
      <ha-card header="安克充电 · ${this._config.title || "250W Prime Charger"}">
        <div class="ac-wrap">
          <div class="ac-top">
            <div class="ac-big"><span class="ac-num" id="ac-total">—</span><span class="ac-unit">W</span><div class="ac-lab">当前总功率</div></div>
            <div class="ac-big"><span class="ac-num" id="ac-energy">—</span><span class="ac-unit">kWh</span><div class="ac-lab">今日输出</div></div>
          </div>
          <div class="ac-meta" id="ac-meta"></div>
          <div class="ac-ports" id="ac-ports"></div>
        </div>
        <style>
          .ac-wrap{padding:0 16px 16px}
          .ac-top{display:flex;gap:16px;justify-content:space-around;padding:8px 0}
          .ac-big{text-align:center}
          .ac-num{font-size:28px;font-weight:600}
          .ac-unit{font-size:13px;opacity:.7;margin-left:2px}
          .ac-lab{font-size:12px;opacity:.6;margin-top:2px}
          .ac-meta{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;font-size:12px;opacity:.8;padding:4px 0 8px}
          .ac-ports{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
          .ac-port{background:var(--secondary-background-color);border-radius:8px;padding:8px;text-align:center}
          .ac-port .p{font-size:16px;font-weight:600}
          .ac-port .n{font-size:11px;opacity:.65}
          .ac-badge{padding:1px 6px;border-radius:8px;background:var(--primary-color);color:var(--text-primary-color, #fff);font-size:11px}
        </style>
      </ha-card>`;
    this._portsEl = this.querySelector("#ac-ports");
    this._portsEl.dataset.ports = JSON.stringify(ports.map((p) => ({ ...p, id: `sensor.${d}_${p.key}_gong_lu` })));
    this._built = true;
  }
  _update() {
    const d = this._config.device;
    this.querySelector("#ac-total").textContent = this._st(`sensor.${d}_total_power`);
    this.querySelector("#ac-energy").textContent = this._st(`sensor.${d}_output_energy_today`);
    const online = this._st(`binary_sensor.${d}_zai_xian`); 
    const charging = this._st(`binary_sensor.${d}_chong_dian_zhong`);
    const rssi = this._st(`sensor.${d}_wifi_signal`);
    const fw = this._st(`sensor.${d}_firmware_version`);
    this.querySelector("#ac-meta").innerHTML =
      `<span class="ac-badge" style="background:${online === "on" ? "#2e7d32" : "#9e9e9e"}">${online === "on" ? "在线" : "离线"}</span>`+
      `<span class="ac-badge" style="background:${charging === "on" ? "#1565c0" : "#9e9e9e"}">${charging === "on" ? "充电中" : "未充电"}</span>`+
      `<span>WiFi ${rssi} dBm</span><span>固件 ${fw}</span>`;
    const ports = JSON.parse(this._portsEl.dataset.ports);
    this._portsEl.innerHTML = ports.map((p) => {
      let v = this._st(p.id);
      if (v === "unknown" || v === "unavailable") v = "—";
      return `<div class="ac-port"><div class="p">${v}<span style="font-size:10px">W</span></div><div class="n">${p.label}</div></div>`;
    }).join("");
  }
}
customElements.define("anker-charging-card", AnkerChargingCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "anker-charging-card", name: "安克充电卡片", description: "显示安克充电器功率与端口状态" });
