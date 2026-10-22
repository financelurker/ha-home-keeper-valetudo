"""End-to-end: Valetudo MQTT entities → glue → Home Keeper's real test fake."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    async_fire_time_changed,
    mock_integration,
)

from custom_components.home_keeper_valetudo.const import DOMAIN, SOURCE_NS
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

models = pytest.importorskip(
    "custom_components.home_keeper.models", reason="Home Keeper is not installed"
)
FakeHomeKeeper = pytest.importorskip(
    "custom_components.home_keeper.testing", reason="Home Keeper is not installed"
).FakeHomeKeeper

ROBOT = "valetudo_cheerfulrobot"
BRUSH = "sensor.d9_main_brush"
SENSORS = "sensor.d9_sensor_cleaning"
BRUSH_UID = f"{ROBOT}_sensor_ConsumableMonitoringCapability_brush_main"
SENSORS_UID = f"{ROBOT}_sensor_ConsumableMonitoringCapability_cleaning_sensor"
LIFE = {BRUSH: 18000, SENSORS: 1800}  # minutes, as Dreame reports them


class _Fake(FakeHomeKeeper):
    """Home Keeper's fake, merging ``source`` on ``update_task`` like the real store."""

    async def _update_task(self, call) -> None:
        source_update = call.data.get("source")
        task_id = call.data["task_id"]
        before = self.tasks[task_id].get("source")
        await super()._update_task(call)
        if source_update:
            self.tasks[task_id]["source"] = models.merge_source(
                before, source_update, reserved=("part", "buy", "declarative_companion")
            )


async def setup_hk(hass, *, state=ConfigEntryState.LOADED) -> _Fake:
    fake = _Fake(hass)
    fake.register()
    fake.entry = MockConfigEntry(domain="home_keeper", state=state)
    fake.entry.add_to_hass(hass)
    return fake


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()


async def _drain(hass: HomeAssistant) -> None:
    for _ in range(3):
        await hass.async_block_till_done()


def set_minutes(hass, entity_id, minutes) -> None:
    hass.states.async_set(
        entity_id, str(minutes), {"unit_of_measurement": "min", "device_class": "duration"}
    )


@pytest.fixture
async def robot(hass: HomeAssistant):
    """A Valetudo robot: MQTT entry, device, two consumable sensors + reset buttons."""

    async def _ok(hass, entry):
        return True

    mock_integration(hass, MockModule("mqtt", async_setup_entry=_ok, async_unload_entry=_ok))
    mqtt_entry = MockConfigEntry(domain="mqtt", title="MQTT")
    mqtt_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", ROBOT)}, name="D9"
    )
    ent_reg = er.async_get(hass)
    for entity_id, uid, name in (
        (BRUSH, BRUSH_UID, "Main Brush"),
        (SENSORS, SENSORS_UID, "Sensor Cleaning"),
    ):
        ent_reg.async_get_or_create(
            "sensor",
            "mqtt",
            uid,
            suggested_object_id=entity_id.split(".")[1],
            config_entry=mqtt_entry,
            device_id=device.id,
            original_name=name,
        )
        button = ent_reg.async_get_or_create(
            "button",
            "mqtt",
            uid.replace("_sensor_", "_button_") + "_reset",
            suggested_object_id=entity_id.split(".")[1] + "_reset",
            config_entry=mqtt_entry,
            device_id=device.id,
        )
        hass.states.async_set(button.entity_id, "unknown")
    set_minutes(hass, BRUSH, 1200)  # 6.7 % of 18000 once learned
    set_minutes(hass, SENSORS, 1000)

    pressed: list[str] = []

    async def _press(call: ServiceCall) -> None:
        # What Valetudo does: the counter goes back to full and is republished.
        for button_id in (
            call.data["entity_id"]
            if isinstance(call.data["entity_id"], list)
            else [call.data["entity_id"]]
        ):
            pressed.append(button_id)
            sensor = "sensor." + button_id.split(".")[1].removesuffix("_reset")
            set_minutes(hass, sensor, LIFE[sensor])

    hass.services.async_register("button", "press", _press)
    return mqtt_entry, device, pressed


