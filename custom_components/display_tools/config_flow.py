"""Config flow for Display Tools integration."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback

from .const import DOMAIN, CONF_BASE_URL

_LOGGER = logging.getLogger(__name__)


class DisplayToolsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow handler for Display Tools integration."""
    
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle a flow initialized by the user."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors = {}

        if user_input is not None:
            # Validate URL (if provided)
            base_url = user_input.get(CONF_BASE_URL, "").strip()
            
            if base_url and not base_url.startswith(("http://", "https://")):
                errors[CONF_BASE_URL] = "invalid_url"
            else:
                return self.async_create_entry(
                    title="Display Tools",
                    data={
                        CONF_BASE_URL: base_url if base_url else None
                    }
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_BASE_URL,
                    description={
                        "suggested_value": ""
                    }
                ): str,
            }),
            errors=errors,
            description_placeholders={
                "info": "Leave empty for automatic detection (recommended)"
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return DisplayToolsOptionsFlow(config_entry)


class DisplayToolsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Display Tools."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}

        if user_input is not None:
            # Validate URL (if provided)
            base_url = user_input.get(CONF_BASE_URL, "").strip()
            
            if base_url and not base_url.startswith(("http://", "https://")):
                errors[CONF_BASE_URL] = "invalid_url"
            else:
                # Update config entry
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        CONF_BASE_URL: base_url if base_url else None
                    }
                )
                return self.async_create_entry(title="", data={})

        # Get current value
        current_base_url = self.config_entry.data.get(CONF_BASE_URL) or ""

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_BASE_URL,
                    description={
                        "suggested_value": current_base_url
                    }
                ): str,
            }),
            errors=errors,
            description_placeholders={
                "info": "Leave empty for automatic detection (recommended)"
            }
        )
