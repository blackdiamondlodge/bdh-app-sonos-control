from bdh_sdk import PluginSpec, Tile

spec = PluginSpec(
    id="sonos_control",
    app="sonos_control",
    urls="sonos_control.urls",
    tiles=(
        Tile(
            title="Music",
            url_name="sonos_control",
            icon="sonos_control/hub/icon.svg",
            section="lodge",
            subtitle="Group rooms, volume and play/pause",
            order=20,
        ),
    ),
)