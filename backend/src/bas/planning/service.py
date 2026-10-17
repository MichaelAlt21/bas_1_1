"""Mission planning for a mixed UAV fleet: a bounded, deterministic heuristic.

plan() checks the input, then _attempt() builds free space, strips, the fleet, allocates strips
to sorties (multirotors append, planes insert and reorder) and schedules them for every strip
direction and allocation policy, keeping the best by the chosen objective. With echelons the
first plan's routes are flown again at assigned levels (_relevel). _post_processing() adds the
reserve-site check, coverage per area, warnings and metrics. See docs/ALGORITHM.md.
"""
import colorsys
import math
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from bas.domain.models import Scenario
from bas.catalog.service import resolve_profiles
from bas.catalog.cameras import camera_geometry, effective_swath
from bas.planning.flight_models.dubins import crab_angle, wind_vector
from bas.planning.flight_models.fixed_wing import operations, connect, bearing, landing, proxy_seconds, bank_deg
from bas.planning.airspace import airspace, deadline
from bas.planning.geometry import LocalGeometry, PlanningError, reject
from bas.planning.coverage.generator import MAX_STRIPS, generate, split_long
from bas.planning.flight_models.multirotor import duration
from bas.planning.scheduling.conflicts import schedule, conflict
from bas.planning.validation.alternates import reachability
from bas.planning.echelons import assign_echelons, TOLERANCE
from bas.planning.cost import breakdown, estimator, tradeoff
from bas.catalog import costs as cost_preset
from bas.planning.tasks import build_units, generic_sensor, sensor_catalog, survey_tasks, task_label
from bas.catalog.sensors import survey_geometry
from bas.planning.diagnostics import equipment as diagnose_equipment, preflight, warnings_of

MAX_FLEET = 20
# Strips that fit no sortie are halved down to this length before the plan gives up.
MIN_SPLIT_M = 100
SEPARATION_M = 30.
# Plane strip insertions built exactly (wind, free space) out of the best by the quick estimate.
INSERTIONS_BUILT = 3
# Local search over a plane sortie's strip order: improving moves applied, and the longest
# sequence for which reversals of every run of strips are also tried.
IMPROVEMENT_ROUNDS = 200
MAX_REVERSAL_STRIPS = 60
# Local search on the chosen objective (all three use the same moves and budget): tried moves, exact realizations (schedule included),
# exact realizations per round, copter insertion positions built per move, and the most strips for
# which swaps are tried as well as moves.
COST_SEARCH_TRIALS = 4000
COST_SEARCH_EXACT = 30
COST_SEARCH_PER_ROUND = 3
COST_SEARCH_POSITIONS = 3
COST_SWAP_STRIPS = 40
# Exact realizations per round of moves that empty an aircraft (not filtered by the estimate).
COST_SEARCH_DISSOLVES = 2
RECOVERY_KEYS = ('end_pose', 'release', 'fall_seconds', 'release_altitude_m', 'descent', 'descent_turns', 'descent_seconds')
TEAL_HUE = colorsys.rgb_to_hls(0x2d / 255, 0xd4 / 255, 0xbf / 255)[0]
GOLDEN_ANGLE = 0.618033988749895
ORANGE_HUES = (0.02, 0.13)


# Running totals must equal sum() over the whole route bit for bit, or near-ties in the
# greedy allocation flip and the same scenario yields a different plan. Python 3.12+ sum()
# of floats is Neumaier-compensated; older versions add plainly.
COMPENSATED_SUM = sum([1.0, 1e100, 1.0, -1e100]) == 2.0
EMPTY_SUM = (0.0, 0.0, 0)


def accumulate(state, values):
    total, compensation, count = state
    for x in values:
        if count == 0:
            total = x
        elif COMPENSATED_SUM:
            t = total + x
            compensation += (total - t) + x if abs(total) >= abs(x) else (x - t) + total
            total = t
        else:
            total += x
        count += 1
    return total, compensation, count


def summed(state):
    total, compensation, count = state
    if count == 0: return 0
    return total + compensation if compensation and math.isfinite(compensation) else total


def footprint_of(missions, requirement=None):
    # A survey leg may carry its own swath (several requirements in one plan); `requirement`
    # keeps only that requirement's legs.
    return unary_union([LineString(l['xy']).buffer(l.get('swath_width_m', m['survey']['swath_width_m'])/2,
                        cap_style=2 if l.get('footprint_mode', m['survey']['mode'])=='camera' else 3)
                        for m in missions for l in m['legs'] if l['phase']=='survey' and (requirement is None or l.get('requirement') == requirement)])


def mission_color(index):
    # Golden-angle hue steps never repeat or cluster for any fleet size, anchored at teal.
    # Oranges are skipped: survey areas are drawn in the customer's brand orange.
    hue, step, found = TEAL_HUE, 0, -1
    while found < index:
        hue = (TEAL_HUE + step * GOLDEN_ANGLE) % 1
        step += 1
        if not ORANGE_HUES[0] <= hue <= ORANGE_HUES[1]:
            found += 1
    # Mid lightness keeps routes readable on the light basemap and on satellite imagery.
    r, g, b = colorsys.hls_to_rgb(hue, 0.44, 0.72)
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


def plan(scenario: Scenario, search: bool = True):
    started = time.monotonic()
    settings = scenario.settings
    legacy = scenario.requirements is None
    profiles = {p.id: p for p in resolve_profiles(scenario, ignore_missing=True).values()}
    found = preflight(scenario, profiles)
    if any(d['severity'] == 'error' for d in found):
        first = next(d for d in found if d['severity'] == 'error')
        raise PlanningError(first['code'], first['message'], diagnostics=found)
    if legacy and settings.coverage_mode == 'camera' and scenario.survey_type not in ('rgb', 'multispectral'):
        reject('SURVEY_UNSUPPORTED', f'Для {scenario.survey_type} пока нет подтвержденного профиля камеры. Используйте ручную ширину захвата.')
    if not scenario.fleet:
        reject('FLEET_REQUIRED', 'Добавьте хотя бы один аппарат в парк.')
    if len(scenario.fleet) > MAX_FLEET:
        reject('FLEET_TOO_LARGE', f'В демонстрационном расчете поддерживается до {MAX_FLEET} аппаратов.')
    profiles = resolve_profiles(scenario)
    if legacy and any(a.payload != scenario.survey_type for a in scenario.fleet):
        reject('PAYLOAD_MISMATCH', f'Для операции с типом съемки {scenario.survey_type} все выбранные аппараты должны иметь эту же нагрузку.')
    observation = scenario.weather.observation
    if observation:
        age = (datetime.now(timezone.utc)-observation.valid_at).total_seconds()
        if not -900 <= age <= 3600:
            reject('WEATHER_STALE', 'Погодному снимку больше часа или его время некорректно. Обновите данные либо задайте ветер вручную.')
    wind_limit = min(p.max_wind_mps for p in profiles.values())
    if max(scenario.weather.wind_speed_mps, observation.wind_gusts_mps if observation else 0) > wind_limit:
        reject('WIND_LIMIT', f'Ветер или порывы превышают предел выбранного парка: {wind_limit:g} м/с.')
    sites = {s.id:s for s in scenario.sites if s.kind == 'launch_landing'}
    if not sites:
        reject('SITE_REQUIRED', 'Добавьте основную площадку старта и посадки.')
    # Per-aircraft flight level: set by hand, else the survey altitude (a first guess for echelons).
    requested = {a.id: a.altitude_m for a in scenario.fleet}
    altitudes = {a.id: settings.altitude_m if requested[a.id] is None else requested[a.id] for a in scenario.fleet}
    # Separation by height applies once levels may differ; a single level keeps the historical rule.
    vertical = settings.vertical_separation_m if settings.echelons or len(set(altitudes.values())) > 1 else None
    run = _attempt(scenario, profiles, sites, altitudes, vertical, search=search)
    echelons = None
    if settings.echelons:
        assigned, echelons = assign_echelons(scenario, profiles, run, altitudes, requested, SEPARATION_M)
        echelons.update(applied=False, single_level=dict(completion_s=run['completion_s'], total_airtime_s=run['airtime_s']))
        if assigned != altitudes:
            second = _relevel(scenario, profiles, run, assigned, vertical)
            safer = echelons['shared_same_level_after_m'] < echelons['shared_same_level_before_m'] - 1
            if second is None:
                echelons['note'] = 'Подъем на эшелон не помещается в заряд или частоту съемки: борта оставлены на прежних высотах.'
            elif safer and second['value'] <= run['value']*(1+TOLERANCE) + 1e-6:
                run = second
                echelons['applied'] = True
            else:
                echelons['note'] = ('Эшелоны ухудшили бы ' + {'min_completion_time': 'срок', 'min_total_airtime': 'налет'}.get(scenario.objective, 'стоимость J')
                                    + f' более чем на {TOLERANCE*100:g}%: борта оставлены на прежних высотах.')
        elif len(echelons['levels']) == 1:
            echelons['note'] = ('Выше высоты съемки нет свободного эшелона (потолок, нижняя граница зон с запасом или предел GSD): '
                                'все борта на одной высоте, по высоте разводятся только набор и снижение.')
        else:
            echelons['note'] = 'Маршруты бортов не сближаются настолько, чтобы разводить их по высоте: высоты не изменены.'
    missions, coverage, angle, geo, fleet = run['missions'], run['coverage'], run['angle'], run['geo'], run['fleet']
    fleet_by_id = {v['aircraft'].id: v for v in fleet}
    charging, airspace_report = run['charging'], run['airspace_report']
    return _post_processing(scenario, started, settings, profiles, sites, observation, missions, coverage, angle, geo, fleet, fleet_by_id,
                            charging, airspace_report, vertical, echelons, run)


def _attempt(scenario, profiles, sites, altitudes, vertical, forced=None, search=True):
    """Plan with fixed flight levels per aircraft: the best of the strip directions and policies.

    `forced` (tests and the brute-force reference only): {'angle', 'assignment'} fixes the strip
    direction and the aircraft of every strip in generation order, one sortie each.
    """
    settings = scenario.settings
    observation = scenario.weather.observation
    tasks = survey_tasks(scenario)
    legacy = scenario.requirements is None
    band = list(altitudes.values()) + [t.altitude_m for t in tasks if t.altitude_m is not None]
    blockers, airspace_report = airspace(scenario, min(band), max(band))
    geo = LocalGeometry(scenario, blockers)
    if observation:
        location = Point(geo.forward(*observation.coordinates))
        if location.distance(geo.area.centroid)>25000:
            reject('WEATHER_LOCATION', 'Погодный снимок относится к другому участку. Обновите погоду.')
    # Charging between sorties: the aircraft waits for its own battery on a site charger, and may
    # land on any site that has one, reserve sites included, to take off from there again.
    charging = settings.recharge_mode == 'charge' and settings.sorties_per_aircraft > 1
    chargers = {s.id: s.charger_count() for s in scenario.sites}
    fleet = []
    for aircraft in scenario.fleet:
        profile = profiles[aircraft.profile_id]
        plane = profile.airframe == 'fixed_wing'
        speed = aircraft.airspeed_mps or (settings.fixed_wing.airspeed_mps if plane else settings.airspeed_mps)
        if speed > profile.max_horizontal_speed_mps or (plane and speed < 64/3.6):
            reject('SPEED_LIMIT', f'{aircraft.id}: скорость вне допустимого диапазона профиля; для демонстрационного самолета минимум 64 км/ч.',
                   aircraft=[aircraft.id], actual=f'{speed:g} м/с', required=f'до {profile.max_horizontal_speed_mps:g} м/с' + (', от 17,8 м/с' if plane else ''))
        altitude = altitudes[aircraft.id]
        if plane and altitude < 100:
            reject('FIXED_WING_ALTITUDE', f'{aircraft.id}: для демонстрационной модели 201 задайте высоту не менее 100 м.', aircraft=[aircraft.id], actual=f'{altitude:g} м', required='≥ 100 м')
        if scenario.weather.wind_speed_mps >= speed:
            reject('WIND_UNREACHABLE', f'Воздушная скорость {aircraft.id} должна превышать постоянный ветер.', aircraft=[aircraft.id],
                   actual=f'{scenario.weather.wind_speed_mps:g} м/с', required=f'< {speed:g} м/с')
        camera = camera_geometry(aircraft, profile, settings, altitude) if legacy else None
        if aircraft.start_site_id is None or aircraft.start_site_id not in sites:
            reject('SITE_ASSIGNMENT', f'Назначьте основную площадку аппарату {aircraft.id}.', aircraft=[aircraft.id])
        site = sites[aircraft.start_site_id]
        home = tuple(geo.forward(*site.coordinates))
        if not geo.free.covers(Point(home)):
            near = [b.label for b, local in geo.blockers if local.distance(Point(home)) < 1][:3]
            reject('SITE_BLOCKED', f'Площадка «{site.name}» вне доступного пространства с учетом отступа' + (f' (мешает: {", ".join(near)})' if near else '') + '.',
                   sites=[site.id], aircraft=[aircraft.id], blockers=[b.ref for b, local in geo.blockers if local.distance(Point(home)) < 1][:3])
        # Time-budget proxy, not a battery energy prediction.
        budget = profiles[aircraft.profile_id].max_flight_time_min*60*settings.endurance_factor*(aircraft.battery_percent-settings.reserve_percent)/100
        if budget <= 0:
            reject('BATTERY_RESERVE', f'Заряд {aircraft.id} не превышает резерв.', aircraft=[aircraft.id], actual=f'{aircraft.battery_percent:g} %',
                   required=f'> {settings.reserve_percent:g} %')
        ops = operations(home, settings, scenario.weather, speed, geo, altitude) if plane else None
        # The launch site is always the first option, so ties keep the aircraft at its own base.
        # Swapping batteries keeps repeat sorties at the base; charging may move them to any site with chargers.
        candidates = [(site, home)]
        max_sorties = settings.sorties_per_aircraft
        if charging and not plane:
            candidates = [(site, home)] if chargers[site.id] else []
            candidates += [(other, tuple(geo.forward(*other.coordinates))) for other in scenario.sites if other.id != site.id and chargers[other.id]]
            if not candidates:
                candidates, max_sorties = [(site, home)], 1
        elif charging:
            # The demonstration aircraft model launches only from its own site.
            max_sorties = max_sorties if chargers[site.id] else 1
        elif settings.landing_policy == 'best_main_site' and settings.sorties_per_aircraft == 1:
            candidates += [(other, tuple(geo.forward(*other.coordinates))) for other in sites.values() if other.id != site.id]
        landing_sites = [(candidate, xy) for candidate, xy in candidates if candidate is site or geo.free.covers(Point(xy))]
        landings = []
        for candidate, xy in landing_sites:
            if plane:
                recovery = ops if candidate is site else landing(xy, settings, scenario.weather, geo, ops['heading'], altitude, speed)
                if recovery: landings.append(dict(site=candidate, xy=xy, **{key: recovery[key] for key in RECOVERY_KEYS}))
            else:
                landings.append(dict(site=candidate, xy=xy))
        # Repeat sorties fly on a fresh battery after the turnaround on the ground.
        fresh_budget = profile.max_flight_time_min*60*settings.endurance_factor*(100-settings.reserve_percent)/100
        if not landings:
            reject('SITE_CHARGING', f'{aircraft.id}: ни одна площадка с зарядными местами не подходит для посадки. Добавьте зарядные места или отключите зарядку.',
                   aircraft=[aircraft.id], sites=[s.id for s, _ in candidates])
        fleet.append(dict(aircraft=aircraft, home=home, site=site, budget=budget, fresh_budget=fresh_budget, plane=plane, speed=speed,
                          camera=camera, ops=ops, landings=landings, overhead=ops['overhead'] if ops else altitude+40, altitude=altitude,
                          max_sorties=max_sorties, battery=aircraft.battery_percent,
                          full_s=profile.max_flight_time_min*60*settings.endurance_factor,
                          charge_s=(profile.charge_time_min or settings.charge_time_min)*60, landing_sites=landing_sites))
    turnaround = settings.turnaround_min*60
    handling = settings.ground_handling_min*60
    # A unit is what a sortie is flown with: aircraft + sensor + survey height + speed. Version 1.1
    # scenarios have one unit per aircraft (its camera), so the historical computation is unchanged.
    if legacy:
        for n, v in enumerate(fleet):
            v.update(i=n, sensor_id=v['aircraft'].camera_id or 'manual', sensor_name=(v['camera'].get('camera') or {}).get('name', 'Ручная ширина'),
                     tasks={0: v['camera']}, fixed_altitude=False)
        units, units_of, eligible = fleet, [[n] for n in range(len(fleet))], [set(range(len(fleet)))]
    else:
        units, units_of, eligible, problems = build_units(scenario, profiles, tasks, fleet, geo)
        missing = [r for r in range(len(tasks)) if not eligible[r]]
        if missing:
            raise PlanningError('EQUIPMENT_MISSING', 'Нет борта и сенсора, способных выполнить: ' + '; '.join(task_label(tasks[r]) for r in missing) + '.',
                                diagnostics=[diagnose_equipment(scenario, profiles, tasks[r], problems[r]) for r in missing])
    sensor_of = [u['sensor_id'] for u in units]

    def change_after(previous_unit, unit):
        """Ground time to change the payload when the next sortie flies another sensor."""
        return settings.payload_change_min*60 if previous_unit is not None and sensor_of[previous_unit] != sensor_of[unit] else 0.

    def charge_after(i, sortie, flown):
        """Charging time to refill the battery after a sortie of `flown` seconds (linear charge model)."""
        vehicle = fleet[i]
        start = vehicle['battery'] if sortie == 1 else 100
        return vehicle['charge_s']*((100-start)/100 + flown/vehicle['full_s'])

    def ground_after(i, sortie, flown):
        return handling + charge_after(i, sortie, flown) if charging else turnaround
    max_strip_m = 0.4*max(max(v['budget'], v['fresh_budget'] if settings.sorties_per_aircraft > 1 else 0)*v['speed'] for v in units)
    connection_cache = {}

    def connector(vehicle, start, finish, lead=0.):
        # (legs, seconds) per connection; the seconds sum the same chords the mission is timed by.
        key = (vehicle['speed'], start, finish, lead)
        if key not in connection_cache:
            legs = connect(start, finish, settings.fixed_wing.turn_radius_m, geo, vehicle['speed'], scenario.weather, lead)
            connection_cache[key] = None if legs is None else (legs, sum(segments(vehicle, legs)))
        return connection_cache[key]

    wind = wind_vector(scenario.weather)
    proxy_cache = {}

    def proxy(vehicle, start, finish, lead=0.):
        key = (vehicle['speed'], start, finish, lead)
        if key not in proxy_cache:
            proxy_cache[key] = proxy_seconds(start, finish, settings.fixed_wing.turn_radius_m, vehicle['speed'], wind, lead)
        return proxy_cache[key]

    def segments(vehicle, legs):
        return [duration(a,b,vehicle['speed'],scenario.weather.wind_speed_mps,scenario.weather.wind_from_deg)
                for leg in legs for a,b in zip(leg['xy'],leg['xy'][1:])]

    return_cache = {}

    def best_return(index, end, heading=None):
        # Cheapest way home from a strip end over the allowed landing sites of unit `index`.
        key = (index, end, heading)
        if key not in return_cache:
            vehicle, best = units[index], None
            for option in vehicle['landings']:
                if vehicle['plane']:
                    found = connector(vehicle, (*end, heading), option['end_pose'])
                    legs = None if found is None else found[0]
                else:
                    path = geo.path(end, option['xy'])
                    legs = [dict(phase='transit', xy=path)] if path else None
                if legs is None: continue
                durations = segments(vehicle, legs)
                cost = sum(durations) + (0 if vehicle['plane'] else 5*len(legs))
                if math.isfinite(cost) and (best is None or cost < best[0]):
                    best = (cost, legs, option, durations)
            return_cache[key] = best
        return return_cache[key]

    cost_model = cost_preset.effective(scenario, profiles)
    aircraft_ids = [v['aircraft'].id for v in fleet]
    cost_estimate = estimator(cost_model, aircraft_ids)

    goal = scenario.objective

    def objective_value(completion, total, scheduled):
        if goal == 'min_completion_time': return completion
        if goal == 'min_total_airtime': return total
        airtime = {}
        for m in scheduled:
            airtime[m['aircraft_id']] = airtime.get(m['aircraft_id'], 0.) + m['flight_time_s']
        return cost_estimate(completion, [airtime.get(a, 0.) for a in aircraft_ids])

    candidates, reasons = [], []

    def swaths_at(angle):
        """{task: (swath, crab)} per unit along strips at `angle`: a plane's frames turn with its crab angle."""
        result = []
        for v in units:
            if not v['plane']:
                result.append({r: (geometry['swath_width_m'], None) for r, geometry in v['tasks'].items()})
                continue
            crab = crab_angle(math.radians(angle), wind, v['speed'])
            if crab is None:
                raise PlanningError('WIND_TRACK', f'{v["aircraft"].id}: боковой ветер не позволяет держать галсы под {angle:g}°.')
            result.append({r: (effective_swath(geometry, crab, 'lidar' if geometry['mode'] == 'scanner' else tasks[r].survey_type), crab)
                           for r, geometry in v['tasks'].items()})
        return result
    directions = [0, 90]
    if any(v['plane'] for v in units) and scenario.weather.wind_speed_mps > 0:
        along = round((90 - scenario.weather.wind_from_deg) % 180, 1)
        for extra in (along, round((along + 90) % 180, 1)):
            if all(min(abs(extra - d), 180 - abs(extra - d)) > .5 for d in directions):
                directions.append(extra)
    # All allocation policies are judged by the final scheduled objective, so a nominally
    # balanced assignment cannot win after costly deconfliction.
    # The same policies for every objective: the three criteria choose from one set of plans. Without the
    # search (fleet analysis: many plans for a table) the allocation of version 1.2.0 is kept.
    allocation = ('min_completion_time', 'min_total_airtime', 'min_cost') if search else         ('min_completion_time', 'min_total_airtime') + (('min_cost',) if scenario.objective == 'min_cost' else ())
    policies = [(a, p) for p in allocation for a in directions]
    if forced is not None:
        directions, policies = [forced['angle']], [(forced['angle'], 'min_completion_time')]
    numbers = {area_id: n for n, area_id in enumerate(scenario.area_id_list(), 1)}
    # Every task over all areas uses the planner's union of areas, as version 1.1 did.
    task_zones = []
    for task in tasks:
        wanted = {numbers[a] for a in task.area_ids}
        zones = [zone for number, zone in zip(geo.area_numbers, geo.areas) if number in wanted]
        task_zones.append((zones, geo.area if len(wanted) == len(numbers) else (unary_union(zones) if zones else None)))
    state = {}
    for angle, policy in policies:
        try:
            swaths = swaths_at(angle)
            strips, widths, coverage_info = [], {}, {}
            for r, task in enumerate(tasks):
                zones, area = task_zones[r]
                if area is None:
                    coverage_info[r] = None
                    continue
                width = min(swaths[u][r][0] for u in sorted(eligible[r]))
                if width <= 0:
                    raise PlanningError('CRAB', 'Снос самолета в боковом ветре так велик, что соседние кадры не перекрываются. Увеличьте продольное перекрытие или направьте галсы по ветру.')
                lines_only = all(units[u]['tasks'][r]['mode'] == 'line' for u in eligible[r])
                overlap = 0 if lines_only else task.side_overlap_percent
                # A strip longer than 40 % of the best single-sortie range is cut, leaving the rest for transit.
                task_strips = split_long(generate(area, width, overlap, angle, geo.reachable if geo.excluding else None), max_strip_m)
                if not task_strips or any(not geo.free.covers(line) for line in task_strips):
                    raise PlanningError('STRIP_BLOCKED', 'Съемочный галс пересекает ограничение.')
                camera_caps = all(units[u]['tasks'][r]['mode'] == 'camera' for u in eligible[r])
                # With excluded parts the strips cannot reach everything next to a blocker:
                # completeness is then checked against what the strips can cover at all.
                reach = unary_union([s.buffer(width/2, cap_style=2 if camera_caps else 3) for s in task_strips]) if geo.excluding else None
                targets = zones if reach is None else [zone.intersection(reach) for zone in zones]
                target = area if reach is None else area.intersection(reach)
                widths[r] = width
                coverage_info[r] = (area, target, targets, reach)
                strips += [(line, r) for line in task_strips]
            if forced is not None and forced.get('count_only'):
                return dict(strip_count=len(strips))
            if len(strips) > MAX_STRIPS:
                raise PlanningError('TOO_MANY_STRIPS', f'Более {MAX_STRIPS} галсов по всем требованиям: уменьшите районы или измените параметры съемки.')
            # A slot is one sortie of one aircraft. Each aircraft keeps its current sortie and, while
            # it may fly more, an empty next one; a strip goes to the best of all open slots.
            # Route durations accumulate per strip, so a candidate costs O(1) instead of O(route).
            slots = [dict(i=i, sortie=1, budget=v['budget'], route=[], time=0., sum=EMPTY_SUM, u=units_of[i][0] if len(units_of[i]) == 1 else None)
                     for i, v in enumerate(fleet)]
            own = [[i] for i in range(len(fleet))]
            finish = [0. for _ in fleet]

            def finish_with(i, index, value, unit=None):
                # Aircraft busy time if slot `index` took `value`: its sorties plus ground time between
                # them (the swap turnaround, or handling and charging; charger queues are left to scheduling),
                # plus a payload change between sorties flown with different sensors.
                total, count, ground, previous, change, previous_unit = 0., 0, 0., None, 0., None
                for k in own[i]:
                    if k != index and not slots[k]['route']: continue
                    t = value if k == index else slots[k]['time']
                    current = unit if k == index and unit is not None else slots[k]['u']
                    if charging and previous is not None: ground += ground_after(i, *previous)
                    if previous_unit is not None and current is not None: change += change_after(previous_unit, current)
                    total = t if count == 0 else total + t
                    count += 1; previous = (slots[k]['sortie'], t); previous_unit = current
                busy = total + (ground if charging else turnaround*(count-1))
                return busy + change if change else busy

            def score_of(busy, airtime):
                if policy == 'min_completion_time': return (max(busy), sum(airtime))
                if policy == 'min_total_airtime': return (sum(airtime), max(busy))
                times = [0.]*len(fleet)
                for s, t in zip(slots, airtime): times[s['i']] += t
                return (cost_estimate(max(busy), times), max(busy), sum(airtime))

            def open_of(i):
                # The current sortie and, when one is waiting, the empty next sortie.
                return own[i][-2:] if len(own[i]) > 1 and not slots[own[i][-1]]['route'] else own[i][-1:]

            def start_of(index):
                # A charging aircraft takes off where the previous sortie landed and charged.
                slot = slots[index]; i = slot['i']; vehicle = fleet[i]
                if slot['sortie'] == 1 or not charging or vehicle['plane']:
                    return vehicle['home'], vehicle['site']
                before = slots[own[i][own[i].index(index)-1]]
                option = best_return(before['u'], tuple(before['route'][-1]['xy'][-1]))[2]
                return option['xy'], option['site']

            def insert(index, strip, r, u):
                """Best place for `strip` in plane sortie `index`: (total, sequence, legs) or None.

                A plane gains most from flying strips out of order: the neighbouring strip is often
                closer than its turn diameter and takes a loop to reach. Every position and direction
                is ranked by the quick estimate (wind, no free-space check), pruned by a straight-line
                lower bound; the best few are built exactly and checked against free space and budget.
                """
                slot = slots[index]; vehicle = units[u]
                sequence = slot.get('sequence') or []
                speed, lead = vehicle['speed'], settings.fixed_wing.lead_in_m
                fastest = speed + scenario.weather.wind_speed_mps
                start = vehicle['ops']['start_pose']
                poses = [((*a, bearing(a, b)), (*b, bearing(a, b))) for a, b, _ in sequence]
                current = best_return(u, poses[-1][1][:2], poses[-1][1][2])[0] if poses else 0.
                bounds = []
                for reverse in (False, True):
                    points = [tuple(p) for p in strip.coords]
                    if reverse: points.reverse()
                    camera = vehicle['tasks'][r]
                    if camera.get('trigger_interval_s') is not None:
                        count = max(2, math.ceil(math.dist(*points)/camera['photo_step_m'])+1)
                        interval = duration(*points,speed,scenario.weather.wind_speed_mps,scenario.weather.wind_from_deg)/(count-1)
                        if interval+1e-9 < camera['trigger_interval_s']: continue
                    a, b = points
                    heading = bearing(a, b)
                    survey_s = duration(a, b, speed, scenario.weather.wind_speed_mps, scenario.weather.wind_from_deg)
                    back = best_return(u, b, heading)
                    if back is None or not math.isfinite(survey_s): continue
                    for position in range(len(sequence) + 1):
                        before = start if position == 0 else poses[position-1][1]
                        if position < len(sequence):
                            after = poses[position][0]
                            lower = (math.dist(before[:2], a) + math.dist(b, after[:2]))/fastest + survey_s - proxy(vehicle, before, after, lead)
                        else:
                            lower = math.dist(before[:2], a)/fastest + survey_s + back[0] - current
                        bounds.append((lower, position, reverse, a, b, heading, survey_s, back[0]))
                ranked = []
                for lower, position, reverse, a, b, heading, survey_s, back_s in sorted(bounds, key=lambda item: item[:3]):
                    if len(ranked) >= INSERTIONS_BUILT and lower >= ranked[-1][0]: break
                    before = start if position == 0 else poses[position-1][1]
                    entry = proxy(vehicle, before, (*a, heading), lead)
                    if position < len(sequence):
                        after = poses[position][0]
                        delta = entry + survey_s + proxy(vehicle, (*b, heading), after, lead) - proxy(vehicle, before, after, lead)
                    else:
                        delta = entry + survey_s + back_s - current
                    ranked.append((delta, position, reverse, a, b))
                    ranked.sort(key=lambda item: item[:3])
                    del ranked[INSERTIONS_BUILT:]
                best = None
                for _, position, _, a, b in ranked:
                    candidate = sequence[:position] + [(a, b, r)] + sequence[position:]
                    built = build(u, candidate)
                    if built is not None and built[0] <= slot['budget'] and (best is None or built[0] < best[0]):
                        best = (built[0], candidate, built[1])
                return best

            def build(u, sequence):
                """(flight time, legs without the return) of plane unit `u` flying `sequence`, or None."""
                vehicle = units[u]
                speed, lead = vehicle['speed'], settings.fixed_wing.lead_in_m
                legs, flown, pose = [], 0., vehicle['ops']['start_pose']
                for x, y, r in sequence:
                    heading = bearing(x, y)
                    found = connector(vehicle, pose, (*x, heading), lead)
                    if found is None: return None
                    legs += found[0] + [dict(phase='survey', xy=[x, y], requirement=r)]
                    flown += found[1] + duration(x, y, speed, scenario.weather.wind_speed_mps, scenario.weather.wind_from_deg)
                    pose = (*y, heading)
                back = best_return(u, pose[:2], pose[2])
                if back is None: return None
                total = vehicle['overhead'] + flown + back[0]
                return (total, legs) if math.isfinite(total) else None

            def improve(index):
                """Reorder a finished plane sortie: moves of one strip and reversals of a run of strips.

                Steepest descent over both kinds of move, each costed in constant time from the
                estimates of the links it changes (the wind-aware estimate plus the exact return);
                a better order is kept only when the exact flight time with the wind is shorter.
                """
                slot = slots[index]; u = slot['u']; vehicle = units[u]
                speed, lead = vehicle['speed'], settings.fixed_wing.lead_in_m
                weather = scenario.weather
                start = vehicle['ops']['start_pose']
                surveys = {}

                def flip(strip): return (strip[1], strip[0], strip[2])

                def survey(strip):
                    if strip not in surveys:
                        surveys[strip] = duration(strip[0], strip[1], speed, weather.wind_speed_mps, weather.wind_from_deg)
                    return surveys[strip]

                def entry(strip): return (*strip[0], bearing(strip[0], strip[1]))
                def leave(strip): return (*strip[1], bearing(strip[0], strip[1]))

                def link(pose, following):
                    # Following pose, or None for the flight home.
                    if following is None:
                        back = best_return(u, pose[:2], pose[2])
                        return back[0] if back else math.inf
                    return proxy(vehicle, pose, following, lead)

                def racetrack(strips):
                    """Strips in the classic fixed-wing order: out on one line, back k lines over.

                    Lines are sorted across the track; with k the turn diameter over the spacing plus
                    one, both turns of each lap (k and k - 1 lines over) are wide enough for the plane.
                    """
                    (x0, y0), (x1, y1) = strips[0][:2]
                    length = math.hypot(x1 - x0, y1 - y0)
                    ux, uy = (x1 - x0) / length, (y1 - y0) / length
                    across = sorted(strips, key=lambda x: (round(-(x[0][0] + x[1][0]) * uy / 2 + (x[0][1] + x[1][1]) * ux / 2, 3),
                                                           (x[0][0] + x[1][0]) * ux / 2 + (x[0][1] + x[1][1]) * uy / 2))
                    offsets = [-(a[0] + b[0]) * uy / 2 + (a[1] + b[1]) * ux / 2 for a, b, _ in across]
                    steps = [q - p for p, q in zip(offsets, offsets[1:]) if q - p > 1]
                    if not steps: return None
                    spacing = sorted(steps)[len(steps) // 2]
                    diameter = 2 * settings.fixed_wing.turn_radius_m * ((speed + weather.wind_speed_mps) / speed) ** 2
                    k = max(1, math.ceil(diameter / spacing) + 1)
                    order = []
                    for block in range(0, len(across), 2 * k):
                        for j in range(k):
                            order += [n for n in (block + j, block + j + k) if n < len(across)]
                    result = []
                    for position, n in enumerate(order):
                        a, b, r = across[n]
                        forward = (b[0] - a[0]) * ux + (b[1] - a[1]) * uy > 0
                        result.append((a, b, r) if forward == (position % 2 == 0) else (b, a, r))
                    return result

                def descend(sequence):
                    for _ in range(IMPROVEMENT_ROUNDS):
                        n = len(sequence)
                        entries, leaves = [entry(x) for x in sequence], [leave(x) for x in sequence]
                        before = [start] + leaves[:-1]
                        after = entries[1:] + [None]
                        best, move = -1e-6, None
                        for k in range(n):
                            rest = sequence[:k] + sequence[k+1:]
                            out = link(before[k], entries[k]) + survey(sequence[k]) + link(leaves[k], after[k])
                            saved = out - link(before[k], after[k])
                            rest_leaves = [leave(x) for x in rest]
                            rest_entries = [entry(x) for x in rest]
                            for position in range(n):
                                previous = start if position == 0 else rest_leaves[position-1]
                                following = rest_entries[position] if position < n-1 else None
                                base = link(previous, following)
                                for strip in (sequence[k], flip(sequence[k])):
                                    if position == k and strip == sequence[k]: continue
                                    gain = link(previous, entry(strip)) + survey(strip) + link(leave(strip), following) - base - saved
                                    if gain < best:
                                        best, move = gain, ('move', k, position, strip)
                        if n <= MAX_REVERSAL_STRIPS:
                            for first in range(n - 1):
                                inner, flipped_inner, surveys_delta = 0., 0., survey(flip(sequence[first])) - survey(sequence[first])
                                for last in range(first + 1, n):
                                    inner += link(leaves[last-1], entries[last])
                                    flipped_inner += link(leave(flip(sequence[last])), entry(flip(sequence[last-1])))
                                    surveys_delta += survey(flip(sequence[last])) - survey(sequence[last])
                                    gain = (link(before[first], entry(flip(sequence[last]))) - link(before[first], entries[first])
                                            + flipped_inner - inner + surveys_delta
                                            + link(leave(flip(sequence[first])), after[last]) - link(leaves[last], after[last]))
                                    if gain < best:
                                        best, move = gain, ('reverse', first, last, None)
                        if move is None: break
                        kind, a, b, strip = move
                        if kind == 'move':
                            rest = sequence[:a] + sequence[a+1:]
                            sequence = rest[:b] + [strip] + rest[b:]
                        else:
                            sequence = sequence[:a] + [flip(x) for x in reversed(sequence[a:b+1])] + sequence[b+1:]
                    return sequence

                candidates = [descend(list(slot['sequence']))]
                laps = racetrack(list(slot['sequence']))
                if laps is not None and laps != slot['sequence']:
                    candidates.append(descend(laps))
                best = None
                for sequence in candidates:
                    if sequence == slot['sequence']: continue
                    built = build(u, sequence)
                    if built is not None and built[0] < (slot['time'] if best is None else best[0]) - 1e-6 and built[0] <= slot['budget']:
                        best = (built[0], sequence, built[1])
                if best is not None:
                    slot.update(route=best[2], sequence=best[1], time=best[0])

            def copter_route(u, sequence, start):
                """(flight time, legs without the return, Neumaier state) of multirotor unit `u` flying `sequence`."""
                vehicle, legs, previous = units[u], [], start
                for a, b, r in sequence:
                    path = geo.path(tuple(previous), tuple(a))
                    if path is None: return None
                    legs += [dict(phase='transit', xy=path), dict(phase='survey', xy=[a, b], requirement=r)]
                    previous = b
                if not sequence: return 0., [], EMPTY_SUM
                back = best_return(u, tuple(previous))
                if back is None: return None
                state = accumulate(EMPTY_SUM, segments(vehicle, legs))
                total = vehicle['overhead'] + summed(accumulate(state, back[3])) + 5*(len(legs)+len(back[1]))
                return (total, legs, state) if math.isfinite(total) else None

            def realize(slots):
                """Missions of the allocated slots, scheduled and checked: (value, scheduled, coverage, completion, total, missions, by_task)."""
                missions = []
                position = {}
                unit_of_mission = []
                # First sorties of every aircraft are scheduled before any second sortie, and so on.
                for index in sorted((k for k, s in enumerate(slots) if s['route']), key=lambda k: (slots[k]['sortie'], slots[k]['i'])):
                    slot = slots[index]; i = slot['i']; u = slot['u']; vehicle = units[u]; route = slot['route']
                    position[(i, slot['sortie'])] = len(missions)
                    home, start_site = start_of(index); elapsed = 0.; timed = []
                    def append(phase,points,seconds,h0,h1,requirement=None):
                        nonlocal elapsed
                        if seconds <= 1e-6: return
                        timed.append(dict(phase=phase,xy=points,start=elapsed,end=elapsed+seconds,altitude_start_m=h0,altitude_end_m=h1))
                        if requirement is not None: timed[-1]['requirement'] = requirement
                        elapsed += seconds
                    ops = vehicle['ops']
                    last = route[-1]['xy']
                    level = vehicle['altitude']
                    if ops:
                        append('takeoff',[home,ops['launch']],ops['launch_seconds'],0,0)
                        climb = ops['climb']
                        for (a, ta), (b, tb) in zip(climb, climb[1:]):
                            append('climb',[a,b],tb-ta,level*ta/ops['climb_seconds'],level if b is climb[-1][0] else level*tb/ops['climb_seconds'])
                        _, back, touchdown, _ = best_return(u, tuple(last[-1]), bearing(last[0],last[-1]))
                    else:
                        append('takeoff',[home,home],20,0,0)
                        append('climb',[home,home],level/2,0,level)
                        _, back, touchdown, _ = best_return(u, tuple(last[-1]))
                    route = route+back
                    for leg in route:
                        for a,b in zip(leg['xy'],leg['xy'][1:]):
                            append(leg['phase'],[a,b],duration(a,b,vehicle['speed'],scenario.weather.wind_speed_mps,scenario.weather.wind_from_deg),level,level,leg.get('requirement'))
                        end = leg['xy'][-1]
                        if not ops: append('hold',[end,end],5,level,level)
                    pad = touchdown['xy']
                    if ops:
                        release_level = touchdown['release_altitude_m']
                        if touchdown['descent']:
                            # Circling down over the release point to the release height.
                            circle = touchdown['descent']
                            laps = [(a, b, duration(a,b,vehicle['speed'],scenario.weather.wind_speed_mps,scenario.weather.wind_from_deg)) for a, b in zip(circle, circle[1:])]
                            drop, spent, count = level-release_level, 0., touchdown['descent_turns']*len(laps)
                            for n in range(count):
                                a, b, seconds = laps[n % len(laps)]
                                append('descent',[a,b],seconds,level-drop*spent/touchdown['descent_seconds'],
                                       release_level if n == count-1 else level-drop*(spent+seconds)/touchdown['descent_seconds'])
                                spent += seconds
                        append('parachute',[touchdown['release'],pad],touchdown['fall_seconds'],release_level,0)
                    else:
                        append('descent',[pad,pad],level/2,level,0)
                    append('landing',[pad,pad],20,0,0)
                    if elapsed > slot['budget']+1e-6:
                        raise PlanningError('CAPACITY','Не хватило ресурса после детализации маршрута.', aircraft=[vehicle['aircraft'].id])
                    served = sorted({leg['requirement'] for leg in timed if leg['phase'] == 'survey'})
                    surveys = {}
                    for r in served:
                        survey = dict(vehicle['tasks'][r])
                        if vehicle['plane']:
                            swath, crab = swaths[u][r]
                            survey.update(swath_width_m=swath, nominal_swath_width_m=vehicle['tasks'][r]['swath_width_m'], crab_deg=math.degrees(abs(crab)))
                        if survey['mode'] == 'camera':
                            captures = 0
                            intervals = []
                            for leg in timed:
                                if leg['phase'] != 'survey' or leg['requirement'] != r: continue
                                count = max(2, math.ceil(math.dist(*leg['xy'])/survey['photo_step_m'])+1)
                                interval = (leg['end']-leg['start'])/(count-1)
                                if survey['trigger_interval_s'] is not None and interval+1e-9 < survey['trigger_interval_s']:
                                    raise PlanningError('CAPTURE_RATE', f'{vehicle["aircraft"].id}: требуемый интервал снимков {interval:.2f} с меньше заданного предела камеры. Уменьшите скорость или перекрытие.',
                                                        aircraft=[vehicle['aircraft'].id], actual=f'{interval:.2f} с', required=f'≥ {survey["trigger_interval_s"]:g} с')
                                leg.update(photo_count=count,photo_interval_s=interval,photo_step_m=math.dist(*leg['xy'])/(count-1))
                                captures += count; intervals.append(interval)
                            survey.update(photo_count=captures,min_photo_interval_s=min(intervals))
                        surveys[r] = survey
                    if not legacy:
                        for leg in timed:
                            if leg['phase'] == 'survey':
                                leg.update(swath_width_m=surveys[leg['requirement']]['swath_width_m'], footprint_mode=surveys[leg['requirement']]['mode'])
                    after = position.get((i, slot['sortie']-1))
                    ground = dict(ground_s=ground_after(i, slot['sortie']-1, missions[after]['flight_time_s'])) if after is not None else {}
                    if after is not None and charging:
                        ground.update(charge_site_id=missions[after]['landing_site_id'], charge_s=charge_after(i, slot['sortie']-1, missions[after]['flight_time_s']))
                    change = change_after(unit_of_mission[after], u) if after is not None else 0.
                    if change:
                        ground.update(change_s=change, ground_s=ground['ground_s']+change)
                    start_battery = vehicle['battery'] if slot['sortie'] == 1 else 100
                    extra = {} if legacy else dict(surveys={tasks[r].id: surveys[r] for r in served})
                    missions.append(dict(aircraft_id=vehicle['aircraft'].id,profile_id=vehicle['aircraft'].profile_id,site_id=start_site.id,landing_site_id=touchdown['site'].id,color=mission_color(i),legs=timed,
                        airframe='fixed_wing' if ops else 'multirotor',airspeed_mps=vehicle['speed'],survey=surveys[served[0]],
                        fixed_wing_assumptions=dict(ops['parameters'],launch_heading_deg=ops['launch_heading_deg'],arc_error_m=.2,spiral_climb=ops['spiral_climb'],
                                                    bank_deg=bank_deg(settings.fixed_wing.turn_radius_m, vehicle['speed']),release_altitude_m=touchdown['release_altitude_m'],
                                                    descent_turns=touchdown['descent_turns']) if ops else None,altitude_m=level,
                        flight_time_s=elapsed,budget_s=slot['budget'],sortie=slot['sortie'],after=after,distance_m=sum(math.dist(l['xy'][0],l['xy'][-1]) for l in timed),reserve_percent=settings.reserve_percent,
                        battery_start_percent=start_battery,battery_end_percent=start_battery-100*elapsed/vehicle['full_s'],
                        sensor_id=vehicle['sensor_id'],sensor_name=vehicle['sensor_name'],requirement_ids=[tasks[r].id for r in served],
                        survey_types=sorted({tasks[r].survey_type for r in served}),fixed_altitude=vehicle['fixed_altitude'],**extra,**ground))
                    unit_of_mission.append(u)
                scheduled = schedule(missions, turnaround, chargers if charging else None, handling, vertical)
                all_legs = [l for m in scheduled for l in m['legs']]
                if any(not geo.visible(tuple(l['xy'][0]),tuple(l['xy'][-1])) for l in all_legs):
                    raise PlanningError('VALIDATION','Траектория нарушает границы.')
                by_task = {}
                if legacy:
                    footprint = footprint_of(scheduled)
                    coverage = geo.area.intersection(footprint).area/geo.area.area
                    area, target, targets, reach = coverage_info[0]
                    reached = coverage if reach is None else target.intersection(footprint).area/target.area
                    if reached < .999 or any(zone.area > 0 and zone.intersection(footprint).area/zone.area < .999 for zone in targets):
                        raise PlanningError('COVERAGE','Не обеспечено полное геометрическое покрытие.')
                    by_task[0] = dict(coverage=coverage, reached=reached)
                else:
                    for r, info in coverage_info.items():
                        if info is None:
                            by_task[r] = dict(coverage=0., reached=0., excluded=True)
                            continue
                        area, target, targets, reach = info
                        footprint = footprint_of(scheduled, r)
                        covered = area.intersection(footprint).area/area.area
                        reached = covered if reach is None else target.intersection(footprint).area/target.area
                        if reached < .999 or any(zone.area > 0 and zone.intersection(footprint).area/zone.area < .999 for zone in targets):
                            raise PlanningError('COVERAGE', f'Требование «{task_label(tasks[r])}» покрыто не полностью.', requirements=[tasks[r].id])
                        by_task[r] = dict(coverage=covered, reached=reached)
                    coverage = min((v['coverage'] for v in by_task.values()), default=0.)
                if any(conflict(a['legs'],b['legs'],SEPARATION_M,vertical) for i,a in enumerate(scheduled) for b in scheduled[:i]):
                    raise PlanningError('CONFLICT','Не удалось разделить маршруты по времени.')
                completion = max(m['finish_s'] for m in scheduled)
                total = sum(m['flight_time_s'] for m in scheduled)
                for m, u in zip(missions, unit_of_mission): m['unit'] = u
                return objective_value(completion, total, scheduled), scheduled, coverage, completion, total, missions, by_task

            done = 0
            queue = strips[::-1]
            assigned = forced['assignment'] if forced is not None else None
            while queue:
                strip, r = queue.pop()
                choices = []
                used = sum(1 for f in finish if f)
                for slot_index in [k for i in range(len(fleet)) for k in open_of(i)]:
                    slot = slots[slot_index]; i = slot['i']; route = slot['route']
                    if not finish[i] and settings.max_aircraft is not None and used >= settings.max_aircraft: continue
                    if assigned is not None and (i != assigned[done] or slot['sortie'] > 1): continue
                    for u in ([slot['u']] if route else units_of[i]):
                        if u not in eligible[r]: continue
                        vehicle = units[u]
                        if vehicle['plane']:
                            inserted = insert(slot_index, strip, r, u)
                            if inserted is not None:
                                total, sequence, legs = inserted
                                busy = list(finish); busy[i] = finish_with(i, slot_index, total, u)
                                airtime = [s['time'] for s in slots]; airtime[slot_index] = total
                                choices.append((score_of(busy, airtime),slot_index,(sequence,legs),total,None,busy[i],u))
                            continue
                        previous = route[-1]['xy'][-1] if route else start_of(slot_index)[0]
                        for reverse in (False, True):
                            points = list(strip.coords)
                            if reverse: points.reverse()
                            camera = vehicle['tasks'][r]
                            if camera.get('trigger_interval_s') is not None:
                                count = max(2, math.ceil(math.dist(*points)/camera['photo_step_m'])+1)
                                interval = duration(*points,vehicle['speed'],scenario.weather.wind_speed_mps,scenario.weather.wind_from_deg)/(count-1)
                                if interval+1e-9 < camera['trigger_interval_s']: continue
                            path = geo.path(tuple(previous), tuple(points[0]))
                            approach = [dict(phase='transit',xy=path)] if path else None
                            back = best_return(u, tuple(points[-1]))
                            if approach is None or back is None: continue
                            added = approach+[dict(phase='survey', xy=points, requirement=r)]
                            added_sum = accumulate(slot['sum'], segments(vehicle, added))
                            flown = summed(accumulate(added_sum, back[3])) + (0 if vehicle['plane'] else 5*(len(route)+len(added)+len(back[1])))
                            total = vehicle['overhead']+flown
                            if not math.isfinite(total) or total > slot['budget']: continue
                            busy = list(finish); busy[i] = finish_with(i, slot_index, total, u)
                            airtime = [s['time'] for s in slots]; airtime[slot_index] = total
                            choices.append((score_of(busy, airtime),slot_index,added,total,added_sum,busy[i],u))
                if not choices and strip.length > max(2*widths[r], MIN_SPLIT_M) and assigned is None:
                    # No sortie can take the whole strip: fly it as two halves, first half first.
                    if done+len(queue) >= 2*MAX_STRIPS:
                        raise PlanningError('TOO_MANY_STRIPS', f'Более {2*MAX_STRIPS} частей галсов: уменьшите район, добавьте площадки ближе к нему или вылеты.')
                    (x0,y0),(x1,y1) = strip.coords; middle = ((x0+x1)/2,(y0+y1)/2)
                    queue += [(LineString([middle,(x1,y1)]), r), (LineString([(x0,y0),middle]), r)]
                    continue
                if not choices:
                    limit = f' Число бортов ограничено: {settings.max_aircraft}.' if settings.max_aircraft is not None and settings.max_aircraft < len(fleet) else ''
                    exhausted = all(slots[own[i][-1]]['route'] and slots[own[i][-1]]['sortie'] >= fleet[i]['max_sorties'] for i in range(len(fleet))
                                    if settings.max_aircraft is None or finish[i] or used < settings.max_aircraft)
                    capable = sorted({units[u]['aircraft'].id for u in eligible[r]})
                    if exhausted:
                        raise PlanningError('CAPACITY', f'Все вылеты заняты: {sum(1 for s in slots if s["route"])} вылетов '
                                            f'({len({s["i"] for s in slots if s["route"]})} бортов × до {settings.sorties_per_aircraft}) покрывают {done} частей галсов, '
                                            'остальное не помещается. Увеличьте число вылетов на борт, парк или сократите район; оценка по пакетам — в «Задании целиком».'+limit,
                                            requirements=[tasks[r].id] if not legacy else [], aircraft=capable, kind='sorties', limit=bool(limit))
                    paths = [geo.path(v['home'], tuple(strip.coords[0])) for v in fleet]
                    nearest = min((sum(math.dist(a, b) for a, b in zip(p, p[1:])) for p in paths if p), default=math.inf)
                    where = f'в {nearest/1000:.1f} км свободного пути от ближайшей площадки' if math.isfinite(nearest) else 'без свободного пути от площадок'
                    raise PlanningError('CAPACITY',f'Эвристика не нашла распределение в пределах заряда, траекторий и частоты съемки: распределено {done} частей галсов, '
                                        f'следующая ({where}) не помещается ни в один вылет. '
                                        'Проверьте расположение площадок, число вылетов на борт, пространство разворотов, скорость и интервал снимков.'+limit,
                                        requirements=[tasks[r].id] if not legacy else [], aircraft=capable, kind='range',
                                        actual=f'{nearest/1000:.1f} км свободного пути до ближайшей площадки' if math.isfinite(nearest) else None, limit=bool(limit))
                _, index, added, total, added_sum, busy_time, u = min(choices,key=lambda c:(c[0],c[1]))
                slot = slots[index]; i = slot['i']
                if not slot['route'] and slot['sortie'] < fleet[i]['max_sorties']:
                    own[i].append(len(slots))
                    slots.append(dict(i=i, sortie=slot['sortie']+1, budget=fleet[i]['fresh_budget'], route=[], time=0., sum=EMPTY_SUM,
                                      u=units_of[i][0] if len(units_of[i]) == 1 else None))
                if units[u]['plane']:
                    sequence, legs = added
                    slot.update(route=legs, sequence=sequence, time=total, u=u)
                else:
                    survey = added[-1]['xy']
                    slot.update(route=slot['route']+added, time=total, sum=added_sum, u=u,
                                sequence=slot.get('sequence', [])+[(tuple(survey[0]), tuple(survey[-1]), r)])
                finish[i] = busy_time
                done += 1
            for index, slot in enumerate(slots):
                if slot['route'] and units[slot['u']]['plane'] and len(slot.get('sequence', ())) >= 2:
                    improve(index)
            value, scheduled, coverage, completion, total, missions, by_task = realize(slots)
            candidates.append((value,scheduled,coverage,angle,completion,total,missions,by_task))
            state[len(candidates)-1] = dict(slots=slots, own=own, finish=finish, swaths=swaths, policy=policy, realize=realize,
                                           copter_route=copter_route, build=build, finish_with=finish_with, insert=insert,
                                           start_of=start_of, coverage_info=coverage_info, widths=widths)
        except PlanningError as error:
            reasons.append(error)
    if not candidates:
        last = reasons[-1] if reasons else None
        reject('NO_PLAN_FOUND',f'Не найден допустимый демонстрационный план для {len(directions)} направлений галсов. '+(str(last) if last else '')+' Это не доказательство невозможности: попробуйте меньший участок, дополнительные аппараты или другие площадки.',
               cause=last)
    def local_search(start, st):
        """Local search on the chosen objective: move a strip to another aircraft, swap two, or empty a last sortie.

        Every objective gets the same moves and budget, so plans of different criteria are compared
        on equal effort. Keys are lexicographic: completion T, then total airtime L; L, then T;
        J, then Σ t_i². Candidates are ranked by an estimate of the allocation (busy time of each
        aircraft, sortie flight times); the best few are realized exactly — missions, schedule with
        deconfliction, coverage and budget checks — and a move is kept only when the exact key of
        the scheduled plan goes down. Deterministic order; the budget is a number of tried moves
        and exact realizations, not wall time.
        """
        realize, copter_route, build, finish_with, insert, start_of = (st[k] for k in ('realize', 'copter_route', 'build', 'finish_with', 'insert', 'start_of'))
        current = list(start)
        initial = current[0]
        current_key = None
        trials = evaluations = accepted = 0
        exhausted = False

        def times():
            t = [0.] * len(fleet)
            for s in slots:
                if s['route']: t[s['i']] += s['time']
            return t

        def estimate():
            # The second key levels the load on plateaus of the max term (T and J).
            busy = [finish_with(i, -1, 0.) for i in range(len(fleet))]
            if goal == 'min_completion_time':
                return (max(busy), sum(b * b for b in busy))
            if goal == 'min_total_airtime':
                return (sum(times()), max(busy))
            return (cost_estimate(max(busy), times()), sum(b * b for b in busy))

        def better(a, b):
            return a[0] < b[0] - 1e-9 or (a[0] <= b[0] + 1e-9 and a[1] < b[1] - 1e-6)

        def exact_key(result):
            # (T, L), (L, T) or (J, Σ t_i²) of a realized plan; result[0] is the objective value.
            airtime = {}
            for m in result[1]:
                airtime[m['aircraft_id']] = airtime.get(m['aircraft_id'], 0.) + m['flight_time_s']
            if goal == 'min_completion_time':
                return (result[0], sum(airtime.values()))
            if goal == 'min_total_airtime':
                return (result[0], max(m['finish_s'] for m in result[1]))
            return (result[0], sum(t * t for t in airtime.values()))

        def rebuild(index, sequence, u):
            if not sequence: return (0., [], EMPTY_SUM)
            if units[u]['plane']:
                built = build(u, sequence)
                return None if built is None else (built[0], built[1], None)
            return copter_route(u, sequence, start_of(index)[0])

        def place(index, strip, u):
            """(total, sequence, legs, sum) of slot `index` taking `strip` with unit `u`, or None."""
            slot = slots[index]
            if units[u]['plane']:
                found = insert(index, LineString(strip[:2]), strip[2], u)
                return None if found is None else (found[0], found[1], found[2], None)
            sequence = slot.get('sequence', []) if slot['route'] else []
            home = start_of(index)[0]
            options = []
            for position in range(len(sequence) + 1):
                before = home if position == 0 else sequence[position - 1][1]
                after = sequence[position][0] if position < len(sequence) else None
                for a, b in ((strip[0], strip[1]), (strip[1], strip[0])):
                    detour = math.dist(before, a) + (math.dist(b, after) - math.dist(before, after) if after is not None else 0.)
                    options.append((detour, position, a, b))
            best = None
            for _, position, a, b in sorted(options, key=lambda o: o[:2])[:COST_SEARCH_POSITIONS]:
                candidate = sequence[:position] + [(a, b, strip[2])] + sequence[position:]
                built = copter_route(u, candidate, home)
                if built is not None and built[0] <= slot['budget'] and (best is None or built[0] < best[0]):
                    best = (built[0], candidate, built[1], built[2])
            return best

        def apply(changes):
            """Set slot fields; return the undo list."""
            undo = []
            for index, fields in changes:
                undo.append((index, {k: (k in slots[index], slots[index].get(k)) for k in fields}))
                slots[index].update(fields)
            return undo

        def revert(undo):
            # A field the slot did not have is removed again, not left as None.
            for index, fields in reversed(undo):
                for k, (present, value) in fields.items():
                    if present: slots[index][k] = value
                    else: slots[index].pop(k, None)

        def fields_of(built, u):
            total, sequence, legs, state_sum = built
            return dict(route=legs, sequence=sequence, time=total, u=u, sum=state_sum if state_sum is not None else EMPTY_SUM)

        def last_sortie(index):
            slot = slots[index]
            return all(not slots[k]['route'] for k in own[slot['i']] if slots[k]['sortie'] > slot['sortie'])

        def first_empty(index):
            slot = slots[index]
            if slot['route'] or index != own[slot['i']][-1]: return False
            return slot['sortie'] == 1 or bool(slots[own[slot['i']][-2]]['route'])

        def moves(base):
            nonlocal trials
            found = []
            used = {s['i'] for s in slots if s['route']}
            count = sum(len(s.get('sequence', [])) for s in slots if s['route'])
            for ai, a in enumerate(slots):
                if not a['route']: continue
                for k, strip in enumerate(a['sequence']):
                    rest = a['sequence'][:k] + a['sequence'][k+1:]
                    if not rest and not last_sortie(ai): continue
                    new_a = rebuild(ai, rest, a['u'])
                    if new_a is None or new_a[0] > a['budget']: continue
                    new_a = (new_a[0], rest, new_a[1], new_a[2] if len(new_a) > 2 else None)
                    for bi, b in enumerate(slots):
                        if b['i'] == a['i']: continue
                        if b['route']:
                            options = [b['u']] if b['u'] in eligible[strip[2]] else []
                        elif first_empty(bi) and (b['i'] in used or settings.max_aircraft is None or len(used) < settings.max_aircraft):
                            options = [u for u in units_of[b['i']] if u in eligible[strip[2]]]
                        else:
                            continue
                        for u in options:
                            placed = place(bi, strip, u)
                            if placed is None: continue
                            trials += 1
                            undo = apply([(ai, fields_of(new_a, a['u']) if rest else dict(route=[], sequence=[], time=0., sum=EMPTY_SUM)),
                                          (bi, fields_of(placed, u))])
                            value = estimate()
                            revert(undo)
                            if better(value, base):
                                found.append((value, ('move', ai, k, bi, u), [(ai, fields_of(new_a, a['u']) if rest else dict(route=[], sequence=[], time=0., sum=EMPTY_SUM)),
                                                                              (bi, fields_of(placed, u))]))
                            if trials >= COST_SEARCH_TRIALS: return found
            if count > COST_SWAP_STRIPS: return found
            for ai, a in enumerate(slots):
                if not a['route']: continue
                for bi in range(ai + 1, len(slots)):
                    b = slots[bi]
                    if not b['route'] or b['i'] == a['i']: continue
                    for k, x in enumerate(a['sequence']):
                        if a['u'] not in eligible[x[2]] and b['u'] not in eligible[x[2]]: continue
                        for m, y in enumerate(b['sequence']):
                            if b['u'] not in eligible[x[2]] or a['u'] not in eligible[y[2]]: continue
                            seq_a = a['sequence'][:k] + [y] + a['sequence'][k+1:]
                            seq_b = b['sequence'][:m] + [x] + b['sequence'][m+1:]
                            new_a, new_b = rebuild(ai, seq_a, a['u']), rebuild(bi, seq_b, b['u'])
                            if new_a is None or new_b is None or new_a[0] > a['budget'] or new_b[0] > b['budget']: continue
                            trials += 1
                            change = [(ai, fields_of((new_a[0], seq_a, new_a[1], new_a[2] if len(new_a) > 2 else None), a['u'])),
                                      (bi, fields_of((new_b[0], seq_b, new_b[1], new_b[2] if len(new_b) > 2 else None), b['u']))]
                            undo = apply(change)
                            value = estimate()
                            revert(undo)
                            if better(value, base):
                                found.append((value, ('swap', ai, k, bi, m), change))
                            if trials >= COST_SEARCH_TRIALS: return found
            return found

        def grow(move, change):
            """After a strip moves into an aircraft's last sortie, keep an empty next one as the allocation does."""
            if move[0] != 'move': return None
            bi = change[1][0]
            b = slots[bi]
            if b['sortie'] < fleet[b['i']]['max_sorties'] and own[b['i']][-1] == bi:
                own[b['i']].append(len(slots))
                slots.append(dict(i=b['i'], sortie=b['sortie']+1, budget=fleet[b['i']]['fresh_budget'], route=[], time=0., sum=EMPTY_SUM,
                                  u=units_of[b['i']][0] if len(units_of[b['i']]) == 1 else None))
                return b['i']
            return None

        def shrink(extra):
            if extra is not None:
                own[extra].pop()
                slots.pop()

        def snapshot():
            return [dict(slot) for slot in slots]

        def restore(saved):
            for slot, fields in zip(slots, saved):
                slot.clear()
                slot.update(fields)

        def dissolves():
            """Empty the last sortie of an aircraft into the others' sorties, one strip at a time.

            The estimate cannot see waits the schedule adds (a shared pad, crossing routes), so a
            plan with one aircraft fewer may look worse to it and still be better: these moves
            are realized exactly without the estimate filter.
            """
            nonlocal trials
            found = []
            for ai, a in enumerate(slots):
                if not a['route'] or not last_sortie(ai): continue
                saved = snapshot()
                strips = list(a['sequence'])
                change = [(ai, dict(route=[], sequence=[], time=0., sum=EMPTY_SUM))]
                apply(change[:1])
                complete = True
                for strip in strips:
                    options = []
                    for bi, b in enumerate(slots):
                        if b['i'] == a['i'] or not b['route'] or b['u'] not in eligible[strip[2]]: continue
                        placed = place(bi, strip, b['u'])
                        trials += 1
                        if placed is None: continue
                        trial = apply([(bi, fields_of(placed, b['u']))])
                        options.append((estimate(), bi, fields_of(placed, b['u'])))
                        revert(trial)
                    if not options:
                        complete = False
                        break
                    _, bi, fields = min(options, key=lambda o: (o[0], o[1]))
                    apply([(bi, fields)])
                    change.append((bi, fields))
                value = estimate()
                restore(saved)
                if complete:
                    found.append((value, ('dissolve', ai), change))
            return found

        while True:
            if trials >= COST_SEARCH_TRIALS or evaluations >= COST_SEARCH_EXACT:
                exhausted = True
                break
            found = moves(estimate())
            found.sort(key=lambda item: (item[0], item[1]))
            macro = sorted(dissolves(), key=lambda item: (item[0], item[1]))
            candidates_round = found[:COST_SEARCH_PER_ROUND] + macro[:COST_SEARCH_DISSOLVES]
            if not candidates_round: break
            # Best improvement of the round: every candidate is realized, the best one is kept.
            if current_key is None:
                current_key = exact_key((current[0], current[1]))
            chosen = None
            for value, move, change in candidates_round:
                if evaluations >= COST_SEARCH_EXACT:
                    exhausted = True
                    break
                undo = apply(change)
                extra = grow(move, change)
                evaluations += 1
                try:
                    result = realize(slots)
                except PlanningError:
                    result = None
                key = exact_key(result) if result is not None else None
                reference = chosen[0] if chosen else current_key
                if key is not None and (key[0] < reference[0] - 1e-6 or (key[0] <= reference[0] + 1e-9 and key[1] < reference[1] - 1e-6)):
                    chosen = (key, move, change, result)
                shrink(extra)
                revert(undo)
            if chosen is None: break
            key, move, change, result = chosen
            apply(change)
            grow(move, change)
            current_key = key
            v, scheduled, coverage, completion, total, missions, by_task = result
            current = [v, scheduled, coverage, current[3], completion, total, missions, by_task]
            accepted += 1
        return dict(result=tuple(current) if accepted else None, initial=initial, final=current[0], evaluations=evaluations,
                    trials=trials, accepted=accepted, budget_exhausted=exhausted,
                    limits=dict(trials=COST_SEARCH_TRIALS, exact=COST_SEARCH_EXACT))

    def key_of(candidate, target):
        # Exact lexicographic key of a candidate for an objective: (T, L), (L, T) or (J, Σ t_i²).
        completion, total, scheduled = candidate[4], candidate[5], candidate[1]
        if target == 'min_completion_time': return (completion, total)
        if target == 'min_total_airtime': return (total, completion)
        airtime = {}
        for m in scheduled:
            airtime[m['aircraft_id']] = airtime.get(m['aircraft_id'], 0.) + m['flight_time_s']
        return (cost_estimate(completion, [airtime.get(a, 0.) for a in aircraft_ids]), sum(t * t for t in airtime.values()))

    def rounded(key):
        # Keys compared to a micro-second: float noise must not decide between equal plans.
        return tuple(round(x, 6) for x in key)

    searches, searched_state, origins = {}, {}, {}
    pool = list(candidates)
    if forced is None and search:
        # One set of plans for all three criteria: the candidates of every direction and policy plus the
        # results of local searches. Each objective takes the best plan of the set by its own key, so the
        # plan of a criterion is never worse by that criterion than the plans of the other two computed
        # from the same scenario.
        # Starts: T and L from the best plan by their key among plans that do not depend on the cost
        # model (not the ΔJ allocation), so they are never worse than without J. J from the best
        # candidate by J and, when the other searches found a plan better by J, once more from it.
        independent = [state[k]['policy'] != 'min_cost' for k in range(len(candidates))]

        def run_search(target, allowed):
            nonlocal slots, own, finish, swaths, policy, coverage_info, widths, goal
            begin = min(allowed, key=lambda k: (rounded(key_of(pool[k], target)), k))
            st = state[begin] if begin < len(candidates) else searched_state[begin]
            # The search changes the slots it works on: each run starts from its own copy.
            slots = [dict(slot) for slot in st['slots']]
            own = [list(x) for x in st['own']]
            finish, swaths, policy = list(st['finish']), st['swaths'], st['policy']
            coverage_info, widths = st['coverage_info'], st['widths']
            goal = target
            first = list(pool[begin])
            first[0] = key_of(first, target)[0]
            found = local_search(tuple(first), dict(st, slots=slots, own=own))
            if target not in searches or (found['result'] is not None and searches[target]['result'] is None):
                searches[target] = found
            if found['result'] is not None:
                pool.append(found['result'])
                independent.append(target != 'min_cost' and independent[begin])
                origins[len(pool) - 1] = target
                searched_state[len(pool) - 1] = dict(st, slots=[dict(slot) for slot in slots], own=[list(x) for x in own], finish=list(finish))
            return begin

        starts = {}
        for target in ('min_completion_time', 'min_total_airtime'):
            starts[target] = {run_search(target, [k for k in range(len(pool)) if independent[k]] or list(range(len(pool))))}
        starts['min_cost'] = {run_search('min_cost', list(range(len(candidates))))}
        # Second pass: when another criterion's search found the best plan by this key, search on from it.
        for target in ('min_cost', 'min_completion_time', 'min_total_airtime'):
            leader = min(range(len(pool)), key=lambda k: (rounded(key_of(pool[k], target)), k))
            if leader not in starts[target] and origins.get(leader) != target:
                starts[target].add(run_search(target, [leader]))
        goal = scenario.objective
    best = min(range(len(pool)), key=lambda k: (rounded(key_of(pool[k], scenario.objective)), k))
    search = None
    if searches:
        own_search = searches[scenario.objective]
        search = dict({k: v for k, v in own_search.items() if k != 'result'}, objective=scenario.objective,
                      chosen_from=origins.get(best, 'candidate'), final=key_of(pool[best], scenario.objective)[0])
    value, missions, coverage, angle, completion, total, unscheduled, by_task = pool[best]
    value = key_of(pool[best], scenario.objective)[0]
    # Every plan of the set as completion and flight time per aircraft: the trade-off the criteria choose from.
    variants = []
    for k, item in enumerate(pool):
        flown = {}
        for m in item[1]:
            flown[m['aircraft_id']] = flown.get(m['aircraft_id'], 0.) + m['flight_time_s']
        variants.append(dict(completion_s=item[4], airtime_s=flown, origin=origins.get(k, 'candidate'), chosen=k == best))
    return dict(value=value, missions=missions, unscheduled=unscheduled, coverage=coverage, angle=angle, completion_s=completion, airtime_s=total, variants=variants,
                geo=geo, fleet=fleet, units=units, tasks=tasks, by_task=by_task, task_zones=task_zones, charging=charging, chargers=chargers,
                airspace_report=airspace_report, altitudes=dict(altitudes), directions=directions, search=search, legacy=legacy)


def _relevel(scenario, profiles, run, assigned, vertical):
    """The first plan's routes flown at `assigned` levels and scheduled again; None if a sortie no longer fits.

    Only multirotors change level here: they climb and descend vertically over the pad, so the
    route stays the same and each sortie grows by the extra climb and descent. The camera is
    recomputed at the new height; strips keep the first plan's spacing, which a higher camera
    covers with more side overlap.
    """
    settings = scenario.settings
    weather = scenario.weather
    fleet = {v['aircraft'].id: v for v in run['fleet']}
    missions, extra = [], {}
    for mission in run['unscheduled']:
        vehicle = fleet[mission['aircraft_id']]
        level = assigned[mission['aircraft_id']]
        mission = dict(mission)
        after = mission.get('after')
        if after is not None and after in extra and mission.get('ground_s') is not None:
            # A longer previous sortie drains the battery more: charging takes longer.
            if 'charge_s' in mission:
                longer = vehicle['charge_s'] * extra[after] / vehicle['full_s']
                mission.update(charge_s=mission['charge_s'] + longer, ground_s=mission['ground_s'] + longer)
        if level == mission['altitude_m'] or mission.get('fixed_altitude'):
            # A requirement that fixes its survey height keeps the sortie where it is.
            missions.append(mission)
            continue
        if mission['airframe'] != 'multirotor':
            return None
        legs, elapsed = [], 0.
        for leg in mission['legs']:
            leg = dict(leg)
            seconds = leg['end'] - leg['start']
            if leg['phase'] == 'climb':
                seconds = level / 2; leg.update(altitude_end_m=level)
            elif leg['phase'] == 'descent':
                seconds = level / 2; leg.update(altitude_start_m=level)
            elif leg['phase'] not in ('takeoff', 'landing'):
                leg.update(altitude_start_m=level, altitude_end_m=level)
            leg.update(start=elapsed, end=elapsed + seconds)
            elapsed += seconds
            legs.append(leg)
        if elapsed > mission['budget_s'] + 1e-6:
            return None
        survey = mission['survey']
        if not run['legacy']:
            surveys = _resurvey(scenario, run, mission, legs, level)
            if surveys is None:
                return None
            mission.update(surveys=surveys, survey=surveys[mission['requirement_ids'][0]])
            survey = mission['survey']
        elif survey['mode'] == 'camera':
            aircraft = vehicle['aircraft']
            survey = dict(camera_geometry(aircraft, profiles[aircraft.profile_id], settings, level))
            captures, intervals = 0, []
            for leg in legs:
                if leg['phase'] != 'survey': continue
                count = max(2, math.ceil(math.dist(*leg['xy'])/survey['photo_step_m'])+1)
                interval = (leg['end']-leg['start'])/(count-1)
                if survey['trigger_interval_s'] is not None and interval+1e-9 < survey['trigger_interval_s']:
                    return None
                leg.update(photo_count=count, photo_interval_s=interval, photo_step_m=math.dist(*leg['xy'])/(count-1))
                captures += count; intervals.append(interval)
            survey.update(photo_count=captures, min_photo_interval_s=min(intervals))
        extra[len(missions)] = elapsed - mission['flight_time_s']
        mission.update(legs=legs, altitude_m=level, survey=survey, flight_time_s=elapsed,
                       battery_end_percent=mission['battery_start_percent'] - 100*elapsed/vehicle['full_s'])
        missions.append(mission)
    scheduled = schedule(missions, settings.turnaround_min*60, run['chargers'] if run['charging'] else None, settings.ground_handling_min*60, vertical)
    if any(conflict(a['legs'],b['legs'],SEPARATION_M,vertical) for i,a in enumerate(scheduled) for b in scheduled[:i]):
        return None
    completion = max(m['finish_s'] for m in scheduled)
    total = sum(m['flight_time_s'] for m in scheduled)
    report = dict(run['airspace_report'], flight_altitude_max_m=max(assigned.values()))
    if scenario.objective == 'min_cost':
        airtime = {}
        for m in scheduled:
            airtime[m['aircraft_id']] = airtime.get(m['aircraft_id'], 0.) + m['flight_time_s']
        value = breakdown(cost_preset.effective(scenario, profiles), [a.id for a in scenario.fleet], completion, airtime)['total']
    else:
        value = completion if scenario.objective == 'min_completion_time' else total
    return dict(run, value=value, missions=scheduled, unscheduled=missions,
                completion_s=completion, airtime_s=total, altitudes=dict(assigned), airspace_report=report)


def _resurvey(scenario, run, mission, legs, level):
    """Survey geometry of every requirement of a sortie at a new level; None if a requirement's GSD or the camera rate no longer fits."""
    tasks = {t.id: (r, t) for r, t in enumerate(run['tasks'])}
    catalog = sensor_catalog(scenario)
    sensor = catalog.get(mission['sensor_id']) or generic_sensor(mission['survey_types'][0])
    aircraft = next(a for a in scenario.fleet if a.id == mission['aircraft_id'])
    surveys = {}
    for requirement_id in mission['requirement_ids']:
        r, task = tasks[requirement_id]
        survey, problem = survey_geometry(sensor, task, level, aircraft.trigger_interval_s)
        if problem:
            return None
        if survey['mode'] == 'camera':
            captures, intervals = 0, []
            for leg in legs:
                if leg['phase'] != 'survey' or leg.get('requirement') != r: continue
                count = max(2, math.ceil(math.dist(*leg['xy'])/survey['photo_step_m'])+1)
                interval = (leg['end']-leg['start'])/(count-1)
                if survey['trigger_interval_s'] is not None and interval+1e-9 < survey['trigger_interval_s']:
                    return None
                leg.update(photo_count=count, photo_interval_s=interval, photo_step_m=math.dist(*leg['xy'])/(count-1))
                captures += count; intervals.append(interval)
            survey.update(photo_count=captures, min_photo_interval_s=min(intervals))
        for leg in legs:
            if leg['phase'] == 'survey' and leg.get('requirement') == r:
                leg.update(swath_width_m=survey['swath_width_m'], footprint_mode=survey['mode'])
        surveys[requirement_id] = survey
    return surveys


def _post_processing(scenario, started, settings, profiles, sites, observation, missions, coverage, angle, geo, fleet, fleet_by_id,
                     charging, airspace_report, vertical, echelons, run):
    tasks, legacy = run['tasks'], run['legacy']
    for mission in missions:
        vehicle = fleet_by_id[mission['aircraft_id']]
        mission['alternate'] = reachability(dict(vehicle, budget=mission['budget_s']), mission['legs'], mission['start_s'], geo, scenario.sites, scenario.weather)
    footprint = footprint_of(missions)
    surveyable = dict(zip(geo.area_numbers, geo.areas))
    area_coverage = []
    for number, drawn in enumerate(geo.original_areas, 1):
        zone = surveyable.get(number)
        area_coverage.append(dict(index=number, area_m2=zone.area if zone else 0.,
                                  coverage_percent=zone.intersection(footprint).area/zone.area*100 if zone else 0.,
                                  excluded_m2=drawn.area-zone.area if zone else drawn.area))
    requirement_coverage = []
    if not legacy:
        area_ids = scenario.area_id_list()
        for r, task in enumerate(tasks):
            zones, area = run['task_zones'][r]
            drawn = unary_union([geo.original_areas[area_ids.index(a)] for a in task.area_ids])
            flown = [m for m in missions if any(l.get('requirement') == r for l in m['legs'] if l['phase'] == 'survey')]
            covered = area.intersection(footprint_of(missions, r)).area / area.area * 100 if area is not None and area.area > 0 else 0.
            requirement_coverage.append(dict(id=task.id, name=task.name, label=task_label(task), survey_type=task.survey_type, area_ids=list(task.area_ids),
                                             area_m2=area.area if area is not None else 0., excluded_m2=drawn.area - (area.area if area is not None else 0.),
                                             coverage_percent=covered, aircraft=sorted({m['aircraft_id'] for m in flown}),
                                             sensors=sorted({m['sensor_name'] for m in flown}), sorties=len(flown),
                                             max_gsd_cm=task.max_gsd_cm, altitude_m=task.altitude_m))
    for mission in missions:
        mission.pop('unit', None)
        for leg in mission['legs']:
            if 'requirement' in leg:
                leg['requirement_id'] = tasks[leg.pop('requirement')].id
        for sequence,leg in enumerate(mission['legs']):
            leg['coordinates'] = [list(geo.inverse(*p)) for p in leg.pop('xy')]
            leg['sequence'] = sequence
            # A plane also flies at airspeed on the catapult run, the climb and the descent before the parachute.
            powered = leg['phase'] in ('transit','survey','turn') or (mission['airframe']=='fixed_wing' and leg['phase'] in ('takeoff','climb','descent'))
            leg['speed_mps'] = mission['airspeed_mps'] if powered else scenario.weather.wind_speed_mps if leg['phase']=='parachute' else 0
            leg['speed_reference'] = 'air' if powered else 'ground'
            leg['vertical_speed_mps'] = (leg['altitude_end_m']-leg['altitude_start_m'])/(leg['end']-leg['start'])
            leg['altitude_reference'] = 'AGL'
    warnings = [
        'Демонстрационный расчет: плоский рельеф, постоянный ветер, идеализированная кинематика. Не готовое задание для автопилота.',
        'Заряд учтен через консервативный бюджет времени; энергетическая модель не откалибрована.',
        'Сближения разделены по времени с горизонтальным интервалом 30 м. Ожидание начала вылета происходит на земле.' if vertical is None else
        f'Сближения разделены: не ближе 30 м по горизонтали или {vertical:g} м по высоте. Высота на наборе и снижении меняется линейно, '
        'поэтому борта, проходящие через чужой эшелон над общей площадкой, разводятся по времени. Ожидание начала вылета — на земле.',
    ]
    levels = {}
    for mission in missions:
        levels.setdefault(mission['altitude_m'], set()).add(mission['aircraft_id'])
    if len(levels) > 1 or echelons:
        text = '; '.join(f'{height:g} м — ' + ', '.join(sorted(ids, key=lambda x: (len(x), x))) for height, ids in sorted(levels.items()))
        line = f'Высоты полета (AGL, съемка и перелеты): {text}.'
        if echelons:
            line += (f' Автоматические эшелоны от высоты съемки {settings.altitude_m:g} м с шагом {settings.vertical_separation_m:g} м, '
                     f'доступны {", ".join(f"{h:g}" for h in echelons["levels"])} м.')
            before, after = echelons['shared_same_level_before_m'], echelons['shared_same_level_after_m']
            if echelons['applied']:
                single = echelons['single_level']
                line += (f' Общие участки маршрутов ближе 30 м на одной высоте: {before/1000:.1f} км → {after/1000:.1f} км; их разводит только время. '
                         f'На одной высоте завершение заняло бы {single["completion_s"]/60:.1f} мин, налет {single["total_airtime_s"]/60:.1f} мин.')
            elif before >= 1:
                line += f' Общие участки маршрутов ближе 30 м на одной высоте: {before/1000:.1f} км, их разводит только время.'
            if echelons.get('note'):
                line += ' ' + echelons['note']
        if settings.coverage_mode == 'camera':
            line += ' GSD и захват камеры — на высоте каждого борта; галсы общие по самому узкому захвату.'
        warnings.append(line)
    if max(levels) > settings.altitude_ceiling_m + 1e-9:
        warnings.append(f'Высота полета до {max(levels):g} м над поверхностью выше заданного потолка {settings.altitude_ceiling_m:g} м. '
                        + ('Без разрешения на использование воздушного пространства полеты БВС до 30 кг допускаются ниже 150 м от поверхности '
                           '(в пределах прямой видимости, днем, вне диспетчерских зон и зон ограничений) — нужен разрешительный порядок.'
                           if settings.altitude_ceiling_m == 150 else 'Проверьте разрешительный порядок использования воздушного пространства.'))
    if not legacy:
        warnings.append(f'Требования съемки: {len(tasks)} — ' + '; '.join(f"{row['label']}: {row['coverage_percent']:.1f}%" for row in requirement_coverage)
                        + '. Каждое требование покрывается и проверяется отдельно; площади разных видов съемки не суммируются. '
                        'Вылет несет один сенсор: одновременная съемка разными сенсорами не планируется.')
        changes = [m for m in missions if m.get('change_s')]
        if changes:
            warnings.append(f'Смена полезной нагрузки между вылетами: {len(changes)} раз по {settings.payload_change_min:g} мин — допущение, '
                            'время смены и сама возможность смены в поле заказчиком не подтверждены.')
        types = {t.survey_type for t in tasks}
        if 'thermal' in types:
            warnings.append('ИК: геометрия тепловизора 801 не подтверждена документацией; полоса задана в требовании оператором. Тепловое разрешение и частота кадров не проверены.')
        if 'lidar' in types:
            warnings.append('LiDAR: полоса — по углу обзора профиля или заданная в требовании; плотность точек и перекрытие сканов не проверяются.')
        if 'geophysical' in types:
            warnings.append('Геофизика: шаг линий задан требованием; чувствительность датчика и высота огибания рельефа не проверяются.')
        if types & {'rgb', 'multispectral'}:
            warnings.append('Кадровые камеры: съемка в надир, длинная сторона кадра поперек курса; GSD номинальный, не точность фотограмметрии. '
                            'Галсы требования общие по самому узкому захвату борта, который может его выполнять.')
    elif settings.coverage_mode == 'camera':
        sensor_label = 'RGB' if scenario.survey_type == 'rgb' else 'Мультиспектральная (Geoscan Pollux)'
        warnings.append(f'{sensor_label}: съемка в надир, длинная сторона кадра поперек курса; GSD номинальный, не точность фотограмметрии. Галсы общие по самой узкой камере; снимки рассчитаны с продольным перекрытием. Наклон, смаз, рельеф и калибровка не моделируются.')
        if scenario.survey_type == 'multispectral':
            warnings.append('Геометрия Pollux не означает обработку спектральных каналов: регистрация полос, радиометрическая калибровка и вегетационные индексы не входят в расчет.')
        if any(m['survey'].get('trigger_interval_s') is None for m in missions):
            warnings.append('Предельный рабочий интервал камеры не задан для части бортов: показан требуемый интервал, его выполнимость нужно подтвердить.')
    elif legacy and scenario.survey_type == 'rgb':
        warnings.append('Ширина захвата задана вручную. Покрытие геометрическое; GSD, продольное перекрытие и частота кадров не проверены.')
    elif legacy and scenario.survey_type == 'multispectral':
        warnings.append('Мультиспектральная съемка в ручном режиме: ширина задана оператором, не выведена из камеры. Для расчета по камере Pollux переключите режим покрытия на «По камерам».')
    elif legacy and scenario.survey_type == 'thermal':
        warnings.append('ИК: подтвержденного профиля тепловизионной камеры 801 пока нет (не хватает размера пикселя матрицы для геометрии). Ширина захвата задана вручную; GSD, тепловое разрешение и частота кадров не проверены.')
    elif legacy and scenario.survey_type == 'lidar':
        warnings.append('LiDAR: ширина — заданная оператором полоса сканирования, не паспортный параметр конкретного сканера. Плотность точек, перекрытие сканов и угол обзора не проверяются.')
    elif legacy:
        warnings.append('Геофизическая съемка: ширина — заданный оператором шаг линий, не характеристика конкретного датчика (например, магнитометра). Чувствительность, высота буксировки и разрешение не проверяются.')
    if any(v['plane'] for v in fleet):
        f = settings.fixed_wing
        plane_missions = [m for m in missions if m['airframe'] == 'fixed_wing']
        spirals = sorted({m['aircraft_id'] for m in plane_missions if m['fixed_wing_assumptions']['spiral_climb']})
        crab = max((m['survey'].get('crab_deg', 0) for m in plane_missions), default=0)
        narrowed = [m for m in plane_missions if m['survey']['swath_width_m'] < m['survey'].get('nominal_swath_width_m', 0) - 1e-6]
        speeds = sorted({m['airspeed_mps'] for m in plane_missions}) or [f.airspeed_mps]
        warnings.append('Самолет — демонстрационная модель. Развороты с постоянным креном: радиус '
                        f'{f.turn_radius_m:g} м относительно воздуха (крен ≈ {", ".join(f"{bank_deg(f.turn_radius_m, v):.0f}° при {v:g} м/с" for v in speeds)}), '
                        'в ветре траектория разворота вытягивается по ветру; из шести вариантов пути Дубинса, включая петли, берется быстрейший. '
                        f'Галсы облетаются в выгодном порядке, не обязательно подряд; перед каждым — прямой заход {f.lead_in_m:g} м. '
                        + ('Набор высоты по спирали над площадкой (прямо не хватает места): ' + ', '.join(spirals) + '. ' if spirals else 'Набор высоты прямо по курсу запуска. ')
                        + (f'Перед выпуском парашюта — снижение кругами до {f.parachute_release_m:g} м над точкой выпуска с вертикальной скоростью набора. ' if f.parachute_release_m is not None else 'Парашют выпускается с высоты полета. ')
                        + 'Радиус, набор, заход, 50 м запуска и 20 с завершения посадки — допущения, не паспортные параметры 201. Переходные процессы и запасная посадка не проверяются.')
        if crab >= .5:
            warnings.append(f'Снос самолета в боковом ветре до {crab:.0f}°: камера закреплена на корпусе, кадры повернуты к галсу. '
                            + (f'Надежная полоса захвата уменьшена у {", ".join(sorted({m["aircraft_id"] for m in narrowed}))}; галсы сближены.' if narrowed
                               else 'При заданном продольном перекрытии надежная полоса захвата не уменьшилась.')
                            + ' Перекрытие соседних кадров в системе снимка ниже номинального.')
    unused = [a.id for a in scenario.fleet if a.id not in {m['aircraft_id'] for m in missions}]
    if observation:
        warnings.append('Применены модельные данные Open-Meteo: ветер на 10 м, не на высоте съемки. Порывы проверены по пределу парка; осадки и температура показаны справочно. Локальные изменения погоды не моделируются.')
    if any(p.origin == 'user' for p in profiles.values()):
        warnings.append('Использованы пользовательские характеристики БПЛА; они не проверены изготовителем. Модель движения остается идеализированной.')
    if unused: warnings.append('Не задействованы при выбранном критерии: '+', '.join(unused))
    repeat = [m for m in missions if m['sortie'] > 1]
    all_sites = {s.id: s for s in scenario.sites}
    if charging:
        charged_h = sum(m.get('charge_s', 0) for m in repeat)/3600
        waited_h = sum(m.get('charge_wait_s', 0) for m in repeat)/3600
        on_reserve = sorted({all_sites[m['charge_site_id']].name for m in repeat if all_sites[m['charge_site_id']].kind == 'alternate'})
        times = sorted({f"{p.name} — {p.charge_time_min:g} мин ({p.charge_source.split(';')[0]})" if p.charge_time_min else f'{p.name} — {settings.charge_time_min:g} мин (настройка)'
                        for p in profiles.values()})
        warnings.append(f'Зарядка между вылетами: {len(repeat)} повторных вылетов, зарядка {charged_h:.1f} ч, ожидание свободного ЗУ {waited_h:.1f} ч, '
                        f'обслуживание на земле {settings.ground_handling_min:g} мин перед каждой зарядкой. Каждое ЗУ заряжает одну АКБ за раз; '
                        f'борт ждет свою АКБ и взлетает с площадки, где заряжался. Полная зарядка: {"; ".join(times)}. Заряд линейный по израсходованной емкости.'
                        + (f' Заряжаются на запасных площадках: {", ".join(on_reserve)}.' if on_reserve else ''))
        no_charge = [v['aircraft'].id for v in fleet if v['max_sorties'] == 1]
        if no_charge:
            warnings.append('Без повторных вылетов (нет доступного зарядного места): ' + ', '.join(no_charge) + '.')
    elif settings.sorties_per_aircraft > 1:
        warnings.append(f'Повторные вылеты: до {settings.sorties_per_aircraft} на борт, {len(repeat)} запланировано. Каждый повторный вылет — с той же площадки '
                        f'через {settings.turnaround_min:g} мин на замену АКБ заряженной запасной; наличие запасных АКБ не проверяется.'
                        + (' Посадка на другую площадку при повторных вылетах отключена.' if settings.landing_policy == 'best_main_site' else ''))
    if settings.landing_policy == 'best_main_site' and settings.sorties_per_aircraft == 1:
        moved = [f"{m['aircraft_id']} → {sites[m['landing_site_id']].name}" for m in missions if m['landing_site_id'] != m['site_id']]
        warnings.append('Посадка разрешена на любую основную площадку: для каждого борта выбрана площадка с кратчайшим возвращением из конца маршрута. Экипаж, место и зарядка на площадке посадки не проверяются; повторный вылет не планируется. '
                        + ('На другую площадку садятся: '+', '.join(moved)+'.' if moved else 'В этом плане все борта возвращаются на площадку старта.'))
    report = airspace_report
    terrain = (f'{settings.terrain_min_m:g} м' if settings.terrain_min_m == settings.terrain_max_m
               else f'{settings.terrain_min_m:g}–{settings.terrain_max_m:g} м (в худшую сторону)')
    if scenario.restriction_zones:
        warnings.append(f"Зоны ограничений: учтено {len(report['zones_applied'])}, выше высоты полета с запасом {len(report['zones_above'])}, "
                        f"не действуют в окне операции {len(report['zones_inactive'])}. Высоты AMSL и эшелоны пересчитаны с рельефом {terrain} "
                        f"и стандартной атмосферой; зона, достигающая высоты {max(levels):g} м + {settings.vertical_buffer_m:g} м, облетается целиком на всех высотах.")
        if report['zones_time_assumed']:
            warnings.append(f"Время действия неизвестно у {len(report['zones_time_assumed'])} зон (или не задано начало операции): они считаются действующими.")
    if scenario.obstacles:
        amsl = sum(1 for o in scenario.obstacles if o.height_reference == 'AMSL')
        warnings.append(f"Препятствия: облетается {len(report['obstacles_applied'])} с отступом {settings.obstacle_clearance_m:g} м; "
                        f"{report['obstacles_below']} ниже высоты полета{' (самой низкой)' if len(levels) > 1 else ''} минус запас {settings.vertical_buffer_m:g} м пролетаются сверху."
                        + (f' {amsl} заданы над уровнем моря и пересчитаны с рельефом {settings.terrain_min_m:g} м (минимум района).' if amsl else ''))
    if geo.excluding:
        blocked_by = ', '.join(b['label'] for b in geo.excluded_by[:6]) + ('…' if len(geo.excluded_by) > 6 else '')
        warnings.append(f'Из съемки исключено {geo.excluded_m2/1e4:.2f} га под ограничениями с учетом отступов'
                        + (f' ({blocked_by})' if blocked_by else '') + ('; часть района вне разрешенного пространства' if geo.excluded_outside else '')
                        + f'. Доступная часть покрыта на {coverage*100:.1f}%: у границ ограничений галсы прерываются.')
    completion_s = max(m['finish_s'] for m in missions)
    due = deadline(scenario)
    limit_s = due['limit_s']
    if due['starts_in_dark']:
        warnings.append('Начало работ приходится на темное время суток: срок «до заката» отсчитан до следующего заката.')
    if limit_s is not None and completion_s > limit_s:
        warnings.append(f'План не укладывается в срок: завершение через {completion_s/60:.1f} мин при лимите {limit_s/60:.0f} мин'
                        + (' (до заката)' if due['source'] == 'sunset' else '') + '. Добавьте борта, повторные вылеты, площадки ближе к району или сократите район; '
                        'подбор числа бортов — в анализе парка.')
    if settings.max_aircraft is not None and settings.max_aircraft < len(scenario.fleet):
        warnings.append(f'Задействовано не более {settings.max_aircraft} бортов из {len(scenario.fleet)} по ограничению задания.')
    constraints = dict(time_limit_s=limit_s, time_limit_source=due['source'], sunset=due['sunset'], completion_s=completion_s,
                       meets_time_limit=None if limit_s is None else completion_s <= limit_s,
                       max_aircraft=settings.max_aircraft, aircraft_used=len({m['aircraft_id'] for m in missions}), sorties=len(missions),
                       operation_start=settings.operation_start.isoformat() if settings.operation_start else None,
                       operation_end=(settings.operation_start+timedelta(seconds=completion_s)).isoformat() if settings.operation_start else None)
    reserve_sites = [s for s in scenario.sites if s.kind == 'alternate']
    if not reserve_sites:
        warnings.append('Резервные площадки не заданы: аварийный вариант посадки при недостижимости основной площадки не проверялся.')
    else:
        warnings.append('Достижимость резервных площадок оценена по кратчайшему свободному пути на скорости борта в самой удаленной точке маршрута; развороты, потеря высоты и энергия на маневр не учтены. Это не сертифицированное решение «годен/не годен».')
        unreachable = [m['aircraft_id'] + (f" (вылет {m['sortie']})" if m['sortie'] > 1 else '') for m in missions if m['alternate']['reachable'] is False]
        if unreachable:
            warnings.append('Ни одна резервная площадка не гарантированно достижима с оставшимся ресурсом: '+', '.join(unreachable)+'.')
    maintenance = []
    for aircraft_id in dict.fromkeys(m['aircraft_id'] for m in missions):
        flown = [m for m in missions if m['aircraft_id'] == aircraft_id]
        profile = profiles[flown[0]['profile_id']]
        seconds = sum(m['flight_time_s'] for m in flown)
        interval, unit = profile.maintenance_interval, profile.maintenance_unit
        # Motor hours are taken equal to flight hours: ground runs are not modelled.
        share = None if interval is None else len(flown)/interval if unit == 'flights' else seconds/3600/interval
        maintenance.append(dict(aircraft_id=aircraft_id, profile_id=profile.id, sorties=len(flown), flight_time_s=seconds,
                                interval=interval, unit=unit, interval_share=share, source=profile.maintenance_source))
    if any(m['interval'] is None for m in maintenance):
        warnings.append('Для части бортов нет норматива ТО: расход ресурса до обслуживания для них не посчитан.')
    elif maintenance:
        warnings.append('Расход ресурса до ТО посчитан по нормативам заказчика: по числу полетов (201) или по налету в часах (801, Gemini — по аналогии с 801); моточасы приравнены к налету.')
    airspace_out = dict(report, excluded_area_m2=geo.excluded_m2, excluded_by=geo.excluded_by, excluded_outside_allowed=geo.excluded_outside,
                        blockers_considered=len(geo.blockers), free_space_vertices=len(geo.nodes))
    result = dict(id=str(uuid4()),status='demonstration',objective=scenario.objective,algorithm_version='1.2',missions=missions,warnings=warnings,
        echelons=echelons,separation=dict(horizontal_m=SEPARATION_M,vertical_m=vertical),
        weather=scenario.weather.model_dump(mode='json'), aircraft_profiles=[p.model_dump(mode='json') for p in profiles.values()],
        airspace=airspace_out, constraints=constraints,
        metrics=dict(survey_area_count=len(geo.original_areas),coverage_by_area=area_coverage,completion_time_s=completion_s,total_airtime_s=sum(m['flight_time_s'] for m in missions),
        total_distance_m=sum(m['distance_m'] for m in missions),area_m2=geo.area.area,excluded_area_m2=geo.excluded_m2,coverage_percent=coverage*100,
        aircraft_used=len({m['aircraft_id'] for m in missions}),sortie_count=len(missions),maintenance=maintenance,
        charging_s=sum(m.get('charge_s', 0) for m in missions),charger_wait_s=sum(m.get('charge_wait_s', 0) for m in missions),
        maintenance_share_total=sum(m['interval_share'] for m in maintenance if m['interval_share'] is not None),strip_angle_deg=angle,coverage_by_requirement=requirement_coverage or None,calculation_time_s=time.monotonic()-started))

    model = cost_preset.effective(scenario, profiles)
    airtime = {}
    for m in missions:
        airtime[m['aircraft_id']] = airtime.get(m['aircraft_id'], 0.) + m['flight_time_s']
    search = run.get('search')
    result['search'] = search
    # The model as the scenario gives it (type coefficients kept); the computation uses it written out per aircraft.
    result['cost'] = dict(breakdown(model, [a.id for a in scenario.fleet], completion_s, airtime), model=(scenario.cost_model or model).model_dump(),
                          default_model=scenario.cost_model is None, search=result['search'] if scenario.objective == 'min_cost' else None,
                          tradeoff=tradeoff(model, [a.id for a in scenario.fleet], run.get('variants') or [], relevelled=bool(echelons and echelons.get('applied'))))
    if search and scenario.objective != 'min_cost' and search['final'] < search['initial'] - 1e-6:
        warnings.append(f"Локальный поиск улучшил {'срок' if scenario.objective == 'min_completion_time' else 'суммарный налет'}: "
                        f"{search['initial'] / 60:.1f} → {search['final'] / 60:.1f} мин{', бюджет поиска исчерпан' if search['budget_exhausted'] else ''}.")
    names = {'min_completion_time': 'срок', 'min_total_airtime': 'суммарный налет', 'min_cost': 'стоимость J'}
    if search and search.get('chosen_from') in names and search['chosen_from'] != scenario.objective:
        warnings.append(f"Лучший по критерию «{names[scenario.objective]}» план нашел локальный поиск по критерию «{names[search['chosen_from']]}»: "
                        'поэтому при изменении коэффициентов J этот план может стать лучше, но не хуже результата собственного поиска.')
    if search:
        warnings.append('Три критерия выбирают из одного набора планов (варианты галсов и распределения и итоги локального поиска '
                        'по каждому критерию), поэтому план критерия не хуже двух других по своему показателю. Это эвристика: '
                        'глобальный оптимум не гарантирован.')
    if scenario.objective == 'min_cost':
        c = result['cost']
        warnings.append(f"Критерий стоимости: J = α·T + cᵀt + tᵀQt = {c['total']:.0f} у.е.: срок {c['linear']:.0f}, налет {c['airtime_total']:.0f}, "
                        f"риск по бортам {c['individual_total']:.1f}, пары {c['pairs_total']:.1f}. "
                        + ('Коэффициенты — оценка по открытым данным (ред. 2026-10, 1 у.е. ≈ 1 ₽), не данные заказчика. ' if c['default_model'] else '')
                        + 'Поиск — эвристика с локальными улучшениями, глобальный оптимум не гарантирован.')
    result['diagnostics'] = warnings_of(result, scenario)
    return result