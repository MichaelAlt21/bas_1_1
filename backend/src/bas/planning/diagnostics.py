"""Structured diagnostics: why a plan is impossible, not found, or worth a warning.

Every diagnostic carries a stable code, a severity and one of six categories:

    input       — the scenario itself is incomplete or contradictory;
    equipment   — no aircraft and sensor can do a survey requirement as asked;
    constraint  — a stated constraint (airspace, reserve, wind, site) rules a plan out;
    search      — the heuristic found no plan within its rules: not a proof that none exists;
    budget      — the search stopped on its computing budget;
    internal    — a fault of the service.

Subjects name the affected areas, aircraft, sites, requirements, zones and obstacles. Actions
follow from the cause: `navigate` opens the place in the interface (tab, section, field, map
object); `apply` carries a patch the interface shows before applying and can undo. No action
relaxes quality, reserve or airspace limits on its own.
"""
from bas.planning.tasks import TYPE_NAMES, aircraft_sensors, sensor_catalog, sensor_usable, survey_tasks, task_label
from bas.catalog.sensors import survey_geometry

CATEGORY = {
    'FLEET_REQUIRED': 'input', 'FLEET_TOO_LARGE': 'input', 'PROFILE_UNKNOWN': 'input', 'SITE_REQUIRED': 'input',
    'SITE_ASSIGNMENT': 'input', 'AIRSPACE_REQUIRED': 'input', 'REGION_TOO_LARGE': 'input', 'GEOMETRY_TOO_COMPLEX': 'input',
    'WEATHER_STALE': 'input', 'WEATHER_LOCATION': 'input', 'SPEED_LIMIT': 'input', 'BATTERY_RESERVE': 'input',
    'REQUIREMENTS_EMPTY': 'input', 'COST_MODEL': 'input', 'INPUT_INVALID': 'input',
    'SURVEY_UNSUPPORTED': 'equipment', 'PAYLOAD_MISMATCH': 'equipment', 'CAMERA_REQUIRED': 'equipment', 'CAMERA_SENSOR_MISMATCH': 'equipment',
    'CAMERA_INCOMPATIBLE': 'equipment', 'GSD_LIMIT': 'equipment', 'GSD_NEEDS_CAMERA': 'equipment', 'EQUIPMENT_MISSING': 'equipment',
    'WIND_LIMIT': 'constraint', 'WIND_UNREACHABLE': 'constraint', 'FIXED_WING_ALTITUDE': 'constraint', 'SITE_BLOCKED': 'constraint',
    'SITE_CHARGING': 'constraint', 'FIXED_WING_SITE': 'constraint', 'AREA_BLOCKED': 'constraint', 'AREA_UNREACHABLE': 'constraint',
    'WIND_TRACK': 'constraint', 'CRAB': 'constraint', 'STRIP_BLOCKED': 'constraint', 'CAPTURE_RATE': 'constraint',
    'CAPACITY': 'search', 'NO_PLAN_FOUND': 'search', 'COVERAGE': 'search', 'CONFLICT': 'search', 'VALIDATION': 'search',
    'TOO_MANY_STRIPS': 'budget', 'SEARCH_BUDGET': 'budget', 'ANALYSIS_BUDGET': 'budget', 'INTERNAL': 'internal',
}
CATEGORY_NAMES = {'input': 'Ввод', 'equipment': 'Оборудование и требования', 'constraint': 'Ограничения',
                  'search': 'План не найден эвристикой', 'budget': 'Бюджет вычислений', 'internal': 'Ошибка сервиса'}


def target(tab, section=None, field=None, map_kind=None, map_id=None):
    result = dict(tab=tab)
    if section: result['section'] = section
    if field: result['field'] = field
    if map_kind: result['map'] = dict(kind=map_kind, id=map_id)
    return result


def navigate(label, *args, **kwargs):
    return dict(kind='navigate', label=label, target=target(*args, **kwargs))


def make(code, message, severity='error', subjects=None, actual=None, required=None, actions=(), title=None, place=None):
    subjects = {k: v for k, v in (subjects or {}).items() if v}
    return dict(code=code, severity=severity, category=CATEGORY.get(code, 'search'), category_name=CATEGORY_NAMES[CATEGORY.get(code, 'search')],
                title=title or message.split('.')[0][:120], message=message, subjects=subjects, actual=actual, required=required,
                actions=list(actions), target=place or (actions[0]['target'] if actions else None))


