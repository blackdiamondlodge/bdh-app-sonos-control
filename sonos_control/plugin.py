from bdh_sdk import PluginSpec, Tile

spec = PluginSpec(
    id="sonos_control",
    app="sonos_control",
    urls="sonos_control.urls",
    websocket_routes="sonos_control.routing.websocket_urlpatterns",
    tiles=(
        Tile(
            title="Music",
            url_name="sonos_control",
            icon="sonos_control/hub/icon.svg",
            section="lodge",
            subtitle="Sonos in every room",
            order=20,
        ),
    ),
)
