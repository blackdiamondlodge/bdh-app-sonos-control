import json
import os
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from sonos_control import sonos


class FakeZone:
    def __init__(self, name, uid, model='Sonos Amp', volume=30):
        self.player_name = name
        self.uid = uid
        self.model = model
        self.volume = volume
        self.mute = False
        self.group = None
        self.avTransport = MagicMock()
        self.zone_group_state = MagicMock()
        self.transport = 'STOPPED'
        self.unjoin = MagicMock(side_effect=self._unjoin)
        self.join = MagicMock(side_effect=self._join)

    def get_speaker_info(self):
        return {'model_name': self.model}

    @property
    def is_coordinator(self):
        return self.group.coordinator is self

    def get_current_transport_info(self):
        return {'current_transport_state': self.transport}

    def get_current_track_info(self):
        return {'title': 'Song', 'artist': 'Band', 'album': '', 'album_art': '',
                'uri': 'x-sonos-vli:RINCON_1:2,spotify:abc'}

    def get_current_media_info(self):
        return {'uri': ''}

    def _unjoin(self):
        rest = [m for m in self.group.members if m is not self]
        if rest and self.group.coordinator is self:
            FakeGroup(rest[0], rest)
        else:
            self.group.members.remove(self)
        FakeGroup(self, [self])

    @property
    def all_groups(self):
        return list({z.group for z in HOUSEHOLD})

    def _join(self, master):
        if self in self.group.members:
            self.group.members.remove(self)
        master.group.members.append(self)
        self.group = master.group


class FakeGroup:
    def __init__(self, coordinator, members):
        self.coordinator = coordinator
        self.members = members
        self.volume = 40
        for m in members:
            m.group = self


HOUSEHOLD = []


def household():
    zs = [FakeZone('Dining Room', 'DR'), FakeZone('Top Deck', 'TD'),
          FakeZone('Hot Tub', 'HT'), FakeZone('Office', 'OF', model='Arc Ultra')]
    FakeGroup(zs[0], [zs[0], zs[1]])
    FakeGroup(zs[2], [zs[2]])
    FakeGroup(zs[3], [zs[3]])
    HOUSEHOLD[:] = zs
    return zs


class DiscoveryTests(TestCase):
    def tearDown(self):
        sonos._zones.clear()
        sonos.invalidate()

    @patch('sonos_control.sonos.soco.discover', return_value=set())
    def test_namespaced_interface_wins(self, discover):
        env = {'BDH_SONOS_CONTROL_INTERFACE': '10.0.0.5', 'SONOS_INTERFACE': '10.0.0.9'}
        with patch.dict(os.environ, env):
            sonos._discover_sonos(timeout=1)
        discover.assert_called_once_with(timeout=1, interface_addr='10.0.0.5')

    @patch('sonos_control.sonos.soco.discover', return_value=set())
    def test_legacy_interface_still_works(self, discover):
        with patch.dict(os.environ, {'SONOS_INTERFACE': '10.0.0.9'}):
            os.environ.pop('BDH_SONOS_CONTROL_INTERFACE', None)
            sonos._discover_sonos()
        discover.assert_called_once_with(interface_addr='10.0.0.9')

    def test_only_amps_are_controllable(self):
        with patch('sonos_control.sonos._discover_sonos', return_value=set(household())):
            names = sorted(z.player_name for z in sonos.zones(force=True).values())
        self.assertEqual(names, ['Dining Room', 'Hot Tub', 'Top Deck'])


