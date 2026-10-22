"""Keys that existing installations depend on must not change.

The glue finds its tasks and appliances again through the keys it writes into Home
Keeper's ``source``, keyed on the Valetudo consumable sensor's unique id. Changing any of
these, the domain or an option key would make an update lose the tasks (and their
replacement history) and the spare-part counts.
"""

from __future__ import annotations

from custom_components.home_keeper_valetudo import const, logic


def test_domain_and_namespaces() -> None:
    assert const.DOMAIN == "home_keeper_valetudo"
    assert const.SOURCE_NS == "home_keeper_valetudo"
    assert const.ORIGIN == "home_keeper_valetudo"
    assert const.ASSET_ROLE == "robot_spares"
    assert const.STORAGE_KEY == "home_keeper_valetudo.lifetimes"


def test_option_keys() -> None:
    assert {
        const.OPT_THRESHOLD,
        const.OPT_RESET_MARGIN,
        const.OPT_RESET_ON_DONE,
        const.OPT_EXCLUDED_ENTITIES,
        const.OPT_STOCK_ENABLED,
    } == {"threshold", "reset_margin", "reset_on_done", "excluded_entities", "stock_enabled"}


def test_source_keys() -> None:
    uid = "valetudo_rob1_sensor_ConsumableMonitoringCapability_brush_main"
    sid = logic.parse_sensor_uid(uid)
    assert sid is not None
    assert (sid.robot_key, sid.topic) == ("valetudo_rob1", "brush_main")
    brush = logic.Consumable(
        uid=uid,
        entity_id="sensor.d9_main_brush",
        device_id="dev",
        robot="D9",
        robot_key="valetudo_rob1",
        topic="brush_main",
        name="Main brush",
    )
    settings = logic.Settings(config_entry_id="entry")
    task = logic.build_add_task_payload(brush, settings)
    assert task["source"] == {"home_keeper_valetudo": {"sensor_uid": uid, "device_id": "dev"}}
    asset = logic.build_asset_payload("valetudo_rob1", [brush], settings)
    assert asset["source"] == {
        "home_keeper_valetudo": {
            "role": "robot_spares",
            "robot_key": "valetudo_rob1",
            "device_id": "dev",
        }
    }
    # Marks the glue keeps on its tasks across restarts.
    marked = logic._ns_with(None, brush, paused=True, pending=True)["home_keeper_valetudo"]
    assert marked["paused"] is True
    assert marked["pending_reset"] is True
