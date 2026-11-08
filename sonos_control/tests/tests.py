from django.test import TestCase
from unittest.mock import patch


class SpotifySearchInputTest(TestCase):
    """Tests for touch-screen keyboard support on the Spotify search input."""

    @patch('sonos_control.views.sonos_get_speaker_info')
    def test_search_input_has_inputmode_none(self, mock_speakers):
        """The search input should have inputmode='none' to suppress the native keyboard in favour of the custom virtual keyboard."""
        mock_speakers.return_value = {}

        session = self.client.session
        session['spotify_token_info'] = {'access_token': 'fake-token'}
        session.save()

        response = self.client.get('/sonos_control/')
        content = response.content.decode()

        self.assertIn('inputmode="none"', content)

    @patch('sonos_control.views.sonos_get_speaker_info')
    def test_search_input_has_autocomplete_off(self, mock_speakers):
        """The search input should have autocomplete='off' to avoid browser suggestions covering the keyboard."""
        mock_speakers.return_value = {}

        session = self.client.session
        session['spotify_token_info'] = {'access_token': 'fake-token'}
        session.save()

        response = self.client.get('/sonos_control/')
        content = response.content.decode()

        self.assertIn('autocomplete="off"', content)

    @patch('sonos_control.views.sonos_get_speaker_info')
    def test_virtual_keyboard_present(self, mock_speakers):
        """The page should include the custom virtual keyboard container for touch-screen use."""
        mock_speakers.return_value = {}

        session = self.client.session
        session['spotify_token_info'] = {'access_token': 'fake-token'}
        session.save()

        response = self.client.get('/sonos_control/')
        content = response.content.decode()

        self.assertIn('id="virtual-keyboard"', content)
        self.assertIn('vk-container', content)


class SonosInterfaceSettingTests(TestCase):
    @patch('sonos_control.views.soco.discover', return_value=set())
    def test_namespaced_interface_wins(self, discover):
        import os
        from sonos_control import views
        env = {'BDH_SONOS_CONTROL_INTERFACE': '10.0.0.5',
               'SONOS_INTERFACE': '10.0.0.9'}
        with patch.dict(os.environ, env):
            views._discover_sonos(timeout=1)
        discover.assert_called_once_with(timeout=1, interface_addr='10.0.0.5')

    @patch('sonos_control.views.soco.discover', return_value=set())
    def test_legacy_interface_still_works(self, discover):
        import os
        from sonos_control import views
        with patch.dict(os.environ, {'SONOS_INTERFACE': '10.0.0.9'}):
            os.environ.pop('BDH_SONOS_CONTROL_INTERFACE', None)
            views._discover_sonos()
        discover.assert_called_once_with(interface_addr='10.0.0.9')


class SonosWebsocketRouteTests(TestCase):
    def test_plugin_exposes_sonos_updates_route(self):
        from hub.loader import websocket_urlpatterns
        from sonos_control.consumers import SonosConsumer
        from sonos_control.plugin import spec
        patterns = websocket_urlpatterns([spec])
        self.assertIn('ws/sonos_updates/', [str(p.pattern) for p in patterns])
        self.assertIn(SonosConsumer, [getattr(p.callback, 'consumer_class', None)
                                      for p in patterns])
