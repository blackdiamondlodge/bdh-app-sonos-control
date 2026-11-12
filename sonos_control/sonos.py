"""Sonos service layer: cached discovery, shared state snapshots and actions.

Every browser polls the same cached snapshot, so the speakers see a bounded
amount of traffic no matter how many screens are open.
"""
import json
import logging
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import soco
from bdh_sdk.plugins import as_list
from bdh_sdk.settings import app_setting
from bdh_sdk.storage import app_state

log = logging.getLogger(__name__)

DISCOVERY_TTL = 60
SNAPSHOT_TTL = 1.5
NIGHT_TTL = 30

# Rooms and quick groups come from the site's settings (see README):
#   rooms:   {sonos room name: {label, floor, icon}}; floor is upper / main / lower
#            (inside the house diagram) or outside_main / outside_lower. With no
#            floors set, the room picker shows a plain grid instead.
#   presets: [{id, label, icon, rooms: [room names] or "*"}]
DEFAULT_PRESETS = [{'id': 'all', 'label': 'Everywhere', 'icon': 'fa-house', 'rooms': '*'}]

FLOOR_ORDER = ['upper', 'main', 'outside_main', 'lower', 'outside_lower', 'other']

_lock = threading.Lock()
_found = {}           # uid -> SoCo, every room on the network
_found_at = 0.0
_zones = {}           # uid -> SoCo, the rooms this hub controls
_zones_at = 0.0
_zones_for = None     # the picked list _zones was filtered with
_snapshot = None
_snapshot_at = 0.0
_night = None
_night_at = 0.0
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='sonos')


class SonosError(Exception):
    pass


def _discover_sonos(**kwargs):
    """Discover Sonos zones, optionally binding to a specific interface.

    Set BDH_SONOS_CONTROL_INTERFACE (legacy SONOS_INTERFACE) to the IP of the
    interface on the Sonos LAN (needed in Docker with host networking, where
    soco may pick the wrong interface).
    """
    interface = app_setting('sonos_control', 'interface', '', legacy=('SONOS_INTERFACE',))
    if interface:
        kwargs.setdefault('interface_addr', interface)
    return soco.discover(**kwargs)


def _structured(key, default):
    """A dict/list setting; environment values are JSON."""
    value = app_setting('sonos_control', key, default)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            log.warning('sonos_control %s is not valid JSON; using the default', key)
            return default
    return value


def room_layout():
    return _structured('rooms', {})


def presets():
    return _structured('presets', DEFAULT_PRESETS)


def _model_filter():
    """Lower-cased model-name fragments to control, or None for every model ("*")."""
    raw = app_setting('sonos_control', 'models', 'Amp')
    models = [m.strip().lower() for m in str(raw).split(',') if m.strip()]
    return None if '*' in models else models


def picked_speakers():
    """Room uids ticked on the Settings page, or None if never picked."""
    picked = app_setting('sonos_control', 'speakers', None, cast=as_list)
    return None if picked is None else list(picked)


def _model(zone):
    try:
        return zone.get_speaker_info().get('model_name') or ''
    except Exception:  # noqa: BLE001 - speaker unreachable
        return ''


def _wanted(zone, picked):
    if picked is not None:
        return zone.uid in picked
    models = _model_filter()
    if models is None:
        return True
    model = _model(zone).lower()
    return bool(model) and any(m in model for m in models)


def _discover_all(force=False):
    """{uid: SoCo} for every room on the network (bonded speakers count once)."""
    global _found, _found_at
    with _lock:
        if not force and _found and time.monotonic() - _found_at < DISCOVERY_TTL:
            return dict(_found)
    found = _discover_sonos(timeout=3) or set()
    with _lock:
        if found or not _found:
            _found = {z.uid: z for z in found}
        _found_at = time.monotonic()
        return dict(_found)


def zones(force=False):
    """Return {uid: SoCo} for the rooms this hub controls.

    Those ticked under "Speakers" on the Settings page; until that is saved,
    the rooms whose model matches the ``models`` setting (amps by default).
    """
    global _zones, _zones_at, _zones_for
    picked = picked_speakers()
    with _lock:
        fresh = _zones and time.monotonic() - _zones_at < DISCOVERY_TTL
        if not force and fresh and picked == _zones_for:
            return dict(_zones)
    found = [z for z in _discover_all(force).values() if _wanted(z, picked)]
    with _lock:
        if found or not _zones or picked is not None:
            _zones = {z.uid: z for z in found}
        _zones_at = time.monotonic()
        _zones_for = picked
        return dict(_zones)


