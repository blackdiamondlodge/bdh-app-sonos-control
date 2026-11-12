# bdh-app-sonos-control

Black Diamond Hub app for a site's Sonos speakers. Every room or group is
a player card (playing ones first, idle ones as compact rows) with
play/pause/skip and volume; group cards expand to per-room volume and mute.
Each card's Rooms button opens a picture of the house (or a plain grid of
rooms when no floors are configured) where you tick the
rooms that play together (a room already in another group can be moved
after a confirmation). Quick groups (configured per site),
Ungroup all and Pause all sit on top, and every group card has an Ungroup
button. A soundbar playing its TV (or a line-in) gets volume only, and Pause
all and the homepage tile leave it alone. Guests play music from their own
phones (Spotify Connect, AirPlay and so on).
Moved out of
[BlackDiamondHub](https://github.com/blackdiamondlodge/BlackDiamondHub) and
installed into the hub through the `blackdiamondhub.plugins` entry point
(see [bdh-sdk](https://github.com/blackdiamondlodge/bdh-sdk)).

Page: `/sonos_control/`. The page polls `state/` every 2 seconds and
sends changes to `action/`. Every screen shares one cached snapshot, so
the speakers see the same traffic however many screens are open.

On the hub's homepage the Music tile lists the groups that are playing, each
with play/pause and skip, and a pause-everything button (which becomes
Resume). Paused groups stay on the tile, dimmed, for `pause_grace`
minutes so a quick pause can be undone there; groups that were already
paused are never listed, since idle Sonos speakers sit paused for months.

Settings (each reads the Settings page, then the environment variable,
then the Django setting of the same name; the hub fills the Django settings
from `[apps.sonos_control]` in its `sites/<site>/site.toml`):

- `BDH_SONOS_CONTROL_INTERFACE` (legacy `SONOS_INTERFACE`): IP of the
  interface on the Sonos network, for Docker with host networking.
- `BDH_SONOS_CONTROL_SPEAKERS`: Speakers to show, the Sonos room uids this
  hub controls. On the Settings page it is a checklist of every room found on
  the network (ticked rooms that are offline stay listed); new speakers stay
  hidden until ticked. A group that mixes ticked and unticked rooms shows the
  unticked ones greyed out, without controls; ungrouping such a group pauses
  an unticked room left playing. Until this is saved, `models` decides.
- `BDH_SONOS_CONTROL_MODELS`: comma-separated model names to control
  (default `Amp`); `*` controls every Sonos. Ignored once Speakers to show
  has been saved.
- `BDH_SONOS_CONTROL_ROOMS`: `{sonos room name: {label, floor, icon}}`
  (JSON in the environment). `floor` is `upper` / `main` / `lower` (inside the
  house picture) or `outside_main` / `outside_lower`. If no room has a floor,
  the room picker is a plain grid. Unlisted rooms use their Sonos name.
- `BDH_SONOS_CONTROL_PRESETS`: quick groups,
  `[{id, label, icon, rooms: [room names] or "*"}]` (JSON in the
  environment). Default: one "Everywhere" button.
- `BDH_SONOS_CONTROL_NIGHT_URL`: status endpoint of the
  [appdaemon-sonos-night](https://github.com/sslivins/appdaemon-sonos-night)
  app, e.g. `http://192.168.10.217:5050/api/appdaemon/sonos_night`. During
  quiet hours the page shows the limit and caps the outdoor sliders. The limit
  itself is enforced by that app, so phones can't get around it. Unset means
  no night limit is shown.
- `BDH_SONOS_CONTROL_PAUSE_GRACE`: minutes a paused group stays on the
  homepage Music tile (default 5, also on the Settings page).

## Development

CI (`.github/workflows/ci.yml`) uses the reusable `app-ci.yml` from
bdh-sdk. It builds the wheel, installs it next to the hub, then runs
`hub_smoke`, the app tests and the selenium tests.

To release, bump `version` in `pyproject.toml`. When that lands on
`main`, `release.yml` tags it and publishes the wheel, and the release
notes include the hashed line for the hub's `sites/<site>/apps.txt`.
