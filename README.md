# Home Keeper — Valetudo consumables

[![Validate](https://github.com/financelurker/ha-home-keeper-valetudo/actions/workflows/validate.yml/badge.svg)](https://github.com/financelurker/ha-home-keeper-valetudo/actions/workflows/validate.yml)

Turns the consumables of robot vacuums running [Valetudo](https://valetudo.cloud) into [Home Keeper](https://github.com/prestomation/ha-home-keeper) tasks: one *"Replace Main brush (D9)"* or *"Clean Sensors (D9)"* task per consumable, raised when it is worn and cleared when its counter is reset.

It is a small *glue* integration in the sense of Home Keeper's [glue pattern](https://github.com/prestomation/ha-home-keeper/blob/main/docs/GLUE_INTEGRATIONS.md): it changes neither Valetudo nor Home Keeper and talks to both only through the registries, entity states, services and events. It works with any Valetudo robot and uses only the entities Valetudo creates through MQTT autodiscovery.

- **One task per consumable:** a triggered task on the robot's device, raised when the consumable has 10 % of its life left or less.
- **Both directions:** resetting the counter in Valetudo completes the task; ticking the task off in Home Keeper presses the consumable's reset button.
- **Percent or minutes:** robots that report minutes left (Dreame, for example) are measured against a learned lifetime.
- **Spare parts (optional):** one appliance per robot with brushes, filters, mop pads, dust bags and detergent as consumable parts; each replacement takes one off the stock.
- **Disabled robots:** a disabled MQTT entry, device or sensor pauses its tasks instead of deleting them.

## Installation

Requires Home Assistant 2026.10 or newer, Home Keeper, and Valetudo connected over MQTT with:

- **Home Assistant autodiscovery** turned on, and
- the optional **consumables** feature (*ConsumableMonitoringCapability*) turned on in Valetudo's MQTT settings. It is off by default, and without it Valetudo publishes no consumables.

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=financelurker&repository=ha-home-keeper-valetudo&category=integration)

1. Click the button above. It opens the repository in HACS and adds it as a custom repository if needed.
2. Select **Download** and restart Home Assistant.

Without the button: HACS → ⋮ → **Custom repositories** → add `https://github.com/financelurker/ha-home-keeper-valetudo` as type *Integration*.

### Manual

1. Copy `custom_components/home_keeper_valetudo` from the ZIP of the latest release into `config/custom_components/`.
2. Restart Home Assistant.

A manual copy can be switched to HACS later: download it in HACS over the existing folder and restart. Tasks, history, learned lifetimes and spare counts stay as they are.

## Setup

**Settings → Devices & services → Add integration → Home Keeper — Valetudo consumables.** One instance covers every Valetudo robot. It then shows up in Home Keeper under **Settings → Companions**.

| Option | Default | Meaning |
|---|---|---|
| Low limit | 10 % | A task is raised when a consumable has this much of its life left or less. |
| Reset detection | 20 points | A rise of at least this much is read as a counter reset and records the replacement. |
| Reset the robot's counter when a task is marked done | on | Presses the consumable's reset button in Valetudo. |
| Consumables to ignore | none | Their tasks are removed. |
| Count spare parts | on | The spare-parts appliance per robot, see below. |

## How it decides

| The robot reports … / you … | Result in Home Keeper |
|---|---|
| a consumable at or below the low limit | the task is created or armed (also right after setup) |
| the counter is reset in Valetudo | the task is completed, the replacement is in its history and one spare is taken off the stock, also for an early replacement |
| you mark the task done in Home Keeper | the glue presses the consumable's reset button, the counter is full again |
| … while the robot is offline | the reset is repeated once the robot reports again; until then the task does not arm again |
| unknown value, robot offline | nothing |

**Minutes or percent.** Depending on the robot, Valetudo reports percent or minutes left. Minutes are turned into percent of a **learned lifetime**: the highest value the counter has shown, so right after a reset the full life. Until the first reset that is the highest value seen so far, so the first task may come a little late. Learned lifetimes survive restarts.

## Home Keeper

- **Tasks** are *triggered* tasks on the robot's device, managed by this integration (*Managed by Valetudo*). Their name and device are locked; deleting them in Home Keeper is blocked while this integration is installed.
- **Spare parts:** one appliance per robot, named after it, with brushes, filters, mop pads, dust bags and detergent as *consumable* parts, each linked to its task. Enter the stock and a reorder point on a part and turn on *Auto-create buy task* to get a buy task when you run low. Cleaning chores (sensors, wheels, dock) have no stock.
- **Disabled MQTT entry, device or sensor:** its tasks are switched off (not deleted). When it is enabled again they are switched on and continue. A task you switched off yourself is never touched.
- **Removed sensor:** its task is deleted.

## Releases

The GitHub release tag is the only source of the version; nothing in the repository holds it (`manifest.json` keeps the placeholder `0.0.0`, which Home Assistant needs to load the integration).

1. Create a release on GitHub with a tag such as `v1.2.3`, on a commit that contains the *Release* workflow. Pre-releases get their ZIP the same way; HACS offers them only with **Show beta versions** turned on.
2. The *Release* workflow takes the version from the tag (without the leading `v`; it stops with an error if the tag isn't a version number), writes it into `manifest.json`, packs the integration as `home_keeper_valetudo.zip` and attaches it to the release.
3. HACS installs that ZIP (`zip_release` in `hacs.json`), so Home Assistant shows the release version.

To rebuild the ZIP of an existing release, run the *Release* workflow under **Actions → Release → Run workflow** with the release's tag.

Good to know:

- HACS installs **only from releases** (their ZIP). As long as there is no full release (only pre-releases or none), HACS installs fail.
- The ZIP is attached about a minute after the release is published. An update started in that minute fails; just try again.
- The workflow stops if the tag isn't a version Home Assistant accepts, so a release can't ship an integration that Home Assistant refuses to load.

## Development

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

The tests run in a real Home Assistant test instance (`pytest-homeassistant-custom-component`), against Home Keeper's test fake. They need Home Keeper in `custom_components/home_keeper`; CI copies a pinned release there (see `.github/workflows/validate.yml`), locally those tests are skipped without it. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT License, see [LICENSE](LICENSE).

This project is not affiliated with Home Assistant, the Open Home Foundation, Valetudo or Home Keeper.
