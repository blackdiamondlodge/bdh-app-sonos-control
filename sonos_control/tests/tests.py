import json
import os
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
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

    @override_settings(BDH_SONOS_CONTROL_MODELS='*')
    def test_star_controls_every_model(self):
        zs = household()
        zs[3].get_speaker_info = MagicMock(side_effect=OSError('unreachable'))
        with patch('sonos_control.sonos._discover_sonos', return_value=set(zs)):
            names = sorted(z.player_name for z in sonos.zones(force=True).values())
        self.assertEqual(names, ['Dining Room', 'Hot Tub', 'Office', 'Top Deck'])


class SiteConfigTests(TestCase):
    def test_default_preset_is_everywhere(self):
        from django.conf import settings
        with override_settings():
            if hasattr(settings, 'BDH_SONOS_CONTROL_PRESETS'):  # the hub's site.toml may set one
                del settings.BDH_SONOS_CONTROL_PRESETS
            self.assertEqual([p['rooms'] for p in sonos.presets()], ['*'])

    @override_settings(BDH_SONOS_CONTROL_ROOMS={'Kitchen': {'label': 'Kitchen', 'icon': 'fa-utensils'}})
    def test_rooms_from_django_setting(self):
        self.assertEqual(sonos._meta('Kitchen'), {'label': 'Kitchen', 'floor': 'other', 'icon': 'fa-utensils'})
        self.assertEqual(sonos._meta('Den'), {'label': 'Den', 'floor': 'other', 'icon': 'fa-volume-high'})

    def test_presets_from_env_json(self):
        env = {'BDH_SONOS_CONTROL_PRESETS': json.dumps([{'id': 'kd', 'label': 'K + D', 'rooms': ['Kitchen', 'Deck']}])}
        with patch.dict(os.environ, env):
            self.assertEqual(sonos.presets()[0]['rooms'], ['Kitchen', 'Deck'])

    def test_bad_env_json_falls_back(self):
        with patch.dict(os.environ, {'BDH_SONOS_CONTROL_PRESETS': '[oops'}):
            self.assertEqual(sonos.presets(), sonos.DEFAULT_PRESETS)


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

    def _lower_deck_with_hot_tub(self):
        ld = FakeZone('Lower Deck', 'LD')
        FakeGroup(self.zs[2], [self.zs[2], ld])
        HOUSEHOLD.append(ld)
        sonos._zones['LD'] = ld
        return ld

    def test_set_rooms_pulls_room_from_another_group(self):
        ld = self._lower_deck_with_hot_tub()
        self.assertEqual(sonos.set_rooms('DR', ['DR', 'TD', 'LD']), 'DR')
        self.assertIs(ld.group.coordinator, self.zs[0])
        self.assertEqual(self.zs[2].group.members, [self.zs[2]])

    @patch('sonos_control.sonos.time.sleep')
    def test_set_rooms_unticking_coordinator_keeps_the_rest(self, sleep):
        self.zs[0].avTransport.DelegateGroupCoordinationTo.side_effect = Exception('nope')
        self.assertEqual(sonos.set_rooms('DR', ['TD', 'HT']), 'TD')
        self.assertIs(self.zs[2].group.coordinator, self.zs[1])
        self.assertEqual(self.zs[0].group.members, [self.zs[0]])

    def test_set_rooms_from_solo_player(self):
        self.assertEqual(sonos.set_rooms('HT', ['HT', 'TD']), 'HT')
        self.assertIs(self.zs[1].group.coordinator, self.zs[2])
        self.assertEqual(self.zs[0].group.members, [self.zs[0]])

    def test_set_rooms_needs_a_room(self):
        with self.assertRaises(sonos.SonosError):
            sonos.set_rooms('DR', [])

    def test_ungroup_all(self):
        sonos.ungroup_all()
        self.zs[1].unjoin.assert_called_once()
        self.zs[0].unjoin.assert_not_called()

    NIGHT = {'active': True, 'limit': 25, 'end': '07:00', 'rooms': ['Top Deck', 'Hot Tub']}

    def test_night_limit_caps_outdoor_room_volume(self):
        with patch('sonos_control.sonos.night_status', return_value=self.NIGHT):
            sonos.set_room_volume('TD', 80)
            sonos.set_room_volume('DR', 80)
        self.assertEqual(self.zs[1].volume, 25)
        self.assertEqual(self.zs[0].volume, 80)

    def test_night_limit_caps_group_only_when_all_rooms_limited(self):
        with patch('sonos_control.sonos.night_status', return_value=self.NIGHT):
            sonos.set_group_volume('HT', 70)
            sonos.set_group_volume('DR', 70)
        self.assertEqual(self.zs[2].group.volume, 25)
        self.assertEqual(self.zs[0].group.volume, 70)

    def test_inactive_night_limit_does_not_cap(self):
        with patch('sonos_control.sonos.night_status', return_value=dict(self.NIGHT, active=False)):
            sonos.set_room_volume('TD', 80)
        self.assertEqual(self.zs[1].volume, 80)


