"""配置流程：用手机号/邮箱 + 密码登录安克账号。"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AnkerApiError, AnkerAuthError, AnkerChargingApi
from .const import (
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USER_ID,
    COUNTRY_SERVERS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

COUNTRY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value="CN", label="中国区 (CN)"),
            selector.SelectOptionDict(value="EU", label="欧洲区 (EU)"),
            selector.SelectOptionDict(value="US", label="美洲区 (US)"),
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_COUNTRY, default="CN"): COUNTRY_SELECTOR,
    }
)

STEP_TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): str,
        vol.Optional(CONF_USER_ID): str,
        vol.Optional(CONF_COUNTRY, default="CN"): COUNTRY_SELECTOR,
    }
)


class AnkerChargingConfigFlow(ConfigFlow, domain=DOMAIN):
    """配置流程。"""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AnkerChargingOptionsFlow:
        """返回选项流程。"""
        return AnkerChargingOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """选择接入方式：账号密码，或直接粘贴 App 令牌。"""
        return self.async_show_menu(
            step_id="user", menu_options=["account", "token"]
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """手机号/邮箱 + 密码登录。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = AnkerChargingApi(
                session,
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                country=user_input.get(CONF_COUNTRY, "CN"),
            )
            try:
                data = await api.async_login()
            except AnkerAuthError as err:
                _LOGGER.warning("安克登录失败: %s", err)
                errors["base"] = "invalid_auth"
            except AnkerApiError as err:
                _LOGGER.error("连接安克云失败: %s", err)
                errors["base"] = "cannot_connect"
            else:
                uid = data.get("user_id") or user_input[CONF_EMAIL]
                await self.async_set_unique_id(str(uid))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=data.get("nick_name") or user_input[CONF_EMAIL],
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_COUNTRY: user_input.get(CONF_COUNTRY, "CN"),
                    },
                )
        return self.async_show_form(
            step_id="account", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """直接用抓包得到的 App access token（x-auth-token），无需登录。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = AnkerChargingApi(
                session,
                email="",
                password="",
                country=user_input.get(CONF_COUNTRY, "CN"),
                auth_token=user_input[CONF_TOKEN].strip(),
                user_id=(user_input.get(CONF_USER_ID) or "").strip() or None,
            )
            try:
                await api.async_ensure_login()
                devices = await api.async_get_devices()
            except AnkerAuthError as err:
                _LOGGER.warning("安克令牌无效: %s", err)
                errors["base"] = "invalid_auth"
            except AnkerApiError as err:
                _LOGGER.error("连接安克云失败: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    uid = api.uid or (user_input.get(CONF_USER_ID) or "").strip() or "token"
                    await self.async_set_unique_id(str(uid))
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="安克充电 (" + str(uid)[:6] + ")",
                        data={
                            CONF_TOKEN: user_input[CONF_TOKEN].strip(),
                            CONF_USER_ID: uid,
                            CONF_COUNTRY: user_input.get(CONF_COUNTRY, "CN"),
                        },
                    )
        return self.async_show_form(
            step_id="token", data_schema=STEP_TOKEN_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry: ConfigEntry | None = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None and entry is not None:
            session = async_get_clientsession(self.hass)
            api = AnkerChargingApi(
                session,
                email=entry.data[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                country=entry.data.get(CONF_COUNTRY, "CN"),
            )
            try:
                await api.async_login()
            except AnkerAuthError:
                errors["base"] = "invalid_auth"
            except AnkerApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={
                "email": entry.data[CONF_EMAIL] if entry else ""
            },
            errors=errors,
        )

class AnkerChargingOptionsFlow(OptionsFlow):
    """选项流程：调整轮询间隔。"""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )
