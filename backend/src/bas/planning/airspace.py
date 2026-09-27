"""Which restriction zones and obstacles constrain this flight, with the reason for every decision.

The aircraft occupies the band from the ground to its flight level; a zone matters when its
vertical band reaches the highest level plus the vertical buffer, an obstacle when its top plus the
buffer reaches the lowest level. With echelons every aircraft avoids what any level must avoid. Without a DEM, AMSL and flight-level
values are converted with the terrain range of the area, each on its unsafe side: a zone floor
over the highest ground, a zone ceiling and an AMSL obstacle top over the lowest. Zones entirely
below the flight level are still avoided: take-off, climb and landing pass through them.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from shapely.geometry import shape

from bas.planning.sun import sun_times, sunset_after

DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
# Without a deadline, a temporary zone counts if it is active within a day of the start.
DEFAULT_HORIZON = timedelta(hours=24)


@dataclass
class Blocker:
    geometry: object
    clearance_m: float
    kind: str
    ref: str
    label: str


def active_intervals(validity, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    if validity.kind in ('permanent', 'unknown'):
        return [(start, end)]
    tz = ZoneInfo(validity.timezone)
    periods = [(p.start, p.end) for p in validity.periods] or [(start, end)]
    windows = []
    for period_start, period_end in periods:
        lo, hi = max(period_start, start), min(period_end, end)
        if lo >= hi:
            continue
        if not validity.weekly:
            windows.append((lo, hi))
            continue
        day, last = lo.astimezone(tz).date() - timedelta(days=1), hi.astimezone(tz).date()
        while day <= last:
            for weekly in validity.weekly:
                if DAYS[day.weekday()] not in weekly.days:
                    continue
                opens = datetime.combine(day, weekly.start, tz)
                # An end at or before the start means the window runs past midnight.
                closes = datetime.combine(day + timedelta(days=weekly.end <= weekly.start), weekly.end, tz)
                a, b = max(opens, lo), min(closes, hi)
                if a < b:
                    windows.append((a, b))
            day += timedelta(days=1)
    return sorted(windows)


def deadline(scenario):
    """Time limit in seconds from the operation start: the manual limit, sunset, or the earlier of both."""
    s = scenario.settings
    result = dict(limit_s=s.time_limit_min * 60 if s.time_limit_min else None, source='manual' if s.time_limit_min else None,
                  sunrise=None, sunset=None, starts_in_dark=False)
    if s.until_sunset and s.operation_start:
        lon, lat = shape(scenario.survey_area.model_dump()).centroid.coords[0]
        sunset = sunset_after(lat, lon, s.operation_start)
        today = sun_times(lat, lon, (s.operation_start.astimezone(timezone.utc) + timedelta(hours=lon / 15)).date())
        result['starts_in_dark'] = today['polar'] == 'night' or (today['sunrise'] is not None and not today['sunrise'] <= s.operation_start <= today['sunset'])
        result['sunrise'] = today['sunrise'].isoformat() if today['sunrise'] else None
        result['sunset'] = sunset.isoformat() if sunset else None
        if sunset:
            until = (sunset - s.operation_start).total_seconds()
            if result['limit_s'] is None or until < result['limit_s']:
                result.update(limit_s=until, source='sunset')
    return result


def operation_window(scenario):
    start = scenario.settings.operation_start
    if start is None:
        return None, None
    limit = deadline(scenario)['limit_s']
    return start, start + (timedelta(seconds=limit) if limit else DEFAULT_HORIZON)


def airspace(scenario, low=None, high=None):
    """Blockers for flight levels from `low` to `high` (both default to the survey altitude)."""
    s = scenario.settings
    low = s.altitude_m if low is None else low
    high = s.altitude_m if high is None else high
    top = high + s.vertical_buffer_m
    blockers = [Blocker(shape(p.model_dump()), s.boundary_clearance_m, 'no_fly', f'no-fly-{i + 1}', f'Запретная зона {i + 1}')
                for i, p in enumerate(scenario.no_fly_zones)]
    start, end = operation_window(scenario)
    report = dict(terrain_min_m=s.terrain_min_m, terrain_max_m=s.terrain_max_m, vertical_buffer_m=s.vertical_buffer_m, flight_altitude_m=low, flight_altitude_max_m=high,
                  window_start=start.isoformat() if start else None, window_end=end.isoformat() if end else None,
                  zones_applied=[], zones_above=[], zones_below_ground=[], zones_inactive=[], zones_time_assumed=[],
                  obstacles_applied=[], obstacles_below=0, tallest_below_agl_m=None)
    for zone in scenario.restriction_zones:
        lower = zone.lower.above_ground_m(s.terrain_max_m)
        upper = zone.upper.above_ground_m(s.terrain_min_m)
        entry = dict(id=zone.id, name=zone.name, category=zone.category,
                     lower_agl_m=None if math.isinf(lower) else round(lower, 1), upper_agl_m=None if math.isinf(upper) else round(upper, 1))
        if lower >= top:
            report['zones_above'].append(entry)
            continue
        if upper <= 0:
            report['zones_below_ground'].append(entry)
            continue
        if zone.validity.kind == 'scheduled':
            if start is None:
                report['zones_time_assumed'].append(dict(entry, reason='start_unknown'))
            else:
                windows = active_intervals(zone.validity, start, end)
                if not windows:
                    report['zones_inactive'].append(entry)
                    continue
                entry['active'] = [[a.isoformat(), b.isoformat()] for a, b in windows]
        elif zone.validity.kind == 'unknown':
            report['zones_time_assumed'].append(dict(entry, reason='time_unknown'))
        report['zones_applied'].append(entry)
        blockers.append(Blocker(shape(zone.geometry.model_dump()), s.boundary_clearance_m, 'zone', zone.id, zone.name or zone.id))
    for obstacle in scenario.obstacles:
        height = obstacle.height_m - (s.terrain_min_m if obstacle.height_reference == 'AMSL' else 0)
        if height + s.vertical_buffer_m <= low:
            report['obstacles_below'] += 1
            report['tallest_below_agl_m'] = max(report['tallest_below_agl_m'] or 0, round(height, 1))
            continue
        report['obstacles_applied'].append(dict(id=obstacle.id, kind=obstacle.kind, height_agl_m=round(height, 1)))
        blockers.append(Blocker(shape(obstacle.geometry.model_dump()), s.obstacle_clearance_m + obstacle.radius_m, 'obstacle', obstacle.id,
                                f'{obstacle.id} ({obstacle.kind + ", " if obstacle.kind else ""}{height:.0f} м над землей)'))
    return blockers, report