class NightStatusTests(TestCase):
    def setUp(self):
        sonos._night_at = 0.0

    tearDown = setUp

    def test_off_without_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('BDH_SONOS_CONTROL_NIGHT_URL', None)
            self.assertIsNone(sonos.night_status())

    @patch('sonos_control.sonos.urllib.request.urlopen')
    def test_reads_appdaemon_status(self, urlopen):
        body = b'{"active": true, "limit": 25, "end": "07:00", "rooms": ["Top Deck"], "overridden": false}'
        urlopen.return_value.__enter__.return_value.read.return_value = body
        with patch.dict(os.environ, {'BDH_SONOS_CONTROL_NIGHT_URL': 'http://ad/api/appdaemon/sonos_night'}):
            self.assertEqual(sonos.night_status(),
                             {'active': True, 'limit': 25, 'end': '07:00', 'rooms': ['Top Deck']})

    @patch('sonos_control.sonos.urllib.request.urlopen', side_effect=OSError('down'))
    def test_unreachable_means_no_limit_shown(self, urlopen):
        with patch.dict(os.environ, {'BDH_SONOS_CONTROL_NIGHT_URL': 'http://ad/x'}):
            self.assertIsNone(sonos.night_status())


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
        self.assertIn('id="picker"', content)
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

    @override_settings(BDH_SONOS_CONTROL_PRESETS=[
        {'id': 'dining_deck', 'label': 'Dining + Top Deck', 'rooms': ['Dining Room', 'Top Deck']}])
    @patch('sonos_control.views.time.sleep')
    @patch('sonos_control.views.sonos.snapshot', return_value={'rooms': [], 'groups': [], 'presets': []})
    @patch('sonos_control.views.sonos.set_group', return_value='DR')
    def test_preset_action(self, set_group, snap, sleep):
        res = self.post({'action': 'preset', 'preset': 'dining_deck'})
        set_group.assert_called_once_with(['Dining Room', 'Top Deck'])
        self.assertEqual(res.json()['selected'], 'DR')

    @patch('sonos_control.views.sonos.snapshot', return_value={'rooms': [], 'groups': [], 'presets': []})
    @patch('sonos_control.views.sonos.set_room_volume')
    def test_room_volume_action(self, set_vol, snap):
        self.assertEqual(self.post({'action': 'room_volume', 'room': 'DR', 'volume': 20}).status_code, 200)
        set_vol.assert_called_once_with('DR', 20)

    @patch('sonos_control.views.time.sleep')
    @patch('sonos_control.views.sonos.snapshot', return_value={'rooms': [], 'groups': [], 'presets': []})
    @patch('sonos_control.views.sonos.set_rooms', return_value='DR')
    def test_set_rooms_action(self, set_rooms, snap, sleep):
        res = self.post({'action': 'set_rooms', 'room': 'DR', 'rooms': ['DR', 'HT']})
        self.assertEqual(res.status_code, 200)
        set_rooms.assert_called_once_with('DR', ['DR', 'HT'])

    @patch('sonos_control.views.sonos.set_rooms', side_effect=RuntimeError('timeout'))
    def test_speaker_failure_is_reported(self, set_rooms):
        self.assertEqual(self.post({'action': 'set_rooms', 'room': 'HT', 'rooms': ['HT', 'DR']}).status_code, 502)

