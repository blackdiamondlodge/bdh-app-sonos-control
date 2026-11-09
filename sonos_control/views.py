import json
import logging
import time

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import sonos

log = logging.getLogger(__name__)

# Grouping changes take a moment to show up in the zone group state.
SETTLE_SECONDS = 0.6


def sonos_control_view(request):
    return render(request, 'sonos_control/sonos.html')


@require_GET
def state(request):
    try:
        return JsonResponse(sonos.snapshot())
    except Exception:  # noqa: BLE001
        log.exception('sonos snapshot failed')
        return JsonResponse({'error': 'Could not reach the speakers'}, status=503)


ACTIONS = {
    'set_rooms': (lambda d: sonos.set_rooms(d['room'], d['rooms']), True),
    'preset': (lambda d: sonos.set_group(_preset_rooms(d['preset'])), True),
    'ungroup_all': (lambda d: sonos.ungroup_all(), True),
    'pause_all': (lambda d: sonos.pause_all(), False),
    'transport': (lambda d: sonos.transport(d['group'], d['op']), False),
    'group_volume': (lambda d: sonos.set_group_volume(d['group'], d['volume']), False),
    'room_volume': (lambda d: sonos.set_room_volume(d['room'], d['volume']), False),
    'room_mute': (lambda d: sonos.set_room_mute(d['room'], d['muted']), False),
}


def _preset_rooms(preset_id):
    for p in sonos.PRESETS:
        if p['id'] == preset_id:
            return p['rooms']
    raise sonos.SonosError('Unknown preset')


@require_POST
def action(request):
    try:
        data = json.loads(request.body or b'{}')
        fn, regroups = ACTIONS[data['action']]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Bad request'}, status=400)
    result = None
    try:
        result = fn(data)
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'error': 'Bad request'}, status=400)
    except sonos.SonosError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception:  # noqa: BLE001
        log.exception('sonos action %s failed', data.get('action'))
        return JsonResponse({'error': 'The speakers did not respond'}, status=502)
    if regroups:
        time.sleep(SETTLE_SECONDS)
    sonos.invalidate()
    snap = sonos.snapshot(force=True)
    if isinstance(result, str):
        snap = {**snap, 'selected': result}
    return JsonResponse(snap)