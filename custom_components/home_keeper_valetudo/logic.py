"""Pure decision logic for the Valetudo glue.

Nothing here touches Home Assistant. The wiring reads the registries and states into
:class:`Consumable` records and executes the actions that come back.

Per consumable (one Valetudo ``ConsumableMonitoringCapability`` sensor):

* **Remaining life in percent.** Robots report percent or minutes. Minutes are turned
  into percent of a *learned* lifetime: the highest value the counter has shown,
  which is its full life right after a reset. Until the first reset that is the
  highest value seen so far, so the first task can come a little late.
* **At or below the low limit** → the task is armed (created on first sight).
* **A jump up of at least the reset margin** → the counter was reset: a replacement
  is recorded (the task is completed — armed or not, created if it did not exist yet).
* **Ticked off in Home Keeper** → the glue presses the robot's reset button and marks
  the task *pending reset*. The jump that follows only clears the mark, so the
  replacement is not recorded twice. While pending, a stale low reading never re-arms
  the task; when the robot was offline, the press is repeated once it reports again.
* **Ticked off without a reset** (option off, or no usable reset button) while the
  counter is still low → the task is marked *awaiting reset*: it stays quiet (no
  re-arm) until the counter is reset in Valetudo, and that jump only clears the mark.
* **Disabled sensor** (MQTT entry, robot device or entity) → the task is paused, and
  resumed when it comes back. A task the user switched off is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .const import (
    ASSET_ICON,
    ASSET_LOCKED_FIELDS,
    ASSET_ROLE,
    CONSUMABLE_TYPES,
    HK_SOURCE_PART,
    LOCKED_FIELDS,
    MANAGED_DISPLAY_NAME,
    MANAGED_ICON,
    MINUTES_PER_UNIT,
    PART_NAMES,
    PART_TYPE_CONSUMABLE,
    SENSOR_UID_RE,
    SOURCE_NS,
    STOCKED_TYPES,
    TEXT,
)


# ── inputs ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SensorId:
    robot_key: str  # Valetudo identifier of the robot
    topic: str  # e.g. "brush_main", "cleaning_sensor"

    @property
    def ctype(self) -> str:
        head = self.topic.split("_", 1)[0]
        return head if head in CONSUMABLE_TYPES else "cleaning"


def parse_sensor_uid(unique_id: str | None) -> SensorId | None:
    """The robot and consumable of a Valetudo consumable sensor, else ``None``."""
    match = re.match(SENSOR_UID_RE, unique_id or "")
    if not match:
        return None
    return SensorId(match["robot"], match["topic"])


@dataclass(frozen=True)
class Consumable:
    uid: str  # sensor registry unique_id
    entity_id: str
    device_id: str | None
    robot: str  # robot display name
    robot_key: str
    topic: str
    name: str  # display name of the part
    unit: str = "%"  # "%" or "min" (values below are converted to minutes)
    value: float | None = None  # percent, or minutes remaining
    lifetime: float | None = None  # learned full life in minutes (min unit only)
    reset_entity_id: str | None = None
    paused: bool = False

    @property
    def ctype(self) -> str:
        return SensorId(self.robot_key, self.topic).ctype

    @property
    def is_chore(self) -> bool:
        return self.ctype == "cleaning"

    @property
    def percent(self) -> float | None:
        return self.percent_of(self.value)

    def percent_of(self, value: float | None) -> float | None:
        """*value* (in this consumable's unit) as percent of its life *now*.

        An earlier reading must be measured against the current lifetime: a reset can
        raise the learned lifetime, and the old reading measured against the old one
        would hide the jump.
        """
        if value is None:
            return None
        if self.unit == "%":
            return max(0.0, min(100.0, value))
        life = self.lifetime or 0
        if life <= 0:
            return None
        return max(0.0, min(100.0, value / life * 100))


@dataclass(frozen=True)
class Settings:
    config_entry_id: str
    threshold: float = 10
    reset_margin: float = 20
    reset_on_done: bool = True
    lang: str = "en"


# ── actions ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CreateTask:
    """``add_task`` (born armed); with ``complete`` it records a replacement at once."""

    uid: str
    payload: dict[str, Any]
    complete: bool = False


@dataclass(frozen=True)
class ArmTask:
    task_id: str
    uid: str
    notes: str


@dataclass(frozen=True)
class CompleteTask:
    """``complete_task`` with our origin: the counter was reset."""

    task_id: str
    uid: str


@dataclass(frozen=True)
class SetSource:
    """``update_task`` with only our ``source`` namespace (pending / awaiting marks)."""

    task_id: str
    uid: str
    source: dict[str, Any]


@dataclass(frozen=True)
class PressReset:
    uid: str
    button_entity_id: str


@dataclass(frozen=True)
class DeleteTask:
    task_id: str
    uid: str


@dataclass(frozen=True)
class PauseTask:
    task_id: str
    uid: str
    source: dict[str, Any]


@dataclass(frozen=True)
class ResumeTask:
    task_id: str
    uid: str
    source: dict[str, Any]


Action = (
    CreateTask
    | ArmTask
    | CompleteTask
    | SetSource
    | PressReset
    | DeleteTask
    | PauseTask
    | ResumeTask
)


@dataclass(frozen=True)
class EnsureAsset:
    robot_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class UpdateManagedAsset:
    asset_id: str
    parts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class LinkConsumable:
    task_id: str
    asset_id: str
    part_id: str


@dataclass(frozen=True)
class UnlinkConsumable:
    task_id: str


StockAction = EnsureAsset | UpdateManagedAsset | LinkConsumable | UnlinkConsumable


# ── helpers ──────────────────────────────────────────────────────────────────
def lang_of(language: str | None) -> str:
    base = (language or "en").split("-")[0].split("_")[0].lower()
    return base if base in TEXT else "en"


def _t(lang: str, key: str) -> str:
    return TEXT.get(lang, TEXT["en"]).get(key) or TEXT["en"][key]


def as_number(value: Any) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    if number != number or number < 0:
        return None
    return number


def to_minutes(value: Any, unit: str | None) -> float | None:
    """A duration reading in minutes, whatever display unit the user picked."""
    number = as_number(value)
    factor = MINUTES_PER_UNIT.get((unit or "min").strip())
    if number is None or factor is None:
        return None
    return number * factor


def learn_lifetime(stored: float | None, minutes: float | None) -> float | None:
    """The full life of a minute counter: the highest value it has shown."""
    if minutes is None:
        return stored
    return max(stored or 0.0, minutes)


def part_name(topic: str, fallback: str, lang: str) -> str:
    names = PART_NAMES.get(topic)
    if names:
        return names.get(lang) or names["en"]
    return fallback or topic.replace("_", " ").capitalize()


def _fmt_minutes(minutes: float) -> str:
    hours = minutes / 60
    if hours >= 1:
        return f"{hours:.0f} h" if hours >= 10 else f"{hours:.1f} h"
    return f"{minutes:.0f} min"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:.0f}"


def ns(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    src = (item.get("source") or {}).get(SOURCE_NS)
    return src if isinstance(src, dict) else None


def our_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in tasks if isinstance(t, dict) and t.get("id") and ns(t)]


def task_for(tasks: list[dict[str, Any]], uid: str) -> dict[str, Any] | None:
    for task in our_tasks(tasks):
        if (ns(task) or {}).get("sensor_uid") == uid:
            return task
    return None


def is_armed(task: dict[str, Any]) -> bool:
    return bool(task.get("next_due"))


def is_enabled(task: dict[str, Any]) -> bool:
    return task.get("enabled", True) is not False


def is_pending(task: dict[str, Any] | None) -> bool:
    return bool((ns(task) or {}).get("pending_reset"))


def is_awaiting(task: dict[str, Any] | None) -> bool:
    return bool((ns(task) or {}).get("awaiting_reset"))


def _ns_with(
    task: dict[str, Any] | None,
    c: Consumable,
    *,
    paused: bool | None = None,
    pending: bool | None = None,
    awaiting: bool | None = None,
) -> dict[str, Any]:
    """Our namespace for *task*, with the paused / pending / awaiting marks changed."""
    data = {**(ns(task) or {}), "sensor_uid": c.uid, "device_id": c.device_id}
    for key, flag in (
        ("paused", paused),
        ("pending_reset", pending),
        ("awaiting_reset", awaiting),
    ):
        if flag is True:
            data[key] = True
        elif flag is False:
            data.pop(key, None)
    return {SOURCE_NS: data}


def task_name(c: Consumable, settings: Settings) -> str:
    key = "clean" if c.is_chore else "replace"
    return _t(settings.lang, key).format(part=c.name, robot=c.robot)


def task_notes(c: Consumable, settings: Settings) -> str:
    lang = settings.lang
    lines = [
        f"**{_t(lang, 'robot')}:** {c.robot}",
        f"**{_t(lang, 'part')}:** {c.name}",
    ]
    pct = c.percent
    remaining = f"{_fmt_pct(pct)} % {_t(lang, 'of_life')}"
    if c.unit != "%" and c.value is not None:
        remaining += f" ({_fmt_minutes(c.value)})"
    lines.append(
        f"**{_t(lang, 'remaining')}:** {remaining} "
        f"({_t(lang, 'limit')} {_fmt_pct(settings.threshold)} %)"
    )
    if c.unit != "%" and c.lifetime:
        lines += ["", _t(lang, "learned").format(life=_fmt_minutes(c.lifetime))]
    lines += ["", _t(lang, "clears")]
    return "\n".join(lines)


def build_add_task_payload(c: Consumable, settings: Settings) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": task_name(c, settings),
        "notes": task_notes(c, settings),
        "recurrence_type": "triggered",
        "source": _ns_with(None, c),
        "managed_by": {
            "integration": SOURCE_NS,
            "display_name": MANAGED_DISPLAY_NAME,
            "icon": MANAGED_ICON,
            "config_entry_id": settings.config_entry_id,
            "deletion_protected": True,
            "completion_prompt": _t(
                settings.lang, "prompt_clean" if c.is_chore else "prompt_replace"
            ),
            "locked_fields": list(LOCKED_FIELDS),
        },
    }
    if c.device_id:
        payload["device_id"] = c.device_id
    return payload


# ── planners ─────────────────────────────────────────────────────────────────
def plan_reading(
    tasks: list[dict[str, Any]],
    c: Consumable,
    prev_value: float | None,
    settings: Settings,
) -> list[Action]:
    """What one reading means. *prev_value* is the reading before it (same unit)."""
    if c.paused:
        return []
    pct = c.percent
    prev_percent = c.percent_of(prev_value)
    if pct is None:
        return []
    task = task_for(tasks, c.uid)
    if task is not None and not is_enabled(task):
        return []
    recovered = pct >= settings.threshold + settings.reset_margin
    jumped = prev_percent is not None and pct - prev_percent >= settings.reset_margin

    if is_pending(task) or is_awaiting(task):
        if jumped or recovered:
            # The reset we pressed (or waited for) arrived; the replacement is
            # already recorded by the user's completion.
            source = _ns_with(task, c, pending=False, awaiting=False)
            return [SetSource(task["id"], c.uid, source)]
        if is_pending(task) and settings.reset_on_done and c.reset_entity_id:
            return [PressReset(c.uid, c.reset_entity_id)]  # robot back: try again
        return []  # never re-arm on the old, unreset counter

    if jumped:
        # Reset outside Home Keeper (Valetudo UI, the button, an automation).
        if task is None:
            return [CreateTask(c.uid, build_add_task_payload(c, settings), complete=True)]
        return [CompleteTask(task["id"], c.uid)]

    if pct <= settings.threshold:
        if task is None:
            return [CreateTask(c.uid, build_add_task_payload(c, settings))]
        if is_armed(task):
            return []
        return [ArmTask(task["id"], c.uid, task_notes(c, settings))]

    if prev_percent is None and task is not None and is_armed(task) and recovered:
        # Reset while we were not watching (Home Assistant was down).
        return [CompleteTask(task["id"], c.uid)]
    return []


def plan_user_completion(
    task_id: str, task: dict[str, Any] | None, c: Consumable | None, settings: Settings
) -> list[Action]:
    """A user ticked our task off: reset the robot's counter, and expect the jump.

    Without a reset (option off, or no usable button) the counter stays where it is.
    While it is still low, the task waits for the reset in Valetudo instead: no re-arm
    on the old counter, and that reset is not recorded as a second replacement.
    """
    if c is None or c.paused:
        return []
    if settings.reset_on_done and c.reset_entity_id:
        return [
            SetSource(task_id, c.uid, _ns_with(task, c, pending=True, awaiting=False)),
            PressReset(c.uid, c.reset_entity_id),
        ]
    pct = c.percent
    if pct is not None and pct >= settings.threshold + settings.reset_margin:
        return []  # not worn: the next low reading is a new cycle
    return [SetSource(task_id, c.uid, _ns_with(task, c, awaiting=True))]


def plan_reconcile(
    tasks: list[dict[str, Any]],
    consumables: list[Consumable],
    prev: dict[str, float],
    settings: Settings,
) -> list[Action]:
    """Converge every task; pause, resume, drop the ones whose sensor is gone."""
    actions: list[Action] = []
    wanted = {c.uid for c in consumables}
    for task in our_tasks(tasks):
        uid = (ns(task) or {}).get("sensor_uid")
        if uid not in wanted:
            actions.append(DeleteTask(task["id"], str(uid)))
    view = list(tasks)
    for c in consumables:
        task = task_for(view, c.uid)
        if c.paused:
            if task is not None and is_enabled(task):
                actions.append(PauseTask(task["id"], c.uid, _ns_with(task, c, paused=True)))
            continue
        if task is not None and not is_enabled(task) and (ns(task) or {}).get("paused"):
            source = _ns_with(task, c, paused=False)
            actions.append(ResumeTask(task["id"], c.uid, source))
            resumed = {
                **task,
                "enabled": True,
                "source": {**(task.get("source") or {}), **source},
            }
            view = [resumed if t is task else t for t in view]
        actions.extend(plan_reading(view, c, prev.get(c.uid), settings))
    return actions


# ── spare parts (one managed appliance per robot) ────────────────────────────
def find_robot_asset(assets: list[dict[str, Any]], robot_key: str) -> dict[str, Any] | None:
    for asset in assets:
        src = ns(asset)
        if src and src.get("role") == ASSET_ROLE and src.get("robot_key") == robot_key:
            return asset
    return None


def our_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in assets if (ns(a) or {}).get("role") == ASSET_ROLE]


def _part_by_name(asset: dict[str, Any], name: str) -> dict[str, Any] | None:
    for part in asset.get("parts") or []:
        if part.get("name") == name:
            return part
    return None


def _owner_view(part: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(part.get(k) or None for k in ("name", "type"))


def desired_parts(group: list[Consumable]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    parts = []
    for c in group:
        if c.ctype in STOCKED_TYPES and c.name not in seen:
            seen.add(c.name)
            parts.append({"name": c.name, "type": PART_TYPE_CONSUMABLE})
    return parts


def build_asset_payload(
    robot_key: str, group: list[Consumable], settings: Settings
) -> dict[str, Any]:
    first = group[0]
    payload: dict[str, Any] = {
        "name": first.robot,
        "icon": ASSET_ICON,
        "notes": _t(settings.lang, "part_notes").format(robot=first.robot),
        "parts": desired_parts(group),
        "source": {
            SOURCE_NS: {"role": ASSET_ROLE, "robot_key": robot_key, "device_id": first.device_id}
        },
        "managed_by": {
            "integration": SOURCE_NS,
            "display_name": MANAGED_DISPLAY_NAME,
            "icon": MANAGED_ICON,
            "config_entry_id": settings.config_entry_id,
            "deletion_protected": True,
            "locked_fields": list(ASSET_LOCKED_FIELDS),
        },
    }
    if first.device_id:
        payload["device_id"] = first.device_id
    return payload


def plan_stock(
    assets: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    consumables: list[Consumable],
    settings: Settings,
    *,
    enabled: bool,
) -> list[StockAction]:
    """One appliance of spares per robot; each replace task draws from its part."""
    actions: list[StockAction] = []
    ours = {a.get("id") for a in our_assets(assets)}
    if not enabled:
        for task in our_tasks(tasks):
            link = (task.get("source") or {}).get(HK_SOURCE_PART)
            if isinstance(link, dict) and link.get("asset_id") in ours:
                actions.append(UnlinkConsumable(task["id"]))
        return actions

    groups: dict[str, list[Consumable]] = {}
    for c in consumables:
        if c.ctype in STOCKED_TYPES:
            groups.setdefault(c.robot_key, []).append(c)

    for robot_key, group in groups.items():
        asset = find_robot_asset(assets, robot_key)
        if asset is None:
            actions.append(EnsureAsset(robot_key, build_asset_payload(robot_key, group, settings)))
            continue
        wanted = desired_parts(group)
        merged = []
        for part in wanted:
            existing = _part_by_name(asset, part["name"])
            merged.append(
                {**part, "id": existing["id"]} if existing and existing.get("id") else dict(part)
            )
        names = {p["name"] for p in wanted}
        differs = any(
            _part_by_name(asset, p["name"]) is None
            or _owner_view(_part_by_name(asset, p["name"]) or {}) != _owner_view(p)
            for p in wanted
        ) or any(
            p.get("name") not in names and p.get("stock") is None for p in asset.get("parts") or []
        )
        if differs:
            actions.append(UpdateManagedAsset(asset["id"], merged))
        for c in group:
            task = task_for(tasks, c.uid)
            part = _part_by_name(asset, c.name)
            if task is None or part is None or not part.get("id"):
                continue
            link = (task.get("source") or {}).get(HK_SOURCE_PART)
            if not (
                isinstance(link, dict)
                and link.get("asset_id") == asset["id"]
                and link.get("part_id") == part["id"]
            ):
                actions.append(LinkConsumable(task["id"], asset["id"], part["id"]))
    return actions