def snap(*states):
    """A snapshot with groups DR (Dining Room + Top Deck) and HT (Hot Tub)."""
    rooms = [{'uid': 'DR', 'label': 'Dining'}, {'uid': 'TD', 'label': 'Top Deck'},
             {'uid': 'HT', 'label': 'Hot Tub'}]
    groups = [{'id': 'DR', 'members': ['DR', 'TD'], 'state': states[0], 'title': 'Song',
               'artist': 'Band', 'art': 'http://10.0.0.5:1400/getaa?x'},
              {'id': 'HT', 'members': ['HT'], 'state': states[1], 'title': '', 'artist': '', 'art': ''}]
    return {'rooms': rooms, 'groups': groups}


class HomepageTileTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.now = 1000.0
        p = patch('sonos_control.homepage.time.time', side_effect=lambda: self.now)
        p.start()
        self.addCleanup(p.stop)

    def status(self, *states):
        from sonos_control import homepage
        with patch('sonos_control.homepage.sonos.snapshot', return_value=snap(*states)):
            return homepage.status()

    def test_lists_only_playing_groups(self):
        s = self.status('PLAYING', 'PAUSED_PLAYBACK')
        self.assertEqual(s.text, '1 playing')
        self.assertEqual(s.refresh, 3)
        (row,) = s.rows
        self.assertEqual((row.id, row.title, row.detail), ('DR', 'Dining + Top Deck', 'Song – Band'))
        self.assertEqual([a.id for a in row.actions], ['pause', 'next'])
        self.assertEqual([a.id for a in s.actions], ['pause_all'])

    def test_nothing_playing_shows_no_status(self):
        self.assertIsNone(self.status('PAUSED_PLAYBACK', 'STOPPED'))

    def test_paused_group_stays_dimmed_during_grace(self):
        self.status('PLAYING', 'STOPPED')
        self.now += 60
        s = self.status('PAUSED_PLAYBACK', 'STOPPED')
        self.assertEqual(s.text, 'Paused')
        self.assertTrue(s.rows[0].dimmed)
        self.assertEqual(s.rows[0].actions[0].id, 'play')
        self.now += 300
        self.assertIsNone(self.status('PAUSED_PLAYBACK', 'STOPPED'))

    @override_settings(BDH_SONOS_CONTROL_PAUSE_GRACE=1)
    def test_grace_setting(self):
        self.status('PLAYING', 'STOPPED')
        self.now += 30
        self.assertIsNotNone(self.status('PAUSED_PLAYBACK', 'STOPPED'))
        self.now += 59
        self.assertIsNotNone(self.status('PAUSED_PLAYBACK', 'STOPPED'))
        self.now += 2
        self.assertIsNone(self.status('PAUSED_PLAYBACK', 'STOPPED'))

    @patch('sonos_control.homepage.sonos.transport')
    def test_row_pause_shows_at_once(self, transport):
        from sonos_control import homepage
        self.status('PLAYING', 'STOPPED')
        homepage.run_action('DR', 'pause')
        transport.assert_called_once_with('DR', 'pause')
        s = self.status('PLAYING', 'STOPPED')  # speakers haven't caught up yet
        self.assertTrue(s.rows[0].dimmed)

    @patch('sonos_control.homepage.sonos.transport')
    @patch('sonos_control.homepage.sonos.pause_all')
    def test_pause_all_then_resume(self, pause_all, transport):
        from sonos_control import homepage
        self.status('PLAYING', 'PLAYING')
        homepage.run_action('', 'pause_all')
        pause_all.assert_called_once()
        self.now += 10
        s = self.status('PAUSED_PLAYBACK', 'PAUSED_PLAYBACK')
        self.assertEqual([a.id for a in s.actions], ['resume'])
        homepage.run_action('', 'resume')
        self.assertEqual(sorted(c.args for c in transport.call_args_list), [('DR', 'play'), ('HT', 'play')])

    def test_unknown_action(self):
        from sonos_control import homepage
        with self.assertRaises(sonos.SonosError):
            homepage.run_action('DR', 'format_disk')

    def test_snapshot_failure_hides_status(self):
        from sonos_control import homepage
        with patch('sonos_control.homepage.sonos.snapshot', side_effect=OSError('down')):
            self.assertIsNone(homepage.status())