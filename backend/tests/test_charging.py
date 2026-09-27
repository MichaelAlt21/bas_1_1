import pytest
from fastapi.testclient import TestClient

from bas.catalog.service import builtin_profiles
from bas.domain.models import Scenario, Site, TaskEstimateRequest
from bas.main import create_app
from bas.planning.estimate import task_estimate
from bas.planning.scheduling.conflicts import _charger_free
from bas.planning.service import plan
from test_planning import ready, rect


def charging(ready, battery=100, count=2, sorties=4, **settings):
    # About 170 ha: more than one sortie per aircraft, so both come back to charge.
    ready['survey_area'] = rect(37.300, 55.800, .03, .015)
    ready['allowed_airspace'] = rect(37.290, 55.793, .05, .03)
    ready['fleet'] = [dict(id=f'G{i}', profile_id='gemini', battery_percent=battery, payload='rgb', start_site_id='west') for i in range(count)]
    ready['settings'] = dict(sorties_per_aircraft=sorties, recharge_mode='charge', **settings)
    return ready


def test_default_chargers_by_site_kind():
    assert Site(id='a', name='A', kind='launch_landing', coordinates=[37, 55]).charger_count() == 2
    assert Site(id='b', name='B', kind='alternate', coordinates=[37, 55]).charger_count() == 0
    assert Site(id='c', name='C', kind='alternate', coordinates=[37, 55], chargers=3).charger_count() == 3


def test_charger_queue_search():
    assert _charger_free([], 1, 5., 10.) == 5.
    assert _charger_free([(0., 10.), (5., 15.)], 2, 0., 5.) == 0.
    assert _charger_free([(0., 10.), (5., 15.)], 1, 0., 5.) == 15.
    # A gap long enough between two bookings is used.
    assert _charger_free([(0., 10.), (30., 40.)], 1, 0., 15.) == 10.


def test_one_charger_is_shared_in_turn(ready):
    charging(ready)
    ready['sites'][0]['chargers'] = 1
    ready['sites'][1]['chargers'] = 0
    result = plan(Scenario.model_validate(ready))
    repeat = [m for m in result['missions'] if m['sortie'] > 1]
    assert repeat and all(m['charge_site_id'] == 'west' and m['site_id'] == 'west' for m in repeat)
    intervals = sorted((m['charge_start_s'], m['charge_end_s']) for m in repeat)
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(intervals, intervals[1:])), 'one charger charges one battery at a time'
    by_id = {(m['aircraft_id'], m['sortie']): m for m in result['missions']}
    for m in repeat:
        before = by_id[(m['aircraft_id'], m['sortie'] - 1)]
        assert m['charge_start_s'] >= before['finish_s'] + 300 - 1e-6 and m['start_s'] >= m['charge_end_s'] - 1e-6
        # Charging refills what the previous sortie used: 105 min per full battery for Gemini.
        used = (before['battery_start_percent'] - before['battery_end_percent'] + 100 - before['battery_start_percent']) / 100
        assert m['charge_s'] == pytest.approx(105 * 60 * used)
    assert result['metrics']['charger_wait_s'] > 0 and result['metrics']['charging_s'] > 0
    assert any(w.startswith('Зарядка между вылетами') for w in result['warnings'])


def test_charging_takes_longer_than_swapping(ready):
    swap = plan(Scenario.model_validate(dict(charging(ready), settings=dict(sorties_per_aircraft=4, recharge_mode='swap'))))
    charge = plan(Scenario.model_validate(charging(ready)))
    assert charge['metrics']['completion_time_s'] > swap['metrics']['completion_time_s'] + 3600


def test_reserve_site_with_chargers_is_a_charging_stop(ready):
    charging(ready, count=1, sorties=10)
    ready['sites'] = [dict(id='west', name='West', kind='launch_landing', coordinates=[37.297, 55.801], chargers=0),
                      dict(id='spare', name='Spare', kind='alternate', coordinates=[37.311, 55.804], chargers=1)]
    result = plan(Scenario.model_validate(ready))
    first, *repeat = sorted(result['missions'], key=lambda m: m['sortie'])
    assert first['site_id'] == 'west' and first['landing_site_id'] == 'spare'
    assert repeat and all(m['site_id'] == 'spare' and m['charge_site_id'] == 'spare' for m in repeat)
    assert result['metrics']['coverage_percent'] >= 99.9


def test_no_charger_anywhere_means_single_sorties(ready):
    charging(ready, count=2)
    ready['survey_area'] = rect(37.300, 55.800, .008, .005)
    for site in ready['sites']: site['chargers'] = 0
    result = plan(Scenario.model_validate(ready))
    assert all(m['sortie'] == 1 for m in result['missions'])
    assert any('нет доступного зарядного места' in w for w in result['warnings'])


def test_estimate_charging_needs_more_days():
    areas = [rect(37.30 + 0.05 * i, 55.80, .01, .01) for i in range(6)]
    gemini = {p.id: p for p in builtin_profiles()}['gemini']
    swap = task_estimate(TaskEstimateRequest(areas=areas, profile_id='gemini', fleet_size=1, workday_h=4), gemini)
    charge = task_estimate(TaskEstimateRequest(areas=areas, profile_id='gemini', fleet_size=1, workday_h=4, settings=dict(recharge_mode='charge')), gemini)
    assert charge['cycle_min'] > swap['cycle_min'] and charge['days_by_fleet'][0]['days'] >= swap['days_by_fleet'][0]['days']


def test_report_shows_charging(tmp_path, ready):
    charging(ready, count=2)
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")) as client:
        scenario_id = client.post('/api/scenarios', json=ready).json()['id']
        planned = client.post('/api/planning-runs', json=dict(scenario_id=scenario_id)).json()
        report = client.get(f"/api/plans/{planned['id']}/report").text
    assert 'зарядка' in report and 'Зарядных мест' in report
