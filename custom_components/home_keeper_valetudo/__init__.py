"""Home Keeper ↔ Valetudo glue integration.

Watches the consumable sensors Valetudo publishes over MQTT and keeps one Home Keeper
``triggered`` task per consumable: armed when it is worn, completed when its counter
is reset, and the robot's counter reset when the task is ticked off in Home Keeper.
Optionally keeps a managed appliance per robot whose parts count the spares.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import logic
from .const import (
    HK_DOMAIN,
    HK_SERVICE_DELETE_ASSET,
    HK_SERVICE_DELETE_TASK,
    HK_SERVICE_LIST_ASSETS,
    HK_SERVICE_LIST_TASKS,
    HK_SERVICE_UPDATE_ASSET,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .wiring import ValetudoGlue

_LOGGER = logging.getLogger(__name__)

type ValetudoGlueConfigEntry = ConfigEntry[ValetudoGlue]


async def async_setup_entry(hass: HomeAssistant, entry: ValetudoGlueConfigEntry) -> bool:
    glue = ValetudoGlue(hass, entry)
    entry.runtime_data = glue
    await glue.async_setup()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ValetudoGlueConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ValetudoGlueConfigEntry) -> bool:
    """Listeners go through ``entry.async_on_unload``.

    The tasks stay: an unload is often a reload (an options change), and the
    replacement history must survive it. ``async_remove_entry`` cleans up for good.
    """
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ValetudoGlueConfigEntry) -> None:
    """On removal, delete our tasks and hand the spare-part appliances back."""
    await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()
    services = hass.services
    if services.has_service(HK_DOMAIN, HK_SERVICE_LIST_TASKS):
        try:
            resp = await services.async_call(
                HK_DOMAIN, HK_SERVICE_LIST_TASKS, {}, blocking=True, return_response=True
            )
            for task in logic.our_tasks(list((resp or {}).get("tasks", []))):
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_DELETE_TASK,
                    {"task_id": task["id"], "force": True},
                    blocking=True,
                )
        except Exception:
            _LOGGER.debug("Could not remove the consumable tasks", exc_info=True)

    if not services.has_service(HK_DOMAIN, HK_SERVICE_LIST_ASSETS):
        return
    try:
        resp = await services.async_call(
            HK_DOMAIN, HK_SERVICE_LIST_ASSETS, {}, blocking=True, return_response=True
        )
        for asset in logic.our_assets(list((resp or {}).get("assets", []))):
            counted = any(p.get("stock") is not None for p in asset.get("parts") or [])
            if counted:
                # The spares are still in the drawer: keep the appliance and its
                # counts, drop only our ownership (INTEGRATING.md §8, "Uninstall").
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_UPDATE_ASSET,
                    {"asset_id": asset["id"], "managed_by": None},
                    blocking=True,
                )
            elif services.has_service(HK_DOMAIN, HK_SERVICE_DELETE_ASSET):
                await services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_DELETE_ASSET,
                    {"asset_id": asset["id"], "force": True},
                    blocking=True,
                )
    except Exception:
        _LOGGER.debug("Could not release the spare-part appliances", exc_info=True)
