"""Unit tests for the pure decision logic."""

from __future__ import annotations

from custom_components.home_keeper_valetudo import logic
from custom_components.home_keeper_valetudo.const import SOURCE_NS

S = logic.Settings(config_entry_id="e1", threshold=10, reset_margin=20, lang="de")
UID = "rob1_sensor_ConsumableMonitoringCapability_brush_main"
BTN = "button.rob_reset_main_brush"


def c(
    value=None, *, unit="%", lifetime=None, topic="brush_main", uid=UID, paused=False, button=BTN
):
    return logic.Consumable(
        uid=uid,
        entity_id="sensor.x",
        device_id="dev1",
        robot="D9",
        robot_key="rob1",
        topic=topic,
        name=logic.part_name(topic, "", "de"),
        unit=unit,
        value=value,
        lifetime=lifetime,
        reset_entity_id=button,
        paused=paused,
    )


def task(*, armed=False, enabled=True, pending=False, paused=False, tid="t1"):
    src = {"sensor_uid": UID, "device_id": "dev1"}
    if pending:
        src["pending_reset"] = True
    if paused:
        src["paused"] = True
    return {
        "id": tid,
        "next_due": "2026-10-11T08:00:00+00:00" if armed else None,
        "enabled": enabled,
        "source": {SOURCE_NS: src},
    }


def kinds(actions):
    return [type(a).__name__ for a in actions]


def test_parse_unique_ids():
    sid = logic.parse_sensor_uid(
        "valetudo_rob1_sensor_ConsumableMonitoringCapability_brush_side_right"
    )
    assert sid.robot_key == "valetudo_rob1"
    assert sid.topic == "brush_side_right"
    assert sid.ctype == "brush"
    assert (
        logic.parse_sensor_uid("rob1_sensor_ConsumableMonitoringCapability_cleaning_sensor").ctype
        == "cleaning"
    )
    assert logic.parse_sensor_uid("rob1_sensor_BatteryStateAttribute") is None


def test_minutes_become_percent_of_learned_lifetime():
    assert c(900, unit="min", lifetime=9000).percent == 10
    assert c(900, unit="min", lifetime=None).percent is None
    assert logic.to_minutes("2", "h") == 120
    assert logic.to_minutes("90", "min") == 90
    assert logic.learn_lifetime(9000, 18000) == 18000
    assert logic.learn_lifetime(9000, 100) == 9000


def test_low_creates_then_rearms():
    a = logic.plan_reading([], c(8), 12, S)
    assert kinds(a) == ["CreateTask"]
    assert a[0].payload["name"] == "Hauptbürste tauschen (D9)"
    assert kinds(logic.plan_reading([task()], c(8), 12, S)) == ["ArmTask"]
    assert logic.plan_reading([task(armed=True)], c(8), 9, S) == []


def test_cleaning_chores_are_named_as_cleaning():
    a = logic.plan_reading([], c(5, topic="cleaning_sensor"), None, S)
    assert a[0].payload["name"] == "Sensoren reinigen (D9)"


def test_reset_outside_home_keeper_records_a_replacement():
    assert kinds(logic.plan_reading([task(armed=True)], c(100), 6, S)) == ["CompleteTask"]
    assert kinds(logic.plan_reading([task()], c(100), 45, S)) == ["CompleteTask"]  # early swap
    a = logic.plan_reading([], c(100), 40, S)
    assert kinds(a) == ["CreateTask"]
    assert a[0].complete is True
    assert logic.plan_reading([task(armed=True)], c(12), 9, S) == []  # wobble, no reset


def test_startup_catches_a_reset_missed_while_down():
    assert kinds(logic.plan_reading([task(armed=True)], c(100), None, S)) == ["CompleteTask"]
    assert logic.plan_reading([task()], c(100), None, S) == []


def test_done_in_home_keeper_presses_reset_and_waits_for_it():
    a = logic.plan_user_completion("t1", task(), c(8), S)
    assert kinds(a) == ["SetSource", "PressReset"]
    assert a[0].source[SOURCE_NS]["pending_reset"] is True
    # The jump that follows clears the mark only — no second completion.
    a = logic.plan_reading([task(pending=True)], c(100), 8, S)
    assert kinds(a) == ["SetSource"]
    assert "pending_reset" not in a[0].source[SOURCE_NS]
    # A stale low reading (robot was offline) presses again and never re-arms.
    assert kinds(logic.plan_reading([task(pending=True)], c(8), None, S)) == ["PressReset"]


def test_no_reset_without_button_or_option():
    assert logic.plan_user_completion("t1", task(), c(8, button=None), S) == []
    off = logic.Settings(config_entry_id="e", reset_on_done=False)
    assert logic.plan_user_completion("t1", task(), c(8), off) == []


def test_pause_resume_and_user_switched_off():
    assert kinds(logic.plan_reconcile([task(armed=True)], [c(None, paused=True)], {}, S)) == [
        "PauseTask"
    ]
    a = logic.plan_reconcile([task(enabled=False, paused=True)], [c(5)], {}, S)
    assert kinds(a) == ["ResumeTask", "ArmTask"]
    assert logic.plan_reconcile([task(enabled=False)], [c(5)], {}, S) == []
    assert kinds(logic.plan_reconcile([task()], [], {}, S)) == ["DeleteTask"]


def test_stock_only_for_bought_parts():
    group = [
        c(50),
        c(
            50,
            topic="cleaning_sensor",
            uid="rob1_sensor_ConsumableMonitoringCapability_cleaning_sensor",
        ),
    ]
    a = logic.plan_stock([], [], group, S, enabled=True)
    assert kinds(a) == ["EnsureAsset"]
    assert [p["name"] for p in a[0].payload["parts"]] == ["Hauptbürste"]