async def _setup_glue(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _task(hk, uid=BRUSH_UID):
    return hk.get_task_by_source(SOURCE_NS, sensor_uid=uid)


async def _learn_full_life(hass):
    """Simulate one reset in Valetudo so the lifetimes are known."""
    for sensor, life in LIFE.items():
        set_minutes(hass, sensor, life)
    await hass.async_block_till_done()


async def test_wear_reset_and_done_with_stock(hass, robot):
    _, device, pressed = robot
    hk = await setup_hk(hass)
    await _setup_glue(hass)
    # First readings only teach a lifetime: 1200 min is 100 % of what was seen.
    assert _task(hk) is None

    await _learn_full_life(hass)
    # The reset just seen counts as a replacement: a task with one completion.
    brush = _task(hk)
    assert brush is not None
    assert len(brush["completions"]) == 1
    assert brush["name"] == "Replace Main brush (D9)"
    assert brush["device_id"] == device.id
    asset = next(iter(hk.assets.values()))
    part = hk.part_named(asset["id"], "Main brush")
    assert brush["source"]["part"]["part_id"] == part["id"]
    assert hk.part_named(asset["id"], "Sensors") is None  # a chore has no stock
    part["stock"] = 2

    # Wear down to 9 % → armed.
    set_minutes(hass, BRUSH, 1620)
    await hass.async_block_till_done()
    assert _task(hk)["next_due"]

    # Ticked off in Home Keeper → reset pressed, counter full, recorded once.
    hk.fire_user_completion(_task(hk)["id"])
    await _drain(hass)
    brush = _task(hk)
    assert pressed == ["button.d9_main_brush_reset"]
    assert brush["next_due"] is None
    assert len(brush["completions"]) == 2
    assert "pending_reset" not in brush["source"][SOURCE_NS]
    assert hk.part_named(asset["id"], "Main brush")["stock"] == 1


async def test_reset_in_valetudo_completes_the_armed_task(hass, robot):
    hk = await setup_hk(hass)
    await _setup_glue(hass)
    await _learn_full_life(hass)
    set_minutes(hass, BRUSH, 900)
    await hass.async_block_till_done()
    assert _task(hk)["next_due"]

    set_minutes(hass, BRUSH, 18000)  # reset in the Valetudo UI
    await hass.async_block_till_done()
    brush = _task(hk)
    assert brush["next_due"] is None
    assert len(brush["completions"]) == 2


async def test_done_while_robot_offline_waits_and_retries(hass, robot):
    _, _, pressed = robot
    hk = await setup_hk(hass)
    await _setup_glue(hass)
    await _learn_full_life(hass)
    set_minutes(hass, BRUSH, 900)
    await hass.async_block_till_done()

    # Robot goes offline, the user ticks the task off.
    hass.states.async_set(BRUSH, "unavailable")
    hass.states.async_set("button.d9_main_brush_reset", "unavailable")
    hk.fire_user_completion(_task(hk)["id"])
    await hass.async_block_till_done()
    assert pressed == []
    assert _task(hk)["source"][SOURCE_NS]["pending_reset"] is True

    # Back online with the stale low value: no re-arm, the reset is pressed now.
    hass.states.async_set("button.d9_main_brush_reset", "unknown")
    set_minutes(hass, BRUSH, 900)
    await _drain(hass)  # the press triggers a second reading, queued behind the first
    brush = _task(hk)
    assert pressed == ["button.d9_main_brush_reset"]
    assert brush["next_due"] is None
    assert "pending_reset" not in brush["source"][SOURCE_NS]
    assert len(brush["completions"]) == 2  # the learn-reset + the user's, no extra


@pytest.mark.parametrize("options", [{"reset_on_done": False}, {}])
async def test_done_without_reset_waits_for_the_reset_in_valetudo(hass, robot, options):
    _, _, pressed = robot
    if options == {}:  # the option is on, but the robot has no usable reset button
        er.async_get(hass).async_update_entity(
            "button.d9_main_brush_reset", disabled_by=er.RegistryEntryDisabler.USER
        )
    hk = await setup_hk(hass)
    await _setup_glue(hass, options)
    await _learn_full_life(hass)
    asset = next(iter(hk.assets.values()))
    hk.part_named(asset["id"], "Main brush")["stock"] = 2
    set_minutes(hass, BRUSH, 900)
    await hass.async_block_till_done()
    assert _task(hk)["next_due"]

    # Ticked off in Home Keeper; the counter is not reset, it keeps running down.
    hk.fire_user_completion(_task(hk)["id"])
    await _drain(hass)
    assert pressed == []
    for minutes in (850, 600, 0):
        set_minutes(hass, BRUSH, minutes)
        await hass.async_block_till_done()
        assert _task(hk)["next_due"] is None  # quiet until the counter is reset
    assert len(_task(hk)["completions"]) == 2

    # Reset in Valetudo later: that is the replacement already recorded.
    set_minutes(hass, BRUSH, 18000)
    await hass.async_block_till_done()
    brush = _task(hk)
    assert len(brush["completions"]) == 2
    assert "awaiting_reset" not in brush["source"][SOURCE_NS]
    assert hk.part_named(asset["id"], "Main brush")["stock"] == 1

    # The next wear cycle arms and counts as usual.
    set_minutes(hass, BRUSH, 900)
    await hass.async_block_till_done()
    assert _task(hk)["next_due"]
    set_minutes(hass, BRUSH, 18000)
    await hass.async_block_till_done()
    assert len(_task(hk)["completions"]) == 3


async def test_disabled_mqtt_entry_pauses_and_resumes(hass, robot):
    mqtt_entry, _, _ = robot
    hk = await setup_hk(hass)
    await _setup_glue(hass)
    await _learn_full_life(hass)
    task_id = _task(hk)["id"]

    await hass.config_entries.async_set_disabled_by(mqtt_entry.entry_id, ConfigEntryDisabler.USER)
    await _settle(hass)
    assert _task(hk)["id"] == task_id
    assert _task(hk)["enabled"] is False

    await hass.config_entries.async_set_disabled_by(mqtt_entry.entry_id, None)
    await _settle(hass)
    assert _task(hk)["enabled"] is True


async def test_lifetime_survives_a_restart(hass_storage, hass, robot):
    hk = await setup_hk(hass)
    entry = await _setup_glue(hass)
    await _learn_full_life(hass)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    assert hass_storage[f"{DOMAIN}.lifetimes"]["data"][BRUSH_UID] == 18000
    set_minutes(hass, BRUSH, 900)  # 5 % — known straight after the reload
    await hass.async_block_till_done()
    assert _task(hk)["next_due"]


async def test_boot_race_waits_for_home_keeper(hass, robot, caplog):
    hk = await setup_hk(hass, state=ConfigEntryState.SETUP_IN_PROGRESS)
    await _setup_glue(hass)
    await _learn_full_life(hass)
    set_minutes(hass, BRUSH, 900)
    await hass.async_block_till_done()
    assert hk.tasks == {}

    hk.entry.mock_state(hass, ConfigEntryState.LOADED)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert _task(hk)["next_due"]
    assert not [
        r
        for r in caplog.records
        if r.name.startswith("custom_components.home_keeper_valetudo") and r.levelno >= 30
    ]

