"""Reserve landing site reachability, checked at the tightest point of a flown route.

Approximates a diversion as a free-space shortest path at the mission's own
cruise speed under the same wind model as the rest of the route. It ignores
turn radius for fixed-wing aircraft and any additional loss of height/energy
from an unplanned maneuver, so a positive margin is not a certified go/no-go.
"""
import math
from bas.planning.flight_models.multirotor import duration

GROUNDED_PHASES = {'takeoff', 'climb', 'descent', 'parachute', 'landing', 'hold'}


def reachability(vehicle, legs, start_s, geo, sites, weather):
    alternates = [site for site in sites if site.kind == 'alternate']
    if not alternates:
        return dict(checked=False, reachable=None, site_id=None, margin_s=None)
    targets = [(site.id, tuple(geo.forward(*site.coordinates))) for site in alternates]
    worst_margin, worst_site = None, None
    for leg in legs:
        if leg['phase'] in GROUNDED_PHASES:
            continue
        remaining = vehicle['budget'] - (leg['end'] - start_s)
        if remaining <= 0:
            continue
        point = tuple(leg['xy'][-1])
        best_margin, best_site = None, None
        for site_id, target in targets:
            path = geo.path(point, target)
            if path is None:
                continue
            transit = sum(duration(a, b, vehicle['speed'], weather.wind_speed_mps, weather.wind_from_deg)
                          for a, b in zip(path, path[1:]))
            if not math.isfinite(transit):
                continue
            margin = remaining - transit
            if best_margin is None or margin > best_margin:
                best_margin, best_site = margin, site_id
        # No alternate reachable at all from this point is itself the worst case.
        margin, site_id = (best_margin, best_site) if best_margin is not None else (-math.inf, None)
        if worst_margin is None or margin < worst_margin:
            worst_margin, worst_site = margin, site_id
    if worst_margin is None:
        return dict(checked=True, reachable=None, site_id=None, margin_s=None)
    return dict(checked=True, reachable=math.isfinite(worst_margin) and worst_margin >= 0,
                site_id=worst_site, margin_s=worst_margin if math.isfinite(worst_margin) else None)