def _aircraft_actions(aircraft_id, field, label):
    return [navigate(label, 'fleet', 'fleet', f'aircraft:{aircraft_id}:{field}')]


def from_error(error, scenario=None):
    """Diagnostics for a PlanningError (its own, or one built from its code and details)."""
    if getattr(error, 'diagnostics', None):
        return list(error.diagnostics)
    code, message, details = error.code, str(error), getattr(error, 'details', {}) or {}
    aircraft = details.get('aircraft') or []
    sites = details.get('sites') or []
    requirements = details.get('requirements') or []
    subjects = dict(aircraft=aircraft, sites=sites, requirements=requirements, zones=details.get('blockers') or [])
    actions = []
    first = aircraft[0] if aircraft else None
    if code in ('SPEED_LIMIT',) and first:
        actions = _aircraft_actions(first, 'speed', f'Скорость {first}')
    elif code == 'BATTERY_RESERVE' and first:
        actions = _aircraft_actions(first, 'battery', f'Заряд {first}') + [navigate('Резерв заряда', 'settings', 'assumptions', 'settings.reserve_percent')]
    elif code == 'SITE_ASSIGNMENT' and first:
        actions = _aircraft_actions(first, 'site', f'Площадка {first}')
    elif code == 'FIXED_WING_ALTITUDE' and first:
        actions = _aircraft_actions(first, 'altitude', f'Высота {first}') + [navigate('Высота съемки', 'settings', 'survey', 'settings.altitude_m')]
    elif code in ('WIND_LIMIT', 'WIND_UNREACHABLE', 'WEATHER_STALE', 'WEATHER_LOCATION', 'WIND_TRACK'):
        actions = [navigate('Ветер', 'settings', 'weather', 'weather.wind_speed_mps')] + (_aircraft_actions(first, 'speed', f'Скорость {first}') if first else [])
    elif code == 'SITE_BLOCKED':
        actions = [navigate('Площадка на карте', 'scenario', 'sites', f'site:{sites[0]}:name' if sites else None, 'site', sites[0] if sites else None)]
        actions += [navigate('Отступ от границ', 'settings', 'assumptions', 'settings.boundary_clearance_m')]
    elif code == 'SITE_CHARGING':
        actions = [navigate('Зарядные места на площадках', 'scenario', 'sites')] + [navigate('Режим между вылетами', 'settings', 'sorties', 'settings.recharge_mode')]
    elif code == 'FIXED_WING_SITE':
        actions = [navigate('Курс запуска самолета', 'settings', 'survey', 'settings.fixed_wing.launch_heading_deg'),
                   navigate('Разрешенное пространство', 'scenario', 'airspace-drawn')]
    elif code in ('AREA_BLOCKED', 'AREA_UNREACHABLE'):
        actions = [navigate('Участки под ограничениями', 'settings', 'airspace', 'settings.blocked_area_policy'), navigate('Зоны съемки', 'scenario', 'areas')]
    elif code in ('SITE_REQUIRED',):
        actions = [navigate('Площадки', 'scenario', 'sites')]
    elif code in ('AIRSPACE_REQUIRED',):
        actions = [navigate('Разрешенное пространство', 'scenario', 'airspace-drawn')]
    elif code in ('FLEET_REQUIRED', 'FLEET_TOO_LARGE', 'PAYLOAD_MISMATCH', 'PROFILE_UNKNOWN'):
        actions = [navigate('Парк', 'fleet', 'fleet')]
    elif code in ('CAMERA_REQUIRED', 'CAMERA_SENSOR_MISMATCH', 'CAMERA_INCOMPATIBLE') and first:
        actions = _aircraft_actions(first, 'sensors', f'Оборудование {first}')
    elif code in ('GSD_LIMIT', 'GSD_NEEDS_CAMERA', 'SURVEY_UNSUPPORTED'):
        actions = [navigate('Высота съемки', 'settings', 'survey', 'settings.altitude_m'), navigate('Предел GSD', 'settings', 'survey', 'settings.max_gsd_cm')]
    elif code == 'CAPTURE_RATE' and first:
        actions = _aircraft_actions(first, 'speed', f'Скорость {first}') + [navigate('Продольное перекрытие', 'settings', 'survey', 'settings.front_overlap_percent')]
    elif code == 'CAPACITY':
        if details.get('kind') == 'sorties':
            actions = [navigate('Вылетов на борт', 'settings', 'sorties', 'settings.sorties_per_aircraft'), navigate('Парк', 'fleet', 'fleet')]
        else:
            actions = [navigate('Площадки ближе к району', 'scenario', 'sites'), navigate('Вылетов на борт', 'settings', 'sorties', 'settings.sorties_per_aircraft')]
        if details.get('limit'):
            actions.append(navigate('Ограничение числа бортов', 'settings', 'deadline', 'settings.max_aircraft'))
        if requirements:
            actions.append(navigate('Требование съемки', 'settings', 'requirements', f'requirement:{requirements[0]}:area_ids'))
    elif code in ('TOO_MANY_STRIPS', 'REGION_TOO_LARGE', 'GEOMETRY_TOO_COMPLEX'):
        actions = [navigate('Зоны съемки', 'scenario', 'areas')]
    elif code == 'NO_PLAN_FOUND':
        cause = details.get('cause')
        inner = from_error(cause, scenario) if cause is not None else []
        return [make(code, message, subjects=subjects, actions=[navigate('Зоны съемки', 'scenario', 'areas'), navigate('Парк', 'fleet', 'fleet')])] + \
               [dict(d, severity='info', title='Последняя причина: ' + d['title']) for d in inner]
    elif code in ('COVERAGE',) and requirements:
        actions = [navigate('Требование съемки', 'settings', 'requirements', f'requirement:{requirements[0]}:area_ids')]
    return [make(code, message, subjects=subjects, actual=details.get('actual'), required=details.get('required'), actions=actions)]


def equipment(scenario, profiles, task, problems):
    """Why no aircraft and sensor can do `task`, with the ways to provide one."""
    label = task_label(task)
    field = f'requirement:{task.id}'
    actions, reasons, actual, required = [], [], None, None
    catalog = sensor_catalog(scenario)
    gsd = [p for p in problems if p.get('kind') == 'gsd']
    unknown = [p for p in problems if p.get('kind') == 'geometry_unknown']
    spacing = [p for p in problems if p.get('kind') == 'line_spacing']
    if gsd:
        best = min(gsd, key=lambda p: p['actual'])
        actual, required = f"GSD {best['actual']:g} см/пикс на {best['altitude_m']:g} м", f"≤ {task.max_gsd_cm:g} см/пикс"
        reasons.append(f"лучший GSD {best['actual']:g} см/пикс ({best['aircraft']}, {best['sensor_name']}) грубее требуемого {task.max_gsd_cm:g}")
        actions.append(navigate(f"Высота требования: не выше {best['max_altitude_m']:g} м", 'settings', 'requirements', f'{field}:altitude_m'))
        actions.append(navigate('Предел GSD требования', 'settings', 'requirements', f'{field}:max_gsd_cm'))
    if unknown:
        reasons.append('геометрия сенсора ' + ', '.join(sorted({p['sensor_name'] for p in unknown})) + ' не подтверждена')
        actions.append(navigate('Ширина полосы в требовании', 'settings', 'requirements', f'{field}:swath_width_m'))
    if spacing:
        reasons.append('для магнитометра не задан шаг линий')
        actions.append(navigate('Шаг линий в требовании', 'settings', 'requirements', f'{field}:line_spacing_m'))
    for p in problems:
        if p.get('kind') == 'incompatible':
            reasons.append(f"{p['sensor_name']} не подтвержден для {p['profile']} ({p['aircraft']})")
        elif p.get('kind') in ('plane_altitude', 'plane_speed', 'wind'):
            reasons.append({'plane_altitude': f"{p['aircraft']}: самолету нужна высота ≥ 100 м",
                            'plane_speed': f"{p['aircraft']}: предел скорости требования ниже минимальной скорости самолета",
                            'wind': f"{p['aircraft']}: воздушная скорость не превышает ветер"}[p['kind']])
        elif p.get('kind') == 'sensor_unknown':
            reasons.append(f"{p['aircraft']}: профиль {p['sensor']} не найден в каталоге")
    # Which aircraft could carry a fitting sensor: same type, confirmed for its model.
    fitting = [s for s in catalog.values() if s.sensor_type == task.survey_type and not s.id.startswith('generic_')]
    for aircraft in scenario.fleet:
        owned = {sid for sid, _ in aircraft_sensors(aircraft, catalog)}
        for sensor in fitting:
            if sensor.id in owned or not sensor_usable(sensor, profiles[aircraft.profile_id]):
                continue
            geometry, problem = survey_geometry(sensor, task, task.altitude_m or aircraft.altitude_m or scenario.settings.altitude_m)
            if problem is None:
                actions.append(dict(kind='apply', label=f'Добавить {sensor.name} борту {aircraft.id}',
                                    description='Только если этот сенсор действительно есть у борта: сервис не проверяет комплектацию.',
                                    patch=dict(op='add_sensor', aircraft=aircraft.id, sensor=sensor.id),
                                    target=target('fleet', 'fleet', f'aircraft:{aircraft.id}:sensors')))
    if not any(a['kind'] == 'apply' for a in actions):
        actions.append(navigate('Профили сенсоров', 'fleet', 'sensors'))
    actions.append(navigate('Требование съемки', 'settings', 'requirements', f'requirement:{task.id}:survey_type'))
    if not problems and not reasons:
        reasons.append(f'ни у одного борта нет сенсора типа {TYPE_NAMES[task.survey_type]}')
    message = f'Требование «{label}»: нет борта с подходящим сенсором — ' + '; '.join(dict.fromkeys(reasons)) + '.'
    code = 'GSD_LIMIT' if gsd and len(gsd) == len([p for p in problems if p.get('kind') not in ('incompatible', 'sensor_unknown')]) else 'EQUIPMENT_MISSING'
    return make(code, message, subjects=dict(requirements=[task.id], areas=list(task.area_ids), aircraft=sorted({p['aircraft'] for p in problems if 'aircraft' in p})),
                actual=actual, required=required, actions=actions, title=f'Нет оборудования: {label}',
                place=target('settings', 'requirements', f'{field}:survey_type'))


