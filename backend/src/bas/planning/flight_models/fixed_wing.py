"""Demonstration fixed-wing kinematics; no autopilot or certified dynamics.

Turns are flown at constant bank: circles of the calm-air turn radius relative to the air mass,
which the wind turns into trochoids over the ground (see dubins.py). Connections between poses
take the shortest of the six Dubins words. Before a survey strip the aircraft flies a straight
lead-in so it is level when the camera starts. The climb after the catapult is straight along the
launch heading when that corridor is free and a constant-bank spiral otherwise, as the 201
autopilot changes height in a spiral when a straight line is too short. Recovery is a parachute
drop that drifts with the wind; with a release height below the flight level the aircraft first
circles down over the release point.
"""
import math

import shapely
from shapely.geometry import LineString, Point

from bas.planning.flight_models import dubins
from bas.planning.flight_models.dubins import ARC_ERROR_M
from bas.planning.flight_models.multirotor import duration
from bas.planning.geometry import reject

LAUNCH_RUN_M = 50
GRAVITY = 9.80665
TAU = 2 * math.pi


def bearing(a, b):
    return math.atan2(b[1] - a[1], b[0] - a[0])


def bank_deg(radius, airspeed):
    """Bank angle of a coordinated turn of `radius` at `airspeed` in still air."""
    return math.degrees(math.atan(airspeed ** 2 / (GRAVITY * radius)))


def _turn_space(geo):
    """Free space shrunk by the sampling error: a sampled turn inside it keeps the true arc inside free space."""
    space = getattr(geo, 'turn_space', None)
    if space is None:
        space = geo.free.buffer(-(ARC_ERROR_M + 1e-5))
        shapely.prepare(space)
        geo.turn_space = space
    return space


def _covered(geo, phase, points):
    line = LineString(points) if len(points) > 1 else Point(points[0])
    return (_turn_space(geo) if phase == 'turn' else geo.free).covers(line)


def _free(geo, a, b):
    return geo.free.covers(Point(a) if math.dist(a, b) < 1e-7 else LineString([a, b]))


def _connections(start, finish, radius, geo, speed, weather):
    wind = dubins.wind_vector(weather)
    candidates = []
    for _, parts in dubins.paths(start, finish, radius, speed, wind):
        legs = [dict(phase=phase, xy=[(x, y) for x, y, _ in points]) for phase, points in parts]
        if not all(_covered(geo, leg['phase'], leg['xy']) for leg in legs):
            continue
        cost = sum(duration(a, b, speed, weather.wind_speed_mps, weather.wind_from_deg) for leg in legs for a, b in zip(leg['xy'], leg['xy'][1:]))
        if math.isfinite(cost):
            candidates.append((cost, legs))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def connect(start, finish, radius, geo, speed, weather, lead_in=0.):
    """Fastest free path between ground-track poses; with `lead_in`, ending in a straight of that length.

    The lead-in is dropped where it would leave free space or leaves no path, so a strip next to a
    restriction can still be flown, entered from the turn.
    """
    if lead_in > 0:
        entry = (finish[0] - lead_in * math.cos(finish[2]), finish[1] - lead_in * math.sin(finish[2]))
        if _free(geo, entry, finish[:2]):
            legs = _connections(start, (*entry, finish[2]), radius, geo, speed, weather)
            if legs is not None:
                return legs + [dict(phase='transit', xy=[entry, (finish[0], finish[1])])]
    return _connections(start, finish, radius, geo, speed, weather)


def proxy_seconds(start, finish, radius, speed, wind, lead_in=0.):
    """Time of `connect` in the same wind without free-space checks: ranks strip orders cheaply."""
    tail = 0.
    if lead_in > 0:
        track = finish[2]
        finish = (finish[0] - lead_in * math.cos(track), finish[1] - lead_in * math.sin(track), track)
        heading = dubins.air_heading(track, wind, speed)
        if heading is None:
            return math.inf
        tail = lead_in / (speed * math.cos(heading - track) + wind[0] * math.cos(track) + wind[1] * math.sin(track))
    return dubins.fastest(start, finish, radius, speed, wind) + tail


def _ground_circle(centre_from, heading, radius, direction, count):
    """Ground circle through `centre_from` tangent to `heading`, sampled from that point back to it."""
    x, y = centre_from
    cx, cy = x - direction * radius * math.sin(heading), y + direction * radius * math.cos(heading)
    start = math.atan2(y - cy, x - cx)
    return [(cx + radius * math.cos(start + direction * TAU * i / count), cy + radius * math.sin(start + direction * TAU * i / count)) for i in range(count + 1)]


