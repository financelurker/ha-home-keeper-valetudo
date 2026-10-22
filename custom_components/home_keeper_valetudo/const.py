"""Constants for the Home Keeper ↔ Valetudo glue integration.

Valetudo (the cloud-free firmware for robot vacuums) publishes its consumables to Home
Assistant over MQTT autodiscovery: per consumable one sensor with the remaining life
(percent, or minutes depending on the robot) and one button that resets it. The glue
turns a worn consumable into a Home Keeper ``triggered`` task, records a replacement
when the counter is reset, and presses the reset button when the task is ticked off in
Home Keeper. Bus, registries and services only — no Python import in either direction.
"""

from __future__ import annotations

DOMAIN = "home_keeper_valetudo"

# ── Home Keeper side ─────────────────────────────────────────────────────────
HK_DOMAIN = "home_keeper"
HK_EVENT_TASK_COMPLETED = "home_keeper_task_completed"
HK_EVENT_REGISTER_COMPANIONS = "home_keeper_register_companions"
HK_SERVICE_REGISTER_COMPANION = "register_companion"
HK_SERVICE_LIST_TASKS = "list_tasks"
HK_SERVICE_ADD_TASK = "add_task"
HK_SERVICE_UPDATE_TASK = "update_task"
HK_SERVICE_DELETE_TASK = "delete_task"
HK_SERVICE_COMPLETE_TASK = "complete_task"
HK_SERVICE_TRIGGER_TASK = "trigger_task"
HK_SERVICE_LIST_ASSETS = "list_assets"
HK_SERVICE_ADD_ASSET = "add_asset"
HK_SERVICE_UPDATE_ASSET = "update_asset"
HK_SERVICE_DELETE_ASSET = "delete_asset"
HK_SERVICE_UPDATE_MANAGED_ASSET = "update_managed_asset"
HK_SERVICE_SET_TASK_CONSUMABLE = "set_task_consumable"
HK_SOURCE_PART = "part"

SOURCE_NS = DOMAIN
ORIGIN = DOMAIN

# ── Valetudo side (EXTERNAL CONTRACT — Valetudo backend/lib/mqtt) ────────────
# ConsumableMonitoringCapabilityMqttHandle + InLineHassComponent:
#   unique_id = <identifier>_<component type>_ConsumableMonitoringCapability_<topic>
#   topic     = <type>[_<subtype>], the first "-" of "<type>-<subtype>" made "_"
#   sensor    → remaining life in "%" or "min" (device_class duration)
#   button    → the same unique_id + "_reset", payload PERFORM
VALETUDO_PLATFORM = "mqtt"
VALETUDO_CAPABILITY = "ConsumableMonitoringCapability"
SENSOR_UID_RE = r"^(?P<robot>.+)_sensor_ConsumableMonitoringCapability_(?P<topic>[a-z0-9_]+)$"
BUTTON_UID_TEMPLATE = "{robot}_button_ConsumableMonitoringCapability_{topic}_reset"

CONSUMABLE_TYPES = ("filter", "brush", "mop", "detergent", "bin", "cleaning")
# Spares are kept only for parts that are bought; a "cleaning" consumable (sensors,
# wheels, dock) is a chore and has nothing to stock.
STOCKED_TYPES = ("filter", "brush", "mop", "detergent", "bin")

# ── Options ──────────────────────────────────────────────────────────────────
OPT_THRESHOLD = "threshold"
OPT_RESET_MARGIN = "reset_margin"
OPT_RESET_ON_DONE = "reset_on_done"
OPT_EXCLUDED_ENTITIES = "excluded_entities"
OPT_STOCK_ENABLED = "stock_enabled"

DEFAULT_THRESHOLD = 10
# A reset puts the counter back to full; normal wear only takes it down. A rise of at
# least this many percentage points (of the learned lifetime) is read as a reset.
DEFAULT_RESET_MARGIN = 20
DEFAULT_RESET_ON_DONE = True
DEFAULT_STOCK_ENABLED = True

