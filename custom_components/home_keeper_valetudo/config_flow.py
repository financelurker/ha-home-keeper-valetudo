"""Config and options flow for the Valetudo glue (one instance covers every robot)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    DEFAULT_RESET_MARGIN,
    DEFAULT_RESET_ON_DONE,
    DEFAULT_STOCK_ENABLED,
    DEFAULT_THRESHOLD,
    DOMAIN,
    MANAGED_DISPLAY_NAME,
    OPT_EXCLUDED_ENTITIES,
    OPT_RESET_MARGIN,
    OPT_RESET_ON_DONE,
    OPT_STOCK_ENABLED,
    OPT_THRESHOLD,
    VALETUDO_PLATFORM,
)


class ValetudoGlueConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=MANAGED_DISPLAY_NAME, data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ValetudoGlueOptionsFlow()


def _percent(min_value: int, max_value: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=min_value,
            max=max_value,
            step=1,
            unit_of_measurement="%",
            mode=NumberSelectorMode.BOX,
        )
    )


class ValetudoGlueOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[OPT_THRESHOLD]) + float(user_input[OPT_RESET_MARGIN]) > 100:
                errors["base"] = "limits_too_high"
            else:
                return self.async_create_entry(title="", data=user_input)

        opts = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_THRESHOLD, default=opts.get(OPT_THRESHOLD, DEFAULT_THRESHOLD)
                ): _percent(1, 95),
                vol.Required(
                    OPT_RESET_MARGIN, default=opts.get(OPT_RESET_MARGIN, DEFAULT_RESET_MARGIN)
                ): _percent(5, 99),
                vol.Required(
                    OPT_RESET_ON_DONE, default=opts.get(OPT_RESET_ON_DONE, DEFAULT_RESET_ON_DONE)
                ): bool,
                vol.Optional(
                    OPT_EXCLUDED_ENTITIES,
                    default=list(opts.get(OPT_EXCLUDED_ENTITIES) or []),
                ): EntitySelector(
                    EntitySelectorConfig(
                        integration=VALETUDO_PLATFORM, domain="sensor", multiple=True
                    )
                ),
                vol.Required(
                    OPT_STOCK_ENABLED, default=opts.get(OPT_STOCK_ENABLED, DEFAULT_STOCK_ENABLED)
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