def speaker_choices():
    """Every room on the network for the Settings page, plus ticked ones now offline."""
    state = app_state('sonos_control')
    known = state.get('speaker_names') or {}
    choices = {}
    for uid, z in _discover_all().items():
        model = _model(z)
        choices[uid] = f'{z.player_name} ({model})' if model else z.player_name
    if any(known.get(u) != n for u, n in choices.items()):
        state.set('speaker_names', {**known, **choices})
    for uid in picked_speakers() or ():
        if uid not in choices:
            choices[uid] = f'{known.get(uid, uid)} \u2013 offline'
    return sorted(choices.items(), key=lambda c: c[1].lower())


def _meta(name):
    m = room_layout().get(name) or {}
    return {'label': m.get('label') or name, 'floor': m.get('floor') or 'other',
            'icon': m.get('icon') or 'fa-volume-high'}


def source_for(uri, track):
    uri = (uri or '').lower()
    if not uri:
        return None
    if 'spotify' in uri:
        return {'name': 'Spotify Connect', 'icon': 'fa-spotify', 'brand': True}
    if 'airplay' in uri:
        return {'name': 'AirPlay', 'icon': 'fa-apple', 'brand': True}
    if uri.startswith('x-sonos-htastream'):
        return {'name': 'TV', 'icon': 'fa-tv', 'brand': False}
    if uri.startswith('x-rincon-stream'):
        return {'name': 'Line-in', 'icon': 'fa-plug', 'brand': False}
    if 'apple' in uri or 'sid=204' in uri:
        return {'name': 'Apple Music', 'icon': 'fa-apple', 'brand': True}
    if 'youtube' in uri or 'sid=284' in uri:
        return {'name': 'YouTube Music', 'icon': 'fa-youtube', 'brand': True}
    if 'mp3radio' in uri or 'sonosapi-stream' in uri or 'sonosapi-radio' in uri:
        return {'name': 'Radio', 'icon': 'fa-radio', 'brand': False}
    return {'name': 'Sonos', 'icon': 'fa-music', 'brand': False}


def _is_input(uri):
    """True for a soundbar's TV input or line-in: there is nothing to pause."""
    uri = (uri or '').lower()
    return uri.startswith('x-sonos-htastream') or uri.startswith('x-rincon-stream')


def _room_state(zone):
    try:
        return {'volume': zone.volume, 'muted': zone.mute, 'online': True}
    except Exception:  # noqa: BLE001
        return {'volume': 0, 'muted': False, 'online': False}


def _group_state(coord):
    out = {'state': 'STOPPED', 'title': '', 'artist': '', 'album': '', 'art': '',
           'source': None, 'volume': None, 'tv': False}
    try:
        out['state'] = coord.get_current_transport_info().get('current_transport_state', 'STOPPED')
        t = coord.get_current_track_info()
        uri = t.get('uri', '')
        try:
            media_uri = coord.get_current_media_info().get('uri', '')
        except Exception:  # noqa: BLE001
            media_uri = ''
        out.update(title=t.get('title') or '', artist=t.get('artist') or '',
                   album=t.get('album') or '', art=t.get('album_art') or '',
                   source=source_for(media_uri or uri, t))
        if uri.startswith('x-sonos-htastream'):
            out['title'] = out['title'] or 'TV audio'
        out['tv'] = _is_input(media_uri or uri)
    except Exception:  # noqa: BLE001
        log.debug('group state failed for %s', coord.player_name, exc_info=True)
    try:
        out['volume'] = coord.group.volume
    except Exception:  # noqa: BLE001
        pass
    return out