def landing(site, settings, weather, geo, heading, altitude, speed):
    """Parachute recovery onto `site`; None when the drift line or the descent circle leaves free space."""
    f = settings.fixed_wing
    release_altitude = altitude if f.parachute_release_m is None else min(f.parachute_release_m, altitude)
    fall_seconds = release_altitude / f.parachute_sink_mps
    wind = dubins.wind_vector(weather)
    release = (site[0] - wind[0] * fall_seconds, site[1] - wind[1] * fall_seconds)
    if not _free(geo, release, site):
        return None
    result = dict(end_pose=(*release, heading), release=release, fall_seconds=fall_seconds, release_altitude_m=release_altitude,
                  descent=None, descent_turns=0, descent_seconds=0.)
    if release_altitude < altitude - 1e-9:
        # A ground-referenced circle over the release point, as the autopilot holds a waiting point.
        # Its radius keeps the calm-air bank at the fastest ground speed, downwind.
        radius = f.turn_radius_m * ((speed + weather.wind_speed_mps) / speed) ** 2
        count = max(8, math.ceil(TAU / min(math.radians(5), 2 * math.acos(1 - ARC_ERROR_M / radius))))
        needed = (altitude - release_altitude) / f.climb_rate_mps
        for direction in (1, -1):
            circle = _ground_circle(release, heading, radius, direction, count)
            if not _covered(geo, 'turn', circle):
                continue
            lap = sum(duration(a, b, speed, weather.wind_speed_mps, weather.wind_from_deg) for a, b in zip(circle, circle[1:]))
            if not math.isfinite(lap):
                continue
            turns = max(1, math.ceil(needed / lap - 1e-9))
            result.update(descent=circle, descent_turns=turns, descent_seconds=turns * lap)
            break
        else:
            return None
    return result


def operations(home, settings, weather, speed, geo, altitude):
    """Catapult run, climb to `altitude` and parachute recovery at the aircraft's own site."""
    f = settings.fixed_wing
    heading_deg = f.launch_heading_deg if f.launch_heading_deg is not None else weather.wind_from_deg
    heading = math.radians(90 - heading_deg)
    unit = (math.cos(heading), math.sin(heading))
    ground = 1 / duration((0, 0), unit, speed, weather.wind_speed_mps, weather.wind_from_deg)
    climb_seconds = altitude / f.climb_rate_mps
    launch = (home[0] + LAUNCH_RUN_M * unit[0], home[1] + LAUNCH_RUN_M * unit[1])
    cruise = (launch[0] + ground * climb_seconds * unit[0], launch[1] + ground * climb_seconds * unit[1])
    recovery = landing(home, settings, weather, geo, heading, altitude, speed)
    climb, start_pose, spiral = None, None, False
    if recovery is not None and _free(geo, home, launch):
        if _free(geo, launch, cruise):
            climb, start_pose = [(launch, 0.), (cruise, climb_seconds)], (*cruise, heading)
        else:
            wind = dubins.wind_vector(weather)
            for direction in (1, -1):
                circled = dubins.loiter((*launch, heading), f.turn_radius_m, speed, wind, direction, climb_seconds)
                if circled is None:
                    continue
                points, track = circled
                if _covered(geo, 'turn', [(x, y) for x, y, _ in points]):
                    points[0] = (*launch, 0.)
                    climb = [((x, y), t) for x, y, t in points]
                    start_pose, spiral = (points[-1][0], points[-1][1], track), True
                    break
    if climb is None:
        reject('FIXED_WING_SITE', 'Не хватает свободного пространства для запуска, набора высоты (прямо или по спирали) или парашютной посадки самолета. '
               'Измените площадку/курс запуска и разрешенную зону.')
    launch_seconds = LAUNCH_RUN_M / ground
    return dict(start_pose=start_pose, launch=launch, climb=climb, climb_seconds=climb_seconds, spiral_climb=spiral,
                launch_seconds=launch_seconds, heading=heading, **recovery,
                overhead=launch_seconds + climb_seconds + recovery['descent_seconds'] + recovery['fall_seconds'] + 20,
                parameters=f.model_dump(), launch_heading_deg=heading_deg)
