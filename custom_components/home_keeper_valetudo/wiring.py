"""Home Assistant wiring for the Valetudo glue.

Finds Valetudo's consumable sensors and reset buttons in the entity registry (MQTT
autodiscovery), follows their states, and turns the pure decisions in :mod:`logic`
into Home Keeper service calls and button presses.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store

from . import logic
from .const import (
    BUTTON_UID_TEMPLATE,
    COMPANION_ICON,
    DEFAULT_RESET_MARGIN,
    DEFAULT_RESET_ON_DONE,
    DEFAULT_STOCK_ENABLED,
    DEFAULT_THRESHOLD,
    DOCS_URL,
    DOMAIN,
    HK_DOMAIN,
    HK_EVENT_REGISTER_COMPANIONS,
    HK_EVENT_TASK_COMPLETED,
    HK_SERVICE_ADD_ASSET,
    HK_SERVICE_ADD_TASK,
    HK_SERVICE_COMPLETE_TASK,
    HK_SERVICE_DELETE_TASK,
    HK_SERVICE_LIST_ASSETS,
    HK_SERVICE_LIST_TASKS,
    HK_SERVICE_REGISTER_COMPANION,
    HK_SERVICE_SET_TASK_CONSUMABLE,
    HK_SERVICE_TRIGGER_TASK,
    HK_SERVICE_UPDATE_MANAGED_ASSET,
    HK_SERVICE_UPDATE_TASK,
    OPT_EXCLUDED_ENTITIES,
    OPT_RESET_MARGIN,
    OPT_RESET_ON_DONE,
    OPT_STOCK_ENABLED,
    OPT_THRESHOLD,
    ORIGIN,
    SOURCE_NS,
    STORAGE_KEY,
    STORAGE_VERSION,
    TEXT,
    VALETUDO_PLATFORM,
)

_LOGGER = logging.getLogger(__name__)

_REGISTRY_DEBOUNCE_SECONDS = 2.0
# Home Keeper asks companions to register from inside its setup, before its services
# answer; until it is loaded the glue checks again this often, this many times.
_CATCH_UP_SECONDS = 3.0
_CATCH_UP_ATTEMPTS = 100


class HomeKeeperUnavailable(Exception):
    """Home Keeper's services exist but its entry is not loaded (boot, reload, off)."""