def _build_snapshot():
    zs = zones()
    if not zs:
        return {'rooms': [], 'groups': [], 'presets': presets(), 'ts': time.time()}

    any_zone = next(iter(zs.values()))
    try:
        any_zone.zone_group_state.clear_cache()
    except Exception:  # noqa: BLE001
        pass

    try:
        groups = list(any_zone.all_groups)
    except Exception:  # noqa: BLE001
        groups = []
    # Rooms not picked on the Settings page show up only as greyed-out
    # members of a group that also has a picked room.
    coord_of, guests, coord_zone = {}, {}, {}
    for g in groups:
        members = list(g.members)
        if not any(m.uid in zs for m in members):
            continue
        coord_zone[g.coordinator.uid] = g.coordinator
        for m in members:
            coord_of[m.uid] = g.coordinator.uid
            if m.uid not in zs:
                guests[m.uid] = m
    for uid in zs:
        coord_of.setdefault(uid, uid)

    every = {**guests, **zs}
    room_futs = {uid: _pool.submit(_room_state, z) for uid, z in every.items()}
    coords = {}
    for uid, cuid in coord_of.items():
        coords.setdefault(cuid, []).append(uid)

    def zone_for(uid):
        return every.get(uid) or coord_zone.get(uid)

    group_futs = {c: _pool.submit(_group_state, zone_for(c)) for c in coords if zone_for(c)}

    rooms = []
    for uid, z in every.items():
        meta = _meta(z.player_name)
        st = room_futs[uid].result()
        rooms.append({'uid': uid, 'name': z.player_name, 'label': meta['label'],
                      'floor': meta['floor'], 'icon': meta['icon'],
                      'group': coord_of[uid], 'shown': uid in zs, **st})
    order = {f: i for i, f in enumerate(FLOOR_ORDER)}
    rooms.sort(key=lambda r: (not r['shown'], order.get(r['floor'], 99), r['label']))
    room_rank = {r['uid']: i for i, r in enumerate(rooms)}

    out_groups = []
    for cuid, members in coords.items():
        gs = group_futs[cuid].result() if cuid in group_futs else {}
        members = sorted(members, key=lambda u: room_rank[u])
        vols = [r['volume'] for r in rooms if r['uid'] in members]
        if gs.get('volume') is None:
            gs['volume'] = round(sum(vols) / len(vols)) if vols else 0
        out_groups.append({'id': cuid, 'members': members, **gs})
    out_groups.sort(key=lambda g: room_rank[g['members'][0]])
    return {'rooms': rooms, 'groups': out_groups, 'presets': presets(),
            'night': night_status(), 'ts': time.time()}


def night_status():
    """Night volume limit from the appdaemon-sonos-night app, or None.

    BDH_SONOS_CONTROL_NIGHT_URL points at its status endpoint. The limit is
    enforced there; the hub only shows it and caps its own sliders.
    """
    global _night, _night_at
    url = app_setting('sonos_control', 'night_url', '')
    if not url:
        return None
    if time.monotonic() - _night_at < NIGHT_TTL:
        return _night
    try:
        with urllib.request.urlopen(url, timeout=2) as res:
            data = json.loads(res.read())
        _night = {'active': bool(data.get('active')), 'limit': int(data['limit']),
                  'end': str(data.get('end', '')), 'rooms': list(data.get('rooms') or [])}
    except Exception as exc:  # noqa: BLE001
        log.warning('night limit status failed: %s', exc)
        _night = None
    _night_at = time.monotonic()
    return _night


def _night_cap(zones_):
    """The night limit if every one of these zones is under it, else None."""
    night = night_status()
    if not night or not night['active']:
        return None
    names = set(night['rooms'])
    return night['limit'] if all(z.player_name in names for z in zones_) else None


def snapshot(force=False):
    global _snapshot, _snapshot_at
    with _lock:
        fresh = _snapshot and time.monotonic() - _snapshot_at < SNAPSHOT_TTL
        if fresh and not force:
            return _snapshot
    snap = _build_snapshot()
    with _lock:
        _snapshot, _snapshot_at = snap, time.monotonic()
    return snap


def invalidate():
    global _snapshot_at
    with _lock:
        _snapshot_at = 0.0


# ---------------------------------------------------------------- actions

def _zone(uid):
    z = zones().get(uid)
    if z is None:
        raise SonosError('Unknown room')
    return z


def _group(group_id):
    """The group with this id (its coordinator's uid) that has a picked room."""
    zs = zones()
    if group_id in zs:
        return zs[group_id].group
    for z in zs.values():
        if z.group.coordinator.uid == group_id:
            return z.group
    raise SonosError('Unknown group')


def _leave(z):
    """Remove a room from its group without stopping the others."""
    group = z.group
    members = [m for m in group.members if m.uid != z.uid]
    if not members:
        return
    if group.coordinator.uid == z.uid:
        heir = members[0]
        try:
            z.avTransport.DelegateGroupCoordinationTo([
                ('InstanceID', 0), ('NewCoordinator', heir.uid), ('RejoinGroup', 0)])
            return
        except Exception:  # noqa: BLE001 - fall back to re-forming the group
            log.info('delegate failed; re-forming group', exc_info=True)
            z.unjoin()
            for m in members[1:]:
                m.join(heir)
            return
    z.unjoin()


def join(uid, target_group):
    z = _zone(uid)
    coord = _zone(target_group)
    if coord.uid == z.uid:
        return
    if z.group.coordinator.uid == coord.group.coordinator.uid:
        return
    if z.is_coordinator and len(z.group.members) > 1:
        _leave(z)
    z.join(coord.group.coordinator)


def _refresh(z):
    try:
        z.zone_group_state.clear_cache()
    except Exception:  # noqa: BLE001
        pass


