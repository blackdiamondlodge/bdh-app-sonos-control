"""Sonos service layer: cached discovery, shared state snapshots and actions.

Every browser polls the same cached snapshot, so the speakers see a bounded
amount of traffic no matter how many screens are open.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import soco
from bdh_sdk.settings import app_setting

log = logging.getLogger(__name__)

DISCOVERY_TTL = 60
SNAPSHOT_TTL = 1.5

# Where each room sits on the lodge diagram. Keys are Sonos room names.
#   floor: upper / main / lower (inside) or outside_main / outside_lower
LAYOUT = {
    'Master Bedroom En-suite': {'label': 'Master En-suite', 'floor': 'upper', 'icon': 'fa-bath'},
    'Dining Room': {'label': 'Dining Room', 'floor': 'main', 'icon': 'fa-utensils'},
    'Basement Master En-suite': {'label': 'Basement En-suite', 'floor': 'lower', 'icon': 'fa-bath'},
    'Top Deck': {'label': 'Top Deck', 'floor': 'outside_main', 'icon': 'fa-tree'},
    'Lower Deck': {'label': 'Lower Deck', 'floor': 'outside_lower', 'icon': 'fa-chair'},
    'Hot Tub': {'label': 'Hot Tub', 'floor': 'outside_lower', 'icon': 'fa-hot-tub-person'},
}

PRESETS = [
    {'id': 'whole', 'label': 'Whole Lodge', 'icon': 'fa-house', 'rooms': '*'},
    {'id': 'outside', 'label': 'All Outside', 'icon': 'fa-tree',
     'rooms': ['Top Deck', 'Lower Deck', 'Hot Tub']},
    {'id': 'hottub', 'label': 'Deck + Hot Tub', 'icon': 'fa-hot-tub-person',
     'rooms': ['Lower Deck', 'Hot Tub']},
]

FLOOR_ORDER = ['upper', 'main', 'outside_main', 'lower', 'outside_lower', 'other']

_lock = threading.Lock()
_zones = {}           # uid -> SoCo
_zones_at = 0.0
_snapshot = None
_snapshot_at = 0.0
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


def _model_filter():
    raw = app_setting('sonos_control', 'models', 'Amp')
    return [m.strip().lower() for m in str(raw).split(',') if m.strip()]


def _wanted(zone):
    try:
        model = (zone.get_speaker_info().get('model_name') or '').lower()
    except Exception:  # noqa: BLE001 - speaker unreachable
        return False
    return any(m in model for m in _model_filter())


def zones(force=False):
    """Return {uid: SoCo} for the controllable rooms (amps by default)."""
    global _zones, _zones_at
    with _lock:
        if not force and _zones and time.monotonic() - _zones_at < DISCOVERY_TTL:
            return dict(_zones)
    found = _discover_sonos(timeout=3) or set()
    found = [z for z in found if _wanted(z)]
    with _lock:
        if found or not _zones:
            _zones = {z.uid: z for z in found}
        _zones_at = time.monotonic()
        return dict(_zones)


def _meta(name):
    m = LAYOUT.get(name)
    if m:
        return dict(m)
    return {'label': name, 'floor': 'other', 'icon': 'fa-volume-high'}


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


def _room_state(zone):
    try:
        return {'volume': zone.volume, 'muted': zone.mute, 'online': True}
    except Exception:  # noqa: BLE001
        return {'volume': 0, 'muted': False, 'online': False}


def _group_state(coord):
    out = {'state': 'STOPPED', 'title': '', 'artist': '', 'album': '', 'art': '',
           'source': None, 'volume': None}
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
        return {'rooms': [], 'groups': [], 'presets': PRESETS, 'ts': time.time()}

    any_zone = next(iter(zs.values()))
    try:
        any_zone.zone_group_state.clear_cache()
    except Exception:  # noqa: BLE001
        pass

    room_futs = {uid: _pool.submit(_room_state, z) for uid, z in zs.items()}
    coord_of = {}
    try:
        groups = list(any_zone.all_groups)
    except Exception:  # noqa: BLE001
        groups = []
    for g in groups:
        for m in g.members:
            if m.uid in zs:
                coord_of[m.uid] = g.coordinator.uid
    for uid in zs:
        coord_of.setdefault(uid, uid)

    coords = {}
    for uid, cuid in coord_of.items():
        coords.setdefault(cuid, []).append(uid)

    def zone_for(uid):
        if uid in zs:
            return zs[uid]
        for g in groups:
            if g.coordinator.uid == uid:
                return g.coordinator
        return None

    group_futs = {c: _pool.submit(_group_state, zone_for(c)) for c in coords if zone_for(c)}

    rooms = []
    for uid, z in zs.items():
        meta = _meta(z.player_name)
        st = room_futs[uid].result()
        rooms.append({'uid': uid, 'name': z.player_name, 'label': meta['label'],
                      'floor': meta['floor'], 'icon': meta['icon'],
                      'group': coord_of[uid], **st})
    order = {f: i for i, f in enumerate(FLOOR_ORDER)}
    rooms.sort(key=lambda r: (order.get(r['floor'], 99), r['label']))
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
    return {'rooms': rooms, 'groups': out_groups, 'presets': PRESETS, 'ts': time.time()}


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


def merge(source_group, target_group):
    """Move every room of one group into another (target keeps playing)."""
    target = _zone(target_group).group.coordinator
    members = [m for m in _zone(source_group).group.members if m.uid != target.uid]
    # Members first, then the old coordinator once it stands alone.
    for m in sorted(members, key=lambda m: m.is_coordinator):
        m.join(target)
    return target.uid


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


def ungroup_all():
    for z in zones().values():
        if len(z.group.members) > 1 and not z.is_coordinator:
            z.unjoin()


def pause_all():
    seen = set()
    for z in zones().values():
        c = z.group.coordinator
        if c.uid in seen:
            continue
        seen.add(c.uid)
        try:
            if c.get_current_transport_info()['current_transport_state'] == 'PLAYING':
                c.pause()
        except Exception:  # noqa: BLE001
            log.info('pause failed for %s', c.player_name, exc_info=True)


def transport(group_id, op):
    c = _zone(group_id).group.coordinator
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
    _zone(group_id).group.volume = _clamp(volume)


def set_room_volume(uid, volume):
    _zone(uid).volume = _clamp(volume)


def set_room_mute(uid, muted):
    _zone(uid).mute = bool(muted)


def _clamp(v):
    return max(0, min(100, int(v)))
