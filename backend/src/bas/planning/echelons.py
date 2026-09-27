"""Automatic echelons: aircraft whose routes come close fly at different levels.

A first plan puts every aircraft without a level of its own at the survey altitude. For every pair
of aircraft the planner measures how much of their cruise tracks run within the horizontal
separation of each other: the corridors they would otherwise have to share in time. Levels go up
from the survey altitude in steps of the vertical separation and stay
- below the ceiling (150 m above the surface by default: Russian rules allow UAV flights without
  an airspace permit below it, among other conditions). Levels are heights above the ground under
  the aircraft, as flight tasks in Geoscan Planner are built over the terrain;
- below every restriction zone the first plan flew under, so echelons never add a blocker;
- within the camera's GSD limit, and at 100 m or more for the demonstration 201.
Levels only go up from the survey altitude: a lower level would narrow the common strip width.
A multirotor also needs the budget for the longer climb and descent in every sortie. Planes keep
the level they were planned at: their climb path, and so the whole route, depends on the height.
Aircraft are placed greedily, most entangled first, on the lowest level that shares the least
corridor with aircraft already there; single moves then improve the assignment while they help.
The planner then flies the same routes at the new levels. Separation by time alone is fragile:
a real aircraft running late breaks it, while a level apart keeps working whatever the timing. So
the levels are kept when they shorten the corridors shared at one level and cost at most
TOLERANCE of the objective (the extra climb and descent).
"""
import math

from shapely.geometry import MultiLineString

from bas.catalog.cameras import camera_geometry
from bas.planning.geometry import PlanningError

CRUISE = ('transit', 'survey', 'turn', 'hold')
# Largest relative cost of the objective at which echelons are kept.
TOLERANCE = 0.05


def candidate_levels(settings, report):
    base, step = settings.altitude_m, settings.vertical_separation_m
    top = min(settings.altitude_ceiling_m, 300.)
    for zone in report['zones_above']:
        if zone['lower_agl_m'] is not None:
            top = min(top, zone['lower_agl_m'] - settings.vertical_buffer_m)
    count = int(math.floor((top - base) / step + 1e-9)) + 1 if top >= base else 1
    return [base + k * step for k in range(count)]


def allowed_levels(aircraft, profile, settings, levels):
    allowed = []
    for level in levels:
        if profile.airframe == 'fixed_wing' and level < 100:
            continue
        if settings.coverage_mode == 'camera':
            try:
                camera_geometry(aircraft, profile, settings, level)
            except PlanningError:
                continue
        allowed.append(level)
    return allowed or levels[:1]


def shared_corridors(missions, separation):
    """{(a, b): metres of cruise track of a and b closer than `separation`}, a < b."""
    tracks = {}
    for mission in missions:
        tracks.setdefault(mission['aircraft_id'], []).extend(
            [tuple(leg['xy'][0]), tuple(leg['xy'][-1])] for leg in mission['legs']
            if leg['phase'] in CRUISE and math.dist(leg['xy'][0], leg['xy'][-1]) > 1e-6)
    zones = {key: MultiLineString(lines).buffer(separation / 2) for key, lines in tracks.items() if lines}
    ids = sorted(zones)
    shared = {}
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            if zones[a].intersects(zones[b]):
                shared[(a, b)] = zones[a].intersection(zones[b]).area / separation
    return shared


def same_level(shared, levels, step):
    """Metres of shared corridor between aircraft closer in height than `step`: separated by time only."""
    return sum(metres for (a, b), metres in shared.items() if abs(levels[a] - levels[b]) < step - 1e-9)


def assign(order, fixed, options, shared, step):
    level = dict(fixed)

    def weight(a, b):
        return shared.get((a, b), shared.get((b, a), 0.))

    def cost(a, height):
        return sum(weight(a, b) for b, other in level.items() if b != a and abs(other - height) < step - 1e-9)
    ranked = sorted(options, key=lambda a: (-sum(weight(a, b) for b in order if b != a), order.index(a)))
    for a in ranked:
        level[a] = min(options[a], key=lambda height: (cost(a, height), height))
    for _ in range(20):
        moved = False
        for a in ranked:
            best = min(options[a], key=lambda height: (cost(a, height), height))
            if cost(a, best) < cost(a, level[a]) - 1e-9:
                level[a], moved = best, True
        if not moved:
            break
    return level


def assign_echelons(scenario, profiles, run, altitudes, requested, separation):
    """Levels for aircraft without their own, from the first plan `run`: (altitudes, report)."""
    settings = scenario.settings
    levels = candidate_levels(settings, run['airspace_report'])
    shared = shared_corridors(run['missions'], separation)
    order = [a.id for a in scenario.fleet]
    fixed = {a.id: altitudes[a.id] for a in scenario.fleet if requested[a.id] is not None}
    options = {}
    for a in scenario.fleet:
        if requested[a.id] is not None:
            continue
        if profiles[a.profile_id].airframe != 'multirotor':
            fixed[a.id] = altitudes[a.id]
            continue
        # Each metre up adds half a second of climb and half a second of descent to every sortie.
        slack = min((m['budget_s'] - m['flight_time_s'] for m in run['missions'] if m['aircraft_id'] == a.id), default=math.inf)
        options[a.id] = [h for h in allowed_levels(a, profiles[a.profile_id], settings, levels) if h - altitudes[a.id] <= slack + 1e-9] or [altitudes[a.id]]
    assigned = assign(order, fixed, options, shared, settings.vertical_separation_m)
    pairs = sorted(({'aircraft': [a, b], 'shared_m': round(metres, 1)} for (a, b), metres in shared.items() if metres >= 1),
                   key=lambda item: -item['shared_m'])
    report = dict(levels=levels, vertical_separation_m=settings.vertical_separation_m, ceiling_m=settings.altitude_ceiling_m,
                  manual=sorted(fixed), shared_pairs=pairs[:10], shared_total_m=round(sum(shared.values()), 1),
                  shared_same_level_before_m=round(same_level(shared, altitudes, settings.vertical_separation_m), 1),
                  shared_same_level_after_m=round(same_level(shared, assigned, settings.vertical_separation_m), 1))
    return {key: assigned[key] for key in order}, report