class ValetudoGlue:
    """Follows Valetudo's consumables and drives Home Keeper tasks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._lock = asyncio.Lock()
        self._consumables: dict[str, logic.Consumable] = {}  # entity_id → consumable
        self._prev: dict[str, float] = {}  # sensor uid → last reading (its own unit)
        self._store: Store[dict[str, float]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._lifetimes: dict[str, float] = {}  # sensor uid → learned minutes
        self._unsub_state: CALLBACK_TYPE | None = None
        self._cancel_started: CALLBACK_TYPE | None = None
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self._cancel_catch_up: CALLBACK_TYPE | None = None
        self._catch_up_attempts = 0
        self._hk_down = False

    # ── options ──────────────────────────────────────────────────────────────
    @property
    def settings(self) -> logic.Settings:
        opts = self.entry.options
        return logic.Settings(
            config_entry_id=self.entry.entry_id,
            threshold=float(opts.get(OPT_THRESHOLD, DEFAULT_THRESHOLD)),
            reset_margin=float(opts.get(OPT_RESET_MARGIN, DEFAULT_RESET_MARGIN)),
            reset_on_done=bool(opts.get(OPT_RESET_ON_DONE, DEFAULT_RESET_ON_DONE)),
            lang=logic.lang_of(self.hass.config.language),
        )

    @property
    def _stock_enabled(self) -> bool:
        return bool(self.entry.options.get(OPT_STOCK_ENABLED, DEFAULT_STOCK_ENABLED))

    @property
    def _excluded(self) -> set[str]:
        return set(self.entry.options.get(OPT_EXCLUDED_ENTITIES) or [])

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_setup(self) -> None:
        self._lifetimes = dict(await self._store.async_load() or {})
        bus = self.hass.bus
        on_unload = self.entry.async_on_unload
        on_unload(bus.async_listen(HK_EVENT_TASK_COMPLETED, self._on_hk_completed))
        on_unload(bus.async_listen(HK_EVENT_REGISTER_COMPANIONS, self._on_register_request))
        on_unload(bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_updated))
        on_unload(self._teardown)
        await self._register_companion()
        if self.hass.is_running:
            await self.async_reconcile()
        else:
            self._cancel_started = bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_started
            )

    @callback
    def _teardown(self) -> None:
        for name in ("_unsub_state", "_cancel_debounce", "_cancel_catch_up", "_cancel_started"):
            cancel = getattr(self, name)
            if cancel is not None:
                cancel()
                setattr(self, name, None)

    async def _on_started(self, _event: Event) -> None:
        self._cancel_started = None
        await self.async_reconcile()

    async def _on_register_request(self, _event: Event) -> None:
        await self._register_companion()
        if self._hk_loaded():
            await self.async_reconcile()
        else:
            self._schedule_catch_up(reset=True)

    async def _register_companion(self) -> None:
        if not self._hk_ready(HK_SERVICE_REGISTER_COMPANION):
            return
        lang = logic.lang_of(self.hass.config.language)
        try:
            await self.hass.services.async_call(
                HK_DOMAIN,
                HK_SERVICE_REGISTER_COMPANION,
                {
                    "domain": DOMAIN,
                    "name": "Valetudo",
                    "icon": COMPANION_ICON,
                    "description": TEXT[lang]["description"],
                    "config_entry_id": self.entry.entry_id,
                    "docs_url": DOCS_URL,
                    "capabilities": ["consumable_reset"],
                },
                blocking=False,
            )
        except Exception:
            _LOGGER.debug("Home Keeper companion registration failed", exc_info=True)

    # ── registry → consumables ───────────────────────────────────────────────
    def _read(
        self, entry: er.RegistryEntry, sid: logic.SensorId, button: str | None
    ) -> logic.Consumable:
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get(entry.device_id) if entry.device_id else None
        robot = (device.name_by_user or device.name) if device else None
        state = self.hass.states.get(entry.entity_id)
        paused = entry.disabled_by is not None
        consumable = logic.Consumable(
            uid=entry.unique_id,
            entity_id=entry.entity_id,
            device_id=entry.device_id,
            robot=robot or "Valetudo",
            robot_key=sid.robot_key,
            topic=sid.topic,
            name=logic.part_name(
                sid.topic, entry.name or entry.original_name or "", self.settings.lang
            ),
            reset_entity_id=button,
            paused=paused,
            lifetime=self._lifetimes.get(entry.unique_id),
        )
        if state is None or paused:
            return consumable
        return self._with_state(consumable, state.state, state.attributes)

    def _with_state(self, c: logic.Consumable, value: Any, attrs: Any) -> logic.Consumable:
        """A reading applied to *c*; a minute counter also teaches its lifetime."""
        unit = (attrs or {}).get("unit_of_measurement") or "%"
        if unit == "%":
            return replace(c, unit="%", value=logic.as_number(value))
        minutes = logic.to_minutes(value, unit)
        life = logic.learn_lifetime(self._lifetimes.get(c.uid), minutes)
        if life and life != self._lifetimes.get(c.uid):
            self._lifetimes[c.uid] = life
            # Rare (a reset, or a first reading), so saved at once: a restart right
            # after a reset must not forget the lifetime it taught.
            self.hass.async_create_task(
                self._store.async_save(dict(self._lifetimes)), eager_start=False
            )
        return replace(c, unit="min", value=minutes, lifetime=life)

    def scan(self) -> list[logic.Consumable]:
        """Every Valetudo consumable not excluded; disabled ones as paused."""
        ent_reg = er.async_get(self.hass)
        buttons = {
            e.unique_id: e
            for e in ent_reg.entities.values()
            if e.platform == VALETUDO_PLATFORM and e.domain == "button"
        }
        excluded = self._excluded
        found: list[logic.Consumable] = []
        for entry in ent_reg.entities.values():
            if entry.platform != VALETUDO_PLATFORM or entry.domain != "sensor":
                continue
            sid = logic.parse_sensor_uid(entry.unique_id)
            if sid is None or entry.entity_id in excluded:
                continue
            button = buttons.get(BUTTON_UID_TEMPLATE.format(robot=sid.robot_key, topic=sid.topic))
            button_id = button.entity_id if button and button.disabled_by is None else None
            found.append(self._read(entry, sid, button_id))
        self._consumables = {c.entity_id: c for c in found}
        self._follow_states()
        return found

    def _follow_states(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        active = [eid for eid, c in self._consumables.items() if not c.paused]
        if active:
            self._unsub_state = async_track_state_change_event(self.hass, active, self._on_state)

    @callback
    def _on_registry_updated(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        relevant = (
            entity_id in self._consumables or event.data.get("old_entity_id") in self._consumables
        )
        if not relevant and event.data.get("action") != "remove" and entity_id:
            entry = er.async_get(self.hass).async_get(entity_id)
            relevant = (
                entry is not None
                and entry.platform == VALETUDO_PLATFORM
                and "ConsumableMonitoringCapability" in (entry.unique_id or "")
            )
        if not relevant:
            return
        if self._cancel_debounce is not None:
            self._cancel_debounce()
        self._cancel_debounce = async_call_later(
            self.hass, _REGISTRY_DEBOUNCE_SECONDS, self._debounced_reconcile
        )

    async def _debounced_reconcile(self, _now: datetime) -> None:
        self._cancel_debounce = None
        await self.async_reconcile()

    # ── state → task ─────────────────────────────────────────────────────────
    async def _on_state(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        c = self._consumables.get(entity_id)
        new_state = event.data.get("new_state")
        if c is None or c.paused or new_state is None:
            return
        c = self._with_state(c, new_state.state, new_state.attributes)
        self._consumables[entity_id] = c
        prev = self._prev.get(c.uid)
        if c.value is not None:
            self._prev[c.uid] = c.value
        if not self._hk_loaded():
            self._note_down()
            return
        async with self._lock:
            try:
                tasks = await self._list_tasks()
                for action in logic.plan_reading(tasks, c, prev, self.settings):
                    await self._execute(action)
            except HomeKeeperUnavailable:
                return

    async def _on_hk_completed(self, event: Event) -> None:
        """A user ticked a task off: press the robot's reset button."""
        data = event.data
        if data.get("origin") == ORIGIN:
            return
        src = (data.get("source") or {}).get(SOURCE_NS)
        if not isinstance(src, dict) or not data.get("task_id"):
            return
        c = next((x for x in self._consumables.values() if x.uid == src.get("sensor_uid")), None)
        actions = logic.plan_user_completion(
            data["task_id"], {"source": data.get("source")}, c, self.settings
        )
        if not actions:
            return
        async with self._lock:
            try:
                for action in actions:
                    await self._execute(action)
            except HomeKeeperUnavailable:
                return

    # ── reconcile ────────────────────────────────────────────────────────────
    async def async_reconcile(self) -> None:
        if not self._hk_ready(HK_SERVICE_LIST_TASKS):
            self.scan()
            if self.hass.services.has_service(HK_DOMAIN, HK_SERVICE_LIST_TASKS):
                self._note_down()
            return
        async with self._lock:
            consumables = self.scan()
            try:
                tasks = await self._list_tasks()
                for action in logic.plan_reconcile(tasks, consumables, self._prev, self.settings):
                    await self._execute(action)
                await self._reconcile_stock(consumables)
            except HomeKeeperUnavailable:
                return
            for c in consumables:
                if c.value is not None:
                    self._prev[c.uid] = c.value
            self._note_up()

    async def _reconcile_stock(self, consumables: list[logic.Consumable]) -> None:
        if not self._hk_ready(HK_SERVICE_LIST_ASSETS):
            return
        for _ in range(2):
            assets = await self._list_assets()
            tasks = await self._list_tasks()
            actions = logic.plan_stock(
                assets, tasks, consumables, self.settings, enabled=self._stock_enabled
            )
            for action in actions:
                await self._execute(action)
            if not any(isinstance(a, logic.EnsureAsset) for a in actions):
                return

    # ── Home Keeper availability ─────────────────────────────────────────────
    def _hk_loaded(self) -> bool:
        return any(
            e.state is ConfigEntryState.LOADED
            for e in self.hass.config_entries.async_entries(HK_DOMAIN)
        )

    def _hk_ready(self, service: str) -> bool:
        return self._hk_loaded() and self.hass.services.has_service(HK_DOMAIN, service)

    def _note_down(self) -> None:
        if not self._hk_down:
            self._hk_down = True
            _LOGGER.info("Home Keeper is not loaded yet; consumable tasks catch up once it is")
        self._schedule_catch_up()

    def _note_up(self) -> None:
        self._catch_up_attempts = 0
        if self._hk_down:
            self._hk_down = False
            _LOGGER.info("Home Keeper is loaded again; consumable tasks caught up")

    @callback
    def _schedule_catch_up(self, *, reset: bool = False) -> None:
        if reset:
            self._catch_up_attempts = 0
        if self._cancel_catch_up is not None or self._catch_up_attempts >= _CATCH_UP_ATTEMPTS:
            return
        self._catch_up_attempts += 1
        self._cancel_catch_up = async_call_later(self.hass, _CATCH_UP_SECONDS, self._catch_up)

    async def _catch_up(self, _now: datetime) -> None:
        self._cancel_catch_up = None
        if not self._hk_loaded():
            self._schedule_catch_up()
            return
        await self._register_companion()
        await self.async_reconcile()

    # ── calls ────────────────────────────────────────────────────────────────
    async def _call(
        self, service: str, data: dict[str, Any], *, response: bool = False, required: bool = False
    ) -> Any:
        if not self._hk_ready(service):
            self._note_down()
            raise HomeKeeperUnavailable(service)
        try:
            return await self.hass.services.async_call(
                HK_DOMAIN, service, data, blocking=True, return_response=response
            )
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None) == "integration_not_loaded":
                self._note_down()
                raise HomeKeeperUnavailable(service) from err
            _LOGGER.warning("home_keeper.%s failed: %s", service, err)
            if required:
                raise HomeKeeperUnavailable(service) from err
            return None
        except (ValueError, KeyError) as err:
            _LOGGER.warning("home_keeper.%s failed: %s", service, err)
            if required:
                raise HomeKeeperUnavailable(service) from err
            return None

    async def _list_tasks(self) -> list[dict[str, Any]]:
        resp = await self._call(HK_SERVICE_LIST_TASKS, {}, response=True, required=True)
        if not isinstance(resp, dict) or not isinstance(resp.get("tasks"), list):
            raise HomeKeeperUnavailable(HK_SERVICE_LIST_TASKS)
        return list(resp["tasks"])

    async def _list_assets(self) -> list[dict[str, Any]]:
        resp = await self._call(HK_SERVICE_LIST_ASSETS, {}, response=True, required=True)
        if not isinstance(resp, dict) or not isinstance(resp.get("assets"), list):
            raise HomeKeeperUnavailable(HK_SERVICE_LIST_ASSETS)
        return list(resp["assets"])

    async def _press(self, button_entity_id: str) -> None:
        state = self.hass.states.get(button_entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            _LOGGER.debug(
                "Reset button %s unavailable; retried when the robot reports", button_entity_id
            )
            return
        try:
            await self.hass.services.async_call(
                "button", "press", {"entity_id": button_entity_id}, blocking=True
            )
            _LOGGER.debug("Pressed %s", button_entity_id)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not reset the consumable (%s): %s", button_entity_id, err)

    async def _execute(self, action: logic.Action | logic.StockAction) -> None:
        if isinstance(action, logic.CreateTask):
            resp = await self._call(HK_SERVICE_ADD_TASK, action.payload, response=True)
            task_id = (resp or {}).get("task_id")
            # Link the new task to its spare first, so a replacement recorded right
            # away draws the spare like every later one.
            await self._reconcile_stock(list(self._consumables.values()))
            if action.complete and task_id:
                await self._call(HK_SERVICE_COMPLETE_TASK, {"task_id": task_id, "origin": ORIGIN})
        elif isinstance(action, logic.ArmTask):
            await self._call(HK_SERVICE_TRIGGER_TASK, {"task_id": action.task_id})
            await self._call(
                HK_SERVICE_UPDATE_TASK, {"task_id": action.task_id, "notes": action.notes}
            )
        elif isinstance(action, logic.CompleteTask):
            await self._call(
                HK_SERVICE_COMPLETE_TASK, {"task_id": action.task_id, "origin": ORIGIN}
            )
        elif isinstance(action, logic.SetSource):
            await self._call(
                HK_SERVICE_UPDATE_TASK, {"task_id": action.task_id, "source": action.source}
            )
        elif isinstance(action, logic.PressReset):
            await self._press(action.button_entity_id)
        elif isinstance(action, logic.DeleteTask):
            await self._call(HK_SERVICE_DELETE_TASK, {"task_id": action.task_id, "force": True})
        elif isinstance(action, (logic.PauseTask, logic.ResumeTask)):
            await self._call(
                HK_SERVICE_UPDATE_TASK,
                {
                    "task_id": action.task_id,
                    "enabled": isinstance(action, logic.ResumeTask),
                    "source": action.source,
                },
            )
        elif isinstance(action, logic.EnsureAsset):
            await self._call(HK_SERVICE_ADD_ASSET, action.payload)
        elif isinstance(action, logic.UpdateManagedAsset):
            await self._call(
                HK_SERVICE_UPDATE_MANAGED_ASSET,
                {"asset_id": action.asset_id, "parts": action.parts},
            )
        elif isinstance(action, logic.LinkConsumable):
            await self._call(
                HK_SERVICE_SET_TASK_CONSUMABLE,
                {
                    "task_id": action.task_id,
                    "asset_id": action.asset_id,
                    "part_id": action.part_id,
                    "quantity": 1,
                },
            )
        elif isinstance(action, logic.UnlinkConsumable):
            await self._call(
                HK_SERVICE_SET_TASK_CONSUMABLE,
                {"task_id": action.task_id, "asset_id": None, "part_id": None},
            )
