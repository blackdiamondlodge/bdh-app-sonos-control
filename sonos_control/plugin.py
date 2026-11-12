from bdh_sdk import PluginSpec, Setting, Tile
from bdh_sdk.plugins import INT, MULTI

spec = PluginSpec(
    id="sonos_control",
    app="sonos_control",
    urls="sonos_control.urls",
    status="sonos_control.homepage.status",
    run_action="sonos_control.homepage.run_action",
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
    settings=(
        Setting("speakers", "Speakers to show", type=MULTI, default=None,
                choices_from="sonos_control.sonos.speaker_choices",
                help="Rooms this hub lists and controls. New speakers stay hidden until ticked. "
                     "Until this is saved, the hub shows the Sonos Amps."),
        Setting("pause_grace", "Keep paused music on the homepage", type=INT, default=5,
                minimum=0, maximum=60, unit="min",
                help="How long a paused group stays on the Music tile so it can be resumed."),
    ),
)