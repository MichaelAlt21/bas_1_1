import random
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, shape

from bas.domain.models import Scenario
from bas.main import create_app
from bas.planning.airspace import airspace
from bas.planning.analysis import fleet_analysis
from bas.planning.estimate import task_estimate
from bas.planning.geometry import LocalGeometry, PlanningError
from bas.planning.service import EMPTY_SUM, accumulate, plan, summed
from bas.planning.sun import sun_times
from bas.catalog.service import builtin_profiles
from bas.domain.models import TaskEstimateRequest
from test_planning import ready, rect

MSK = timezone(timedelta(hours=3))


def zone(zone_id, polygon, lower=('GND', 0), upper=('UNL', 0), **extra):
    return dict(id=zone_id, name=zone_id, geometry=polygon, lower=dict(reference=lower[0], value=lower[1]),
                upper=dict(reference=upper[0], value=upper[1]), **extra)


def tower(obstacle_id, lon, lat, height, reference='AGL'):
    return dict(id=obstacle_id, kind='COMMUNICATION_TOWER', geometry=dict(type='Point', coordinates=[lon, lat]),
                radius_m=5, height_m=height, height_reference=reference)


def legs_avoid(result, scenario, geometry):
    for mission in result['missions']:
        for leg in mission['legs']:
            points = [geometry.forward(*p) for p in leg['coordinates']]
            path = Point(points[0]) if points[0] == points[-1] else LineString(points)
            assert geometry.free.buffer(.001).covers(path)


@pytest.mark.parametrize('lower,terrain,applied', [
    (('FL', 100), (150, 150), False),       # ~3048 m AMSL: far above the flight
    (('AMSL', 400), (150, 150), False),     # 250 m above ground > 100 m + 50 m buffer
    (('AMSL', 400), (150, 300), True),      # over the highest ground the floor is only 100 m up
    (('AGL', 120), (150, 150), True),       # below altitude + buffer
    (('AMSL', 200), (150, 150), True),
])
def test_zone_applies_by_altitude_band(ready, lower, terrain, applied):
    ready['restriction_zones'] = [zone('Z', rect(37.31, 55.80, .002, .002), lower=lower)]
    ready['settings'] = dict(terrain_min_m=terrain[0], terrain_max_m=terrain[1])
    blockers, report = airspace(Scenario.model_validate(ready))
    assert ('Z' in [b.ref for b in blockers]) is applied
    assert (report['zones_applied'] != []) is applied


def test_scheduled_zone_only_inside_operation_window(ready):
    weekly = dict(kind='scheduled', weekly=[dict(days=['MON'], start='08:00', end='10:00')])
    ready['restriction_zones'] = [zone('Z', rect(37.31, 55.80, .002, .002), validity=weekly)]
    # 2026-09-21 is a Monday.
    for start, applied in ((datetime(2026, 9, 21, 9, 0, tzinfo=MSK), True), (datetime(2026, 9, 22, 12, 0, tzinfo=MSK), False),
                           (datetime(2026, 9, 21, 7, 30, tzinfo=MSK), True)):
        ready['settings'] = dict(operation_start=start.isoformat(), time_limit_min=60)
        _, report = airspace(Scenario.model_validate(ready))
        assert bool(report['zones_applied']) is applied and bool(report['zones_inactive']) is not applied
    ready['settings'] = {}
    _, report = airspace(Scenario.model_validate(ready))
    assert report['zones_applied'] and report['zones_time_assumed'][0]['reason'] == 'start_unknown'


@pytest.mark.parametrize('height,reference,terrain_min,applied', [
    (40, 'AGL', 150, False), (60, 'AGL', 150, True), (250, 'AMSL', 150, True), (250, 'AMSL', 200, False)])
def test_obstacle_applies_by_height(ready, height, reference, terrain_min, applied):
    ready['obstacles'] = [tower('T', 37.31, 55.80, height, reference)]
    ready['settings'] = dict(terrain_min_m=terrain_min, terrain_max_m=max(terrain_min, 150))
    blockers, report = airspace(Scenario.model_validate(ready))
    assert ('T' in [b.ref for b in blockers]) is applied and report['obstacles_below'] == (0 if applied else 1)


def test_zone_between_site_and_area_is_flown_around(ready):
    ready['restriction_zones'] = [zone('Wall', rect(37.2985, 55.8003, .0008, .0025))]
    scenario = Scenario.model_validate(ready)
    result = plan(scenario)
    geometry = LocalGeometry(scenario)
    wall = shape(rect(37.2985, 55.8003, .0008, .0025))
    for mission in result['missions']:
        for leg in mission['legs']:
            assert not LineString(leg['coordinates'] * (2 if len(leg['coordinates']) == 1 else 1)).intersects(wall)
    legs_avoid(result, scenario, geometry)
    assert [z['id'] for z in result['airspace']['zones_applied']] == ['Wall']


def test_obstacle_inside_area_rejects_or_is_excluded(ready):
    ready['obstacles'] = [tower('UUT1', 37.304, 55.8025, 150)]
    with pytest.raises(PlanningError) as error:
        plan(Scenario.model_validate(ready))
    assert error.value.code == 'AREA_BLOCKED' and 'UUT1' in str(error.value)
    ready['settings'] = dict(blocked_area_policy='exclude')
    scenario = Scenario.model_validate(ready)
    result = plan(scenario)
    geometry = LocalGeometry(scenario)
    metrics = result['metrics']
    # Octagon around a 55 m radius (50 m clearance + 5 m footprint) is about 1 ha.
    assert 0.8e4 < metrics['excluded_area_m2'] < 1.2e4 and metrics['coverage_by_area'][0]['excluded_m2'] > 0
    assert metrics['coverage_percent'] > 97 and result['airspace']['excluded_by'][0]['id'] == 'UUT1'
    legs_avoid(result, scenario, geometry)
    assert any('исключено' in w for w in result['warnings'])


def test_pocket_cut_off_from_sites_is_reported(ready):
    # A ring of prohibited zones around the middle of the area leaves a pocket with no path from the sites.
    ring = [rect(37.302, 55.8015, .004, .0004), rect(37.302, 55.8031, .004, .0004), rect(37.302, 55.8015, .0006, .002), rect(37.3054, 55.8015, .0006, .002)]
    ready['restriction_zones'] = [zone(f'R{i}', r) for i, r in enumerate(ring)]
    ready['settings'] = dict(blocked_area_policy='exclude', boundary_clearance_m=5)
    scenario = Scenario.model_validate(ready)
    geometry = LocalGeometry(scenario)
    assert geometry.reachable.area < geometry.free.area and geometry.cut_off_m2 > 0
    result = plan(scenario)
    legs_avoid(result, scenario, geometry)


def test_repeat_sorties_follow_turnaround(ready):
    ready['fleet'] = [dict(id='G1', profile_id='gemini', battery_percent=40, payload='rgb', start_site_id='west')]
    with pytest.raises(PlanningError):
        plan(Scenario.model_validate(ready))
    ready['settings'] = dict(sorties_per_aircraft=3, turnaround_min=10)
    result = plan(Scenario.model_validate(ready))
    missions = sorted(result['missions'], key=lambda m: m['sortie'])
    assert [m['sortie'] for m in missions] == list(range(1, len(missions) + 1)) and len(missions) >= 2
    for before, after in zip(missions, missions[1:]):
        assert after['start_s'] >= before['finish_s'] + 600 - 1e-6
        assert after['budget_s'] > before['budget_s'] or before['sortie'] > 1
    assert result['metrics']['aircraft_used'] == 1 and result['metrics']['sortie_count'] == len(missions)
    assert result['metrics']['coverage_percent'] >= 99.9


def test_aircraft_limit_and_deadline_are_reported(ready):
    ready['settings'] = dict(max_aircraft=1, time_limit_min=1)
    result = plan(Scenario.model_validate(ready))
    constraints = result['constraints']
    assert constraints['aircraft_used'] == 1 and constraints['meets_time_limit'] is False
    assert any('не укладывается в срок' in w for w in result['warnings'])


def test_same_pad_launches_do_not_wait_for_whole_flights(ready):
    ready['fleet'] = [dict(id=f'G{i}', profile_id='gemini', battery_percent=100, payload='rgb', start_site_id='west') for i in range(3)]
    result = plan(Scenario.model_validate(ready))
    starts = sorted((m['start_s'], m['finish_s']) for m in result['missions'])
    assert len(starts) >= 2 and all(later[0] < earlier[1] for earlier, later in zip(starts, starts[1:]))


def test_maintenance_share(ready):
    result = plan(Scenario.model_validate(ready))
    shares = {m['aircraft_id']: m for m in result['metrics']['maintenance']}
    gemini = shares['Gemini-01']
    assert gemini['unit'] == 'flight_hours' and gemini['interval'] == 160
    assert gemini['interval_share'] == pytest.approx(gemini['flight_time_s'] / 3600 / 160)


