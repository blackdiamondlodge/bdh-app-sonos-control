"""Homepage tile: the speaker groups playing now, with play/pause and skip.

Sonos has no real "stopped": idle speakers sit paused for months. So the tile
lists groups that are playing, plus groups that stopped playing within the
last ``pause_grace`` minutes (so a quick pause can be undone from the tile).
Groups that were already paused when we first saw them are never listed.
"""
import logging
import time

from bdh_sdk import ON, Action, Status, StatusRow
from bdh_sdk.settings import app_setting
from django.core.cache import cache

from . import sonos

log = logging.getLogger(__name__)

# Shared through the cache so every hub worker agrees on what's "recent".
STATE_KEY = 'sonos_control:tile'
STATE_TTL = 24 * 3600
# How long a button press wins over what the speakers report, while they
# catch up.
EXPECT_SECONDS = 4
REFRESH = 3
PLAYING = frozenset({'PLAYING', 'TRANSITIONING'})

PAUSE = Action('pause', 'fa-solid fa-pause', 'Pause', primary=True)
PLAY = Action('play', 'fa-solid fa-play', 'Play', primary=True)
NEXT = Action('next', 'fa-solid fa-forward-step', 'Next')
PAUSE_ALL = Action('pause_all', 'fa-solid fa-pause', 'Pause everything', primary=True)
RESUME = Action('resume', 'fa-solid fa-play', 'Resume', primary=True)


def _grace():
    try:
        return max(0, int(app_setting('sonos_control', 'pause_grace', 5))) * 60
    except (TypeError, ValueError):
        return 300


def _load():
    return cache.get(STATE_KEY) or {'playing': {}, 'paused': {}, 'expect': {}, 'resume': {}}


def _save(state):
    cache.set(STATE_KEY, state, STATE_TTL)


def _music(snap):
    """Groups that can be paused: a soundbar on its TV input is left off the tile."""
    return [g for g in snap.get('groups', ()) if not g.get('tv')]


def _playing(group, state, now):
    expect = state['expect'].get(group['id'])
    if expect and now < expect[1]:
        return expect[0] == 'play'
    return group.get('state') in PLAYING


def _track(snap, now):
    """Update which groups are playing or recently paused; return the state."""
    state = _load()
    grace = _grace()
    ids = set()
    for g in _music(snap):
        gid = g['id']
        ids.add(gid)
        if _playing(g, state, now):
            state['playing'][gid] = now
            state['paused'].pop(gid, None)
        elif gid in state['playing']:
            state['playing'].pop(gid)
            state['paused'][gid] = now
    for key in ('playing', 'paused'):
        state[key] = {g: t for g, t in state[key].items() if g in ids}
    state['paused'] = {g: t for g, t in state['paused'].items() if now - t < grace}
    state['expect'] = {g: e for g, e in state['expect'].items() if now < e[1]}
    state['resume'] = {g: t for g, t in state['resume'].items()
                       if g in state['paused'] and now - t < grace}
    _save(state)
    return state


def _row(group, labels, playing):
    title = ' + '.join(labels.get(u, u) for u in group['members'])
    detail = ' – '.join(x for x in (group.get('title'), group.get('artist')) if x)
    return StatusRow(
        id=group['id'], title=title, detail=detail, image=group.get('art') or '',
        dimmed=not playing, actions=(PAUSE if playing else PLAY, NEXT),
    )


def status():
    try:
        snap = sonos.snapshot()
    except Exception:  # noqa: BLE001
        log.info('sonos snapshot failed for the homepage', exc_info=True)
        return None
    now = time.time()
    state = _track(snap, now)
    labels = {r['uid']: r['label'] for r in snap.get('rooms', ())}
    rows, playing = [], 0
    for g in _music(snap):
        on = g['id'] in state['playing']
        if on or g['id'] in state['paused']:
            rows.append(_row(g, labels, on))
            playing += on
    if not rows:
        return None
    if playing:
        return Status(f'{playing} playing', level=ON, rows=tuple(rows),
                      actions=(PAUSE_ALL,), refresh=REFRESH)
    return Status('Paused', rows=tuple(rows),
                  actions=(RESUME,) if state['resume'] else (), refresh=REFRESH)


def _expect(state, gid, op, now):
    state['expect'][gid] = (op, now + EXPECT_SECONDS)


def run_action(row_id, action_id):
    now = time.time()
    state = _load()
    try:
        if row_id:
            if action_id not in ('play', 'pause', 'next'):
                raise sonos.SonosError('Unknown action')
            sonos.transport(row_id, action_id)
            if action_id != 'next':
                _expect(state, row_id, action_id, now)
            state['resume'].pop(row_id, None)
        elif action_id == 'pause_all':
            playing = list(state['playing'])
            sonos.pause_all()
            for gid in playing:
                _expect(state, gid, 'pause', now)
            state['resume'] = dict.fromkeys(playing, now)
        elif action_id == 'resume':
            for gid in list(state['resume']):
                try:
                    sonos.transport(gid, 'play')
                    _expect(state, gid, 'play', now)
                except Exception:  # noqa: BLE001
                    log.info('resume failed for %s', gid, exc_info=True)
            state['resume'] = {}
        else:
            raise sonos.SonosError('Unknown action')
    finally:
        _save(state)
        sonos.invalidate()
    return None