class SnapshotTests(TestCase):
    def setUp(self):
        self.zs = household()
        self.zs[0].transport = 'PLAYING'
        sonos._zones.clear()
        sonos._zones.update({z.uid: z for z in self.zs[:3]})
        sonos._zones_at = float('inf')

    def tearDown(self):
        sonos._zones.clear()
        sonos._zones_at = 0.0
        sonos.invalidate()

    def test_groups_rooms_and_source(self):
        snap = sonos.snapshot(force=True)
        groups = {g['id']: g for g in snap['groups']}
        self.assertEqual(set(groups), {'DR', 'HT'})
        self.assertEqual(sorted(groups['DR']['members']), ['DR', 'TD'])
        self.assertEqual(groups['DR']['state'], 'PLAYING')
        self.assertEqual(groups['DR']['source']['name'], 'Spotify Connect')
        labels = {r['label'] for r in snap['rooms']}
        self.assertEqual(labels, {'Dining Room', 'Top Deck', 'Hot Tub'})
        self.assertNotIn('OF', [r['uid'] for r in snap['rooms']])

    def test_coordinator_leaving_hands_off(self):
        sonos.leave('DR')
        self.zs[0].avTransport.DelegateGroupCoordinationTo.assert_called_once()
        args = self.zs[0].avTransport.DelegateGroupCoordinationTo.call_args[0][0]
        self.assertIn(('NewCoordinator', 'TD'), args)
        self.zs[0].unjoin.assert_not_called()

    def test_coordinator_leaving_falls_back_to_regrouping(self):
        self.zs[0].avTransport.DelegateGroupCoordinationTo.side_effect = Exception('nope')
        self.zs[2].join(self.zs[0])
        sonos.leave('DR')
        self.zs[0].unjoin.assert_called_once()
        self.assertIs(self.zs[2].group.coordinator, self.zs[1])

    def test_member_leaving_just_unjoins(self):
        sonos.leave('TD')
        self.zs[1].unjoin.assert_called_once()

    def test_preset_keeps_playing_coordinator(self):
        coord = sonos.set_group(['Top Deck', 'Hot Tub', 'Dining Room'])
        self.assertEqual(coord, 'DR')
        self.assertIs(self.zs[2].group.coordinator, self.zs[0])

    def test_preset_drops_rooms_not_in_it(self):
        sonos.set_group(['Dining Room', 'Hot Tub'])
        self.zs[1].unjoin.assert_called_once()
        self.assertIs(self.zs[2].group.coordinator, self.zs[0])

    def test_merge_moves_idle_group_into_playing_one(self):
        self.assertEqual(sonos.merge('HT', 'DR'), 'DR')
        self.assertIs(self.zs[2].group.coordinator, self.zs[0])

    def test_ungroup_all(self):
        sonos.ungroup_all()
        self.zs[1].unjoin.assert_called_once()
        self.zs[0].unjoin.assert_not_called()


class SourceTests(TestCase):
    def test_sources(self):
        self.assertEqual(sonos.source_for('x-sonos-vli:RINCON_1:2,spotify:x', {})['name'], 'Spotify Connect')
        self.assertEqual(sonos.source_for('x-sonos-vli:RINCON_1:1,airplay:x', {})['name'], 'AirPlay')
        self.assertEqual(sonos.source_for('x-sonos-htastream:RINCON_1:spdif', {})['name'], 'TV')
        self.assertIsNone(sonos.source_for('', {}))


class ViewTests(TestCase):
    def test_page_renders_without_spotify(self):
        res = self.client.get(reverse('sonos_control'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        self.assertIn('id="room-layer"', content)
        self.assertNotIn('spotify_search', content)

    @patch('sonos_control.views.sonos.snapshot', return_value={'rooms': [], 'groups': [], 'presets': []})
    def test_state_json(self, snap):
        res = self.client.get(reverse('sonos_state'))
        self.assertEqual(res.json()['groups'], [])

    @patch('sonos_control.views.sonos.snapshot', side_effect=OSError('down'))
    def test_state_unreachable(self, snap):
        self.assertEqual(self.client.get(reverse('sonos_state')).status_code, 503)

    def post(self, body):
        return self.client.post(reverse('sonos_action'), json.dumps(body), content_type='application/json')

    def test_unknown_action_rejected(self):
        self.assertEqual(self.post({'action': 'format_disk'}).status_code, 400)

    @patch('sonos_control.views.time.sleep')
    @patch('sonos_control.views.sonos.snapshot', return_value={'rooms': [], 'groups': [], 'presets': []})
    @patch('sonos_control.views.sonos.set_group', return_value='DR')
    def test_preset_action(self, set_group, snap, sleep):
        res = self.post({'action': 'preset', 'preset': 'outside'})
        set_group.assert_called_once_with(['Top Deck', 'Lower Deck', 'Hot Tub'])
        self.assertEqual(res.json()['selected'], 'DR')

    @patch('sonos_control.views.sonos.snapshot', return_value={'rooms': [], 'groups': [], 'presets': []})
    @patch('sonos_control.views.sonos.set_room_volume')
    def test_room_volume_action(self, set_vol, snap):
        self.assertEqual(self.post({'action': 'room_volume', 'room': 'DR', 'volume': 20}).status_code, 200)
        set_vol.assert_called_once_with('DR', 20)

    @patch('sonos_control.views.sonos.join', side_effect=RuntimeError('timeout'))
    def test_speaker_failure_is_reported(self, join):
        self.assertEqual(self.post({'action': 'join', 'room': 'HT', 'group': 'DR'}).status_code, 502)