"""Survey requirements as planning tasks, and the units an aircraft can fly them with.

A task is one survey requirement: areas + survey type + quality parameters. A scenario without
requirements (version 1.1 and earlier) is one task: its survey type over all areas with the swath,
overlaps and GSD limit of its settings, flown with each aircraft's camera or the manual swath.

A unit is what one sortie is flown with: aircraft + sensor + survey height + speed. A sortie
carries one sensor: simultaneous survey types are not planned, and a sortie with another sensor
than the previous one first spends `payload_change_min` on the ground.
"""
from bas.catalog.sensors import builtin_sensors, compatible, survey_geometry
from bas.domain.models import SensorProfile, SurveyRequirement
from bas.planning.flight_models.fixed_wing import landing, operations

TYPE_NAMES = {'rgb': 'RGB', 'multispectral': 'мультиспектральная', 'thermal': 'ИК', 'lidar': 'LiDAR', 'geophysical': 'геофизическая'}
LEGACY_ID = 'survey'


def survey_tasks(scenario):
    if scenario.requirements is not None:
        return list(scenario.requirements)
    s = scenario.settings
    return [SurveyRequirement(id=LEGACY_ID, name='', area_ids=scenario.area_id_list(), survey_type=scenario.survey_type,
                              side_overlap_percent=s.side_overlap_percent, front_overlap_percent=s.front_overlap_percent,
                              max_gsd_cm=s.max_gsd_cm, swath_width_m=s.swath_width_m if s.coverage_mode == 'manual' else None)]


def generic_sensor(survey_type):
    """Payload declared on the aircraft without a profile: its geometry is unknown, the swath comes from the requirement."""
    return SensorProfile(id=f'generic_{survey_type}', version=1, origin='builtin', name=f'Нагрузка {TYPE_NAMES[survey_type]} без профиля',
                         sensor_type=survey_type, geometry='unknown', compatible_profiles=[],
                         source='Нагрузка задана у борта без профиля сенсора: ширина полосы — из требования съемки.')


def sensor_catalog(scenario, extra=()):
    """Profiles by id: built-in, then the catalog `extra`, then the scenario snapshot (it wins)."""
    catalog = {p.id: p for p in builtin_sensors()}
    catalog.update({f'generic_{kind}': generic_sensor(kind) for kind in TYPE_NAMES})
    catalog.update({p.id: p for p in extra})
    catalog.update({p.id: p for p in scenario.sensor_profiles})
    return catalog


def aircraft_sensors(aircraft, catalog):
    """[(id, profile or None)] available to the aircraft. Without a list: its 1.1 camera or a generic payload."""
    if aircraft.sensors is not None:
        return [(sid, catalog.get(sid)) for sid in aircraft.sensors]
    if aircraft.camera_id:
        return [(aircraft.camera_id, catalog.get(aircraft.camera_id))]
    return [(f'generic_{aircraft.payload}', generic_sensor(aircraft.payload))]


def sensor_usable(sensor, aircraft_profile):
    """Generic payloads are the operator's declaration, like a 1.1 manual swath."""
    return sensor.id.startswith('generic_') or compatible(sensor, aircraft_profile)


def task_label(task):
    name = task.name or task.id
    return f'{name} · {TYPE_NAMES.get(task.survey_type, task.survey_type)}'


def build_units(scenario, profiles, tasks, fleet, geo):
    """(units, unit indices per aircraft, eligible units per task, problems per task)."""
    settings = scenario.settings
    catalog = sensor_catalog(scenario)
    units, units_of, by_key = [], [[] for _ in fleet], {}
    eligible = [set() for _ in tasks]
    problems = [[] for _ in tasks]
    for n, v in enumerate(fleet):
        aircraft = v['aircraft']
        profile = profiles[aircraft.profile_id]
        for r, task in enumerate(tasks):
            for sid, sensor in aircraft_sensors(aircraft, catalog):
                if sensor is None:
                    problems[r].append(dict(kind='sensor_unknown', aircraft=aircraft.id, sensor=sid))
                    continue
                if sensor.sensor_type != task.survey_type:
                    continue
                if not sensor_usable(sensor, profile):
                    problems[r].append(dict(kind='incompatible', aircraft=aircraft.id, sensor=sensor.id, sensor_name=sensor.name, profile=profile.name))
                    continue
                altitude = task.altitude_m if task.altitude_m is not None else v['altitude']
                speed = min(v['speed'], task.max_speed_mps) if task.max_speed_mps is not None else v['speed']
                if v['plane'] and altitude < 100:
                    problems[r].append(dict(kind='plane_altitude', aircraft=aircraft.id, sensor=sensor.id, actual=altitude, required=100))
                    continue
                if v['plane'] and speed < 64 / 3.6:
                    problems[r].append(dict(kind='plane_speed', aircraft=aircraft.id, sensor=sensor.id, actual=speed, required=round(64 / 3.6, 1)))
                    continue
                if scenario.weather.wind_speed_mps >= speed:
                    problems[r].append(dict(kind='wind', aircraft=aircraft.id, sensor=sensor.id, actual=scenario.weather.wind_speed_mps, required=speed))
                    continue
                geometry, problem = survey_geometry(sensor, task, altitude, aircraft.trigger_interval_s)
                if problem:
                    problems[r].append(dict(problem, aircraft=aircraft.id, sensor=sensor.id, sensor_name=sensor.name))
                    continue
                key = (n, sensor.id, altitude, speed)
                if key not in by_key:
                    unit = dict(v, i=n, sensor_id=sensor.id, sensor_name=sensor.name, altitude=altitude, speed=speed, tasks={}, fixed_altitude=False)
                    if altitude != v['altitude'] or speed != v['speed']:
                        if v['plane']:
                            ops = operations(v['home'], settings, scenario.weather, speed, geo, altitude)
                            landings = []
                            for candidate, xy in v['landing_sites']:
                                recovery = ops if candidate is v['site'] else landing(xy, settings, scenario.weather, geo, ops['heading'], altitude, speed)
                                if recovery:
                                    landings.append(dict(site=candidate, xy=xy, **{k: recovery[k] for k in ('end_pose', 'release', 'fall_seconds', 'release_altitude_m',
                                                                                                       'descent', 'descent_turns', 'descent_seconds')}))
                            unit.update(ops=ops, landings=landings, overhead=ops['overhead'])
                        else:
                            unit.update(overhead=altitude + 40)
                    by_key[key] = len(units)
                    units.append(unit)
                    units_of[n].append(len(units) - 1)
                u = by_key[key]
                units[u]['tasks'][r] = geometry
                units[u]['fixed_altitude'] = units[u]['fixed_altitude'] or task.altitude_m is not None
                eligible[r].add(u)
    return units, units_of, eligible, problems
