# bdh-app-sonos-control

Black Diamond Hub app for the lodge's Sonos amps: a diagram of the lodge
where you tap rooms to group them, preset groups (Whole Lodge, All
Outside, Deck + Hot Tub), Ungroup all and Pause all, group and per-room
volume, and play/pause/skip. Guests play music from their own phones
(Spotify Connect, AirPlay and so on); the soundbars are left to the TVs.
Moved out of
[BlackDiamondHub](https://github.com/blackdiamondlodge/BlackDiamondHub) and
installed into the hub through the `blackdiamondhub.plugins` entry point
(see [bdh-sdk](https://github.com/blackdiamondlodge/bdh-sdk)).

Page: `/sonos_control/`. The page polls `state/` every 2 seconds and
sends changes to `action/`. Every screen shares one cached snapshot, so
the speakers see the same traffic however many screens are open.

Settings (environment):

- `BDH_SONOS_CONTROL_INTERFACE` (legacy `SONOS_INTERFACE`): IP of the
  interface on the Sonos network, for Docker with host networking.
- `BDH_SONOS_CONTROL_MODELS`: comma-separated model names to control
  (default `Amp`).

Room names, floors and presets are in `sonos_control/sonos.py`.

## Development

CI (`.github/workflows/ci.yml`) uses the reusable `app-ci.yml` from
bdh-sdk. It builds the wheel, installs it next to the hub, then runs
`hub_smoke`, the app tests and the selenium tests.

To release, bump `version` in `pyproject.toml`. When that lands on
`main`, `release.yml` tags it and publishes the wheel, and the release
notes include the hashed line for the hub's `sites/<site>/apps.txt`.