def test_until_sunset_needs_start_and_limits_the_plan(ready):
    ready['settings'] = dict(until_sunset=True)
    with pytest.raises(ValueError):
        Scenario.model_validate(ready)
    ready['settings'] = dict(until_sunset=True, operation_start=datetime(2026, 9, 23, 18, 0, tzinfo=MSK).isoformat())
    result = plan(Scenario.model_validate(ready))
    constraints = result['constraints']
    assert constraints['time_limit_source'] == 'sunset' and 20 * 60 < constraints['time_limit_s'] < 30 * 60


def test_sun_times():
    moscow = sun_times(55.7558, 37.6173, date(2026, 6, 21))
    assert moscow['sunrise'].date() == date(2026, 6, 21) and moscow['sunrise'].astimezone(MSK).strftime('%H:%M') in ('03:43', '03:44', '03:45')
    assert moscow['sunset'].astimezone(MSK).strftime('%H:%M') in ('21:17', '21:18', '21:19')
    assert sun_times(69.0, 33.0, date(2026, 6, 21))['polar'] == 'day' and sun_times(69.0, 33.0, date(2026, 12, 21))['polar'] == 'night'


def test_running_sum_matches_builtin_sum():
    rng = random.Random(8)
    for _ in range(500):
        values = [rng.uniform(0, 500) * 10 ** rng.randint(-3, 3) for _ in range(rng.randint(0, 60))]
        cut = rng.randint(0, len(values))
        assert summed(accumulate(accumulate(EMPTY_SUM, values[:cut]), values[cut:])) == sum(values)


def test_fleet_analysis_answers_deadline(ready):
    ready['fleet'] = [dict(id=f'G{i}', profile_id='gemini', battery_percent=100, payload='rgb', start_site_id='west' if i % 2 else 'east') for i in range(4)]
    ready['survey_area'] = rect(37.30, 55.80, .012, .006)
    ready['allowed_airspace'] = rect(37.292, 55.796, .026, .016)
    ready['settings'] = dict(time_limit_min=30)
    result = fleet_analysis(Scenario.model_validate(ready))
    rows = [r for r in result['rows'] if r['feasible']]
    assert rows and result['deadline_s'] == 1800
    assert all(later['best_completion_s'] <= earlier['best_completion_s'] for earlier, later in zip(rows, rows[1:]))
    if result['aircraft_needed'] is not None:
        assert next(r for r in rows if r['aircraft'] == result['aircraft_needed'])['meets_deadline']


def test_task_estimate_packages():
    areas = [rect(37.30 + 0.05 * i, 55.80, .01, .01) for i in range(6)]
    request = TaskEstimateRequest(areas=areas, profile_id='gemini', fleet_size=5, cell_km=4)
    result = task_estimate(request, {p.id: p for p in builtin_profiles()}['gemini'])
    assert len(result['packages']) >= 3 and result['totals']['sorties'] >= len(result['packages'])
    assert result['totals']['area_ha'] == pytest.approx(sum(p['area_ha'] for p in result['packages']))
    assert result['days_by_fleet'][-1]['aircraft'] == 5 and all(p['reachable'] for p in result['packages'])


def test_new_endpoints(tmp_path, ready):
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'a.db').as_posix()}")) as client:
        sun = client.get('/api/sun', params=dict(latitude=55.75, longitude=37.62, date='2026-09-23')).json()
        assert sun['sunrise'] < sun['sunset'] and sun['civil_dusk'] > sun['sunset']
        estimate = client.post('/api/task-estimate', json=dict(areas=[rect(37.3, 55.8, .01, .01)], profile_id='gemini'))
        assert estimate.status_code == 200 and estimate.json()['packages']
        assert client.post('/api/task-estimate', json=dict(areas=[rect(37.3, 55.8, .01, .01)], profile_id='missing')).status_code == 422
        scenario_id = client.post('/api/scenarios', json=ready).json()['id']
        analysis = client.post(f'/api/scenarios/{scenario_id}/fleet-analysis')
        assert analysis.status_code == 200 and analysis.json()['rows']
        planned = client.post('/api/planning-runs', json=dict(scenario_id=scenario_id)).json()
        report = client.get(f"/api/plans/{planned['id']}/report")
        assert report.status_code == 200 and 'Расход ресурса до ТО' in report.text