def preflight(scenario, profiles):
    """Every input and equipment problem found before planning, all at once."""
    out = []
    s = scenario.settings
    if not scenario.fleet:
        out.append(make('FLEET_REQUIRED', 'Добавьте хотя бы один аппарат в парк.', actions=[navigate('Парк', 'fleet', 'fleet')]))
    if not any(site.kind == 'launch_landing' for site in scenario.sites):
        out.append(make('SITE_REQUIRED', 'Добавьте основную площадку старта и посадки.', actions=[navigate('Площадки', 'scenario', 'sites')]))
    if scenario.allowed_airspace is None:
        out.append(make('AIRSPACE_REQUIRED', 'Нарисуйте разрешенное пространство с местом для подходов и возвращения.',
                        actions=[navigate('Разрешенное пространство', 'scenario', 'airspace-drawn')]))
    main_sites = {site.id for site in scenario.sites if site.kind == 'launch_landing'}
    for aircraft in scenario.fleet:
        if aircraft.profile_id not in profiles:
            continue
        if aircraft.start_site_id not in main_sites:
            out.append(make('SITE_ASSIGNMENT', f'Назначьте основную площадку аппарату {aircraft.id}.', subjects=dict(aircraft=[aircraft.id]),
                            actions=_aircraft_actions(aircraft.id, 'site', f'Площадка {aircraft.id}')))
        if aircraft.battery_percent <= s.reserve_percent:
            out.append(make('BATTERY_RESERVE', f'Заряд {aircraft.id} ({aircraft.battery_percent:g} %) не превышает резерв {s.reserve_percent:g} %.',
                            subjects=dict(aircraft=[aircraft.id]), actual=f'{aircraft.battery_percent:g} %', required=f'> {s.reserve_percent:g} %',
                            actions=_aircraft_actions(aircraft.id, 'battery', f'Заряд {aircraft.id}')))
    if scenario.requirements is not None:
        if not scenario.requirements:
            out.append(make('REQUIREMENTS_EMPTY', 'Добавьте хотя бы одно требование съемки: зоны, вид съемки и параметры.',
                            actions=[navigate('Требования съемки', 'settings', 'requirements')]))
        catalog = sensor_catalog(scenario)
        for task in survey_tasks(scenario):
            problems, ok = [], False
            for aircraft in scenario.fleet:
                profile = profiles.get(aircraft.profile_id)
                if profile is None:
                    continue
                altitude = task.altitude_m or aircraft.altitude_m or s.altitude_m
                for sid, sensor in aircraft_sensors(aircraft, catalog):
                    if sensor is None:
                        problems.append(dict(kind='sensor_unknown', aircraft=aircraft.id, sensor=sid))
                        continue
                    if sensor.sensor_type != task.survey_type:
                        continue
                    if not sensor_usable(sensor, profile):
                        problems.append(dict(kind='incompatible', aircraft=aircraft.id, sensor=sensor.id, sensor_name=sensor.name, profile=profile.name))
                        continue
                    if profile.airframe == 'fixed_wing' and altitude < 100:
                        problems.append(dict(kind='plane_altitude', aircraft=aircraft.id, sensor=sensor.id))
                        continue
                    geometry, problem = survey_geometry(sensor, task, altitude)
                    if problem:
                        problems.append(dict(problem, aircraft=aircraft.id, sensor=sensor.id, sensor_name=sensor.name))
                        continue
                    ok = True
            if not ok:
                out.append(equipment(scenario, profiles, task, problems))
    return out