# ── Managed ownership ────────────────────────────────────────────────────────
MANAGED_DISPLAY_NAME = "Valetudo"
MANAGED_ICON = "mdi:robot-vacuum-alert"
COMPANION_ICON = "mdi:robot-vacuum"
DOCS_URL = "https://github.com/financelurker/ha-home-keeper-valetudo"
LOCKED_FIELDS = ["name", "device_id", "recurrence_type"]
ASSET_ROLE = "robot_spares"
ASSET_LOCKED_FIELDS = ["parts"]
ASSET_ICON = "mdi:robot-vacuum"
PART_TYPE_CONSUMABLE = "consumable"

# Storage for the learned lifetime of minute-based consumables.
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.lifetimes"

# ── Text ─────────────────────────────────────────────────────────────────────
TEXT: dict[str, dict[str, str]] = {
    "en": {
        "replace": "Replace {part} ({robot})",
        "clean": "Clean {part} ({robot})",
        "prompt_replace": "Marking it done resets the counter on the robot.",
        "prompt_clean": "Marking it done resets the counter on the robot.",
        "robot": "Robot",
        "part": "Consumable",
        "remaining": "Remaining when the task was raised",
        "of_life": "of its life",
        "limit": "low limit",
        "learned": (
            "Lifetime learned from the counter: {life}. Until the first reset this is "
            "the highest value seen, so the first task may come a little late."
        ),
        "clears": "Resetting the counter (here or in Valetudo) records the replacement.",
        "part_notes": "Spare parts for {robot}.",
        "description": (
            "Turns worn Valetudo consumables into Home Keeper tasks and resets the "
            "robot's counter when you mark them done."
        ),
    },
    "de": {
        "replace": "{part} tauschen ({robot})",
        "clean": "{part} reinigen ({robot})",
        "prompt_replace": "Abhaken setzt den Zähler am Roboter zurück.",
        "prompt_clean": "Abhaken setzt den Zähler am Roboter zurück.",
        "robot": "Roboter",
        "part": "Verbrauchsteil",
        "remaining": "Rest beim Auslösen",
        "of_life": "der Lebensdauer",
        "limit": "Untergrenze",
        "learned": (
            "Lebensdauer aus dem Zähler gelernt: {life}. Bis zum ersten Reset ist das "
            "der höchste gesehene Wert, der erste Task kann daher etwas spät kommen."
        ),
        "clears": ("Ein Zähler-Reset (hier oder in Valetudo) erfasst den Tausch."),
        "part_notes": "Ersatzteile für {robot}.",
        "description": (
            "Macht aus verbrauchten Valetudo-Verbrauchsteilen Home-Keeper-Tasks und "
            "setzt beim Abhaken den Zähler am Roboter zurück."
        ),
    },
}

# Names per Valetudo (type, subtype); the sensor's own name is the fallback.
PART_NAMES: dict[str, dict[str, str]] = {
    "brush_main": {"en": "Main brush", "de": "Hauptbürste"},
    "brush_side_right": {"en": "Right side brush", "de": "Rechte Seitenbürste"},
    "brush_side_left": {"en": "Left side brush", "de": "Linke Seitenbürste"},
    "brush": {"en": "Brush", "de": "Bürste"},
    "brush_secondary": {"en": "Secondary brush", "de": "Zweitbürste"},
    "filter_main": {"en": "Filter", "de": "Filter"},
    "filter": {"en": "Filter", "de": "Filter"},
    "mop_main": {"en": "Mop pad", "de": "Wischpad"},
    "mop": {"en": "Mop pad", "de": "Wischpad"},
    "mop_dock": {"en": "Dock mop", "de": "Wischpad (Station)"},
    "detergent": {"en": "Detergent", "de": "Reinigungsmittel"},
    "detergent_dock": {"en": "Dock detergent", "de": "Reinigungsmittel (Station)"},
    "bin": {"en": "Dust bag", "de": "Staubbeutel"},
    "bin_dock": {"en": "Dock dust bag", "de": "Staubbeutel (Station)"},
    "cleaning_sensor": {"en": "Sensors", "de": "Sensoren"},
    "cleaning_wheel": {"en": "Wheels", "de": "Räder"},
    "cleaning_dock": {"en": "Dock", "de": "Station"},
    "cleaning": {"en": "Robot", "de": "Roboter"},
}

# Minutes per Home Assistant duration unit (a user can change the display unit).
MINUTES_PER_UNIT: dict[str, float] = {
    "s": 1 / 60,
    "min": 1.0,
    "h": 60.0,
    "d": 1440.0,
}
