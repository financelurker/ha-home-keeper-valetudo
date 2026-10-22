# Brand icon

`icon.svg` is the source of the icon in `custom_components/home_keeper_valetudo/brand/`
(`icon.png` 256×256, `icon@2x.png` and `logo.png` 512×512), which Home Assistant shows
on the integration page.

## Origin

The icon was drawn from scratch for this project, as hand-written SVG from basic shapes:
a robot vacuum seen from slightly above, with its lidar turret, and four consumable levels
on its front, one of them low. It shares the blue gradient and the level bars with the
icon of [ha-home-keeper-ipp](https://github.com/financelurker/ha-home-keeper-ipp). No
template, icon set, stock image, Valetudo artwork or other third-party artwork was used.

## Changing it

Edit `icon.svg`, then render the PNGs (Playwright with Chromium) and commit both:

```bash
cd scripts/brand
node render.js
```

The source stays outside the integration folder, so it is not part of the release ZIP.