def warnings_of(plan, scenario):
    """Structured counterparts of the main plan warnings."""
    out = []
    constraints = plan.get('constraints') or {}
    if constraints.get('meets_time_limit') is False:
        out.append(make('DEADLINE', f"План не укладывается в срок: {constraints['completion_s'] / 60:.0f} из {constraints['time_limit_s'] / 60:.0f} мин.",
                        severity='warning', actual=f"{constraints['completion_s'] / 60:.0f} мин", required=f"≤ {constraints['time_limit_s'] / 60:.0f} мин",
                        actions=[navigate('Срок', 'settings', 'deadline', 'settings.time_limit_min'), navigate('Вылетов на борт', 'settings', 'sorties', 'settings.sorties_per_aircraft')]))
    for mission in plan['missions']:
        if (mission.get('alternate') or {}).get('reachable') is False:
            out.append(make('ALTERNATE_UNREACHABLE', f"{mission['aircraft_id']}: резервная площадка не гарантированно достижима.", severity='warning',
                            subjects=dict(aircraft=[mission['aircraft_id']]), actions=[navigate('Площадки', 'scenario', 'sites')]))
    for row in plan['metrics'].get('coverage_by_requirement') or []:
        if row.get('excluded_m2', 0) > 1:
            out.append(make('REQUIREMENT_EXCLUDED', f"«{row['label']}»: {row['excluded_m2'] / 1e4:.2f} га исключено под ограничениями.", severity='warning',
                            subjects=dict(requirements=[row['id']], areas=row['area_ids']),
                            actions=[navigate('Участки под ограничениями', 'settings', 'airspace', 'settings.blocked_area_policy')]))
    search = (plan.get('cost') or {}).get('search') or {}
    if search.get('budget_exhausted'):
        out.append(make('SEARCH_BUDGET', f"Поиск улучшений по стоимости остановлен по бюджету ({search['evaluations']} точных расчетов, "
                        f"{search['trials']} проверенных переносов): план допустим, но лучший мог не найтись.", severity='info'))
    for mission in plan['missions']:
        if (mission.get('survey') or {}).get('demonstration'):
            out.append(make('DEMO_SENSOR', f"{mission['aircraft_id']} летит с демонстрационным профилем {mission['survey'].get('sensor_name')}: параметры не подтверждены.",
                            severity='warning', subjects=dict(aircraft=[mission['aircraft_id']]), actions=[navigate('Профили сенсоров', 'fleet', 'sensors')]))
            break
    return out