def set_rooms(room, uids):
    """Make the player that `room` belongs to contain exactly `uids`.

    Rooms pulled in from other groups leave them without stopping the music
    there. The player keeps its current coordinator (and what it is playing)
    whenever that room stays in.
    """
    zs = zones()
    wanted = [u for u in dict.fromkeys(uids or []) if u in zs]
    if not wanted:
        raise SonosError('Pick at least one room')
    group = _group(room)
    coord = group.coordinator
    current = [m.uid for m in group.members if m.uid in zs]
    kept = [u for u in current if u in wanted]
    anchor = coord if coord.uid in wanted else zs[(kept or wanted)[0]]

    # Drop unticked rooms; the coordinator goes last so its hand-off lands on a kept room.
    drop = sorted((zs[u] for u in current if u not in wanted), key=lambda z: z.uid == coord.uid)
    for z in drop:
        _leave(z)
    if coord.uid not in wanted and kept:
        time.sleep(0.5)  # let the coordinator hand-off settle
    _refresh(anchor)

    if anchor.uid not in current and len(anchor.group.members) > 1:
        _leave(anchor)
        _refresh(anchor)
    for u in wanted:
        if u != anchor.uid:
            join(u, anchor.uid)
    _refresh(anchor)
    return anchor.group.coordinator.uid


def leave(uid):
    _leave(_zone(uid))


def set_group(names):
    """Group exactly the given rooms (all rooms when names == '*')."""
    zs = zones()
    targets = [z for z in zs.values() if names == '*' or z.player_name in names]
    if not targets:
        raise SonosError('No matching rooms')
    target_ids = {z.uid for z in targets}

    def playing(z):
        try:
            return z.get_current_transport_info()['current_transport_state'] == 'PLAYING'
        except Exception:  # noqa: BLE001
            return False

    coords = [z for z in targets if z.is_coordinator]
    coord = next((z for z in coords if playing(z)), None) or (coords[0] if coords else targets[0])
    if not coord.is_coordinator:
        _leave(coord)
    for m in list(coord.group.members):
        if m.uid not in target_ids and m.uid != coord.uid:
            _leave(m)
    for z in targets:
        if z.uid != coord.uid:
            join(z.uid, coord.uid)
    return coord.uid


def _transport_state(z):
    try:
        return z.get_current_transport_info()['current_transport_state']
    except Exception:  # noqa: BLE001
        return ''


def _split(group, shown):
    """Give every room in the group its own player.

    If the coordinator is a room that is not picked on the Settings page it
    would vanish from the hub still playing, so it is paused.
    """
    coord = group.coordinator
    for m in list(group.members):
        if m.uid != coord.uid:
            m.unjoin()
    if coord.uid not in shown and _transport_state(coord) == 'PLAYING':
        try:
            coord.pause()
        except Exception:  # noqa: BLE001
            log.info('pause failed for %s', coord.player_name, exc_info=True)


def ungroup(group_id):
    _split(_group(group_id), zones())


def ungroup_all():
    zs = zones()
    seen = set()
    for z in zs.values():
        g = z.group
        if g.coordinator.uid in seen or len(g.members) < 2:
            continue
        seen.add(g.coordinator.uid)
        _split(g, zs)


def _on_input(z):
    try:
        uri = z.get_current_media_info().get('uri', '') or z.get_current_track_info().get('uri', '')
    except Exception:  # noqa: BLE001
        return False
    return _is_input(uri)


def pause_all():
    """Pause every playing group with a picked room, except TV / line-in."""
    seen = set()
    for z in zones().values():
        c = z.group.coordinator
        if c.uid in seen:
            continue
        seen.add(c.uid)
        try:
            if _transport_state(c) == 'PLAYING' and not _on_input(c):
                c.pause()
        except Exception:  # noqa: BLE001
            log.info('pause failed for %s', c.player_name, exc_info=True)


def transport(group_id, op):
    c = _group(group_id).coordinator
    if op in ('play', 'pause') and _on_input(c):
        raise SonosError('This speaker is playing the TV')
    if op == 'play':
        c.play()
    elif op == 'pause':
        c.pause()
    elif op == 'next':
        c.next()
    elif op == 'previous':
        c.previous()
    else:
        raise SonosError('Unknown transport op')


def set_group_volume(group_id, volume):
    group = _group(group_id)
    group.volume = _clamp(volume, _night_cap(list(group.members)))


def set_room_volume(uid, volume):
    z = _zone(uid)
    z.volume = _clamp(volume, _night_cap([z]))


def set_room_mute(uid, muted):
    _zone(uid).mute = bool(muted)


def _clamp(v, cap=None):
    return max(0, min(100 if cap is None else cap, int(v)))
