import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from bas.domain.models import Scenario
from bas.main import create_app
from bas.planning.flight_models.fixed_wing import landing
from bas.planning.geometry import LocalGeometry
from bas.planning.scheduling.conflicts import conflict
from bas.planning.service import plan


def rect(x, y, w, h):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]}


@pytest.fixture
def ready():
    return dict(schema_version='0.1', name='Landing test', survey_area=rect(37.30, 55.80, .008, .005),
        allowed_airspace=rect(37.294, 55.796, .02, .014), no_fly_zones=[rect(37.300, 55.797, .003, .001)],
        sites=[dict(id='west', name='West', kind='launch_landing', coordinates=[37.297, 55.801]),
               dict(id='east', name='East', kind='launch_landing', coordinates=[37.311, 55.804])],
        fleet=[dict(id='Gemini-01', profile_id='gemini', battery_percent=100, payload='rgb', start_site_id='west'),
               dict(id='801-01', profile_id='geoscan_801', battery_percent=90, payload='rgb', start_site_id='east')],
        weather=dict(wind_speed_mps=3, wind_from_deg=270), objective='min_completion_time')


def fixed_wing_sample():
    return json.loads((Path(__file__).parents[2] / 'contracts/scenario.fixedwing.example.json').read_text(encoding='utf-8'))


def with_policy(raw, policy, objective=None):
    raw = json.loads(json.dumps(raw))
    raw.setdefault('settings', {})['landing_policy'] = policy
    if objective: raw['objective'] = objective
    return raw


def assert_valid(raw, result):
    scenario = Scenario.model_validate(raw)
    geometry = LocalGeometry(scenario)
    sites = {s.id: s for s in scenario.sites}
    assert result['metrics']['coverage_percent'] >= 99.9
    timed = []
    for mission in result['missions']:
        assert mission['flight_time_s'] <= mission['budget_s'] + 1e-6
        legs = mission['legs']
        assert legs[0]['coordinates'][0] == pytest.approx(list(sites[mission['site_id']].coordinates))
        assert legs[-1]['coordinates'][-1] == pytest.approx(list(sites[mission['landing_site_id']].coordinates))
        for previous, leg in zip(legs, legs[1:]):
            assert leg['start'] == pytest.approx(previous['end'])
            assert leg['coordinates'][0] == pytest.approx(previous['coordinates'][-1])
        for leg in legs:
            a, b = [tuple(geometry.forward(*p)) for p in leg['coordinates']]
            assert geometry.free.buffer(.001).covers(LineString([a, b]))
        timed.append([dict(l, xy=[geometry.forward(*p) for p in l['coordinates']]) for l in legs])
    assert all(not conflict(a, b) for i, a in enumerate(timed) for b in timed[:i])


def test_default_policy_always_returns_to_the_launch_site(ready):
    result = plan(Scenario.model_validate(ready))
    assert all(m['landing_site_id'] == m['site_id'] for m in result['missions'])
    assert not any('Посадка разрешена' in w for w in result['warnings'])


@pytest.mark.parametrize('objective', ['min_completion_time', 'min_total_airtime'])
def test_best_main_site_plans_keep_every_physical_invariant(ready, objective):
    raw = with_policy(ready, 'best_main_site', objective)
    assert_valid(raw, plan(Scenario.model_validate(raw)))


def test_multirotor_lands_elsewhere_when_it_shortens_the_flight(ready):
    home = plan(Scenario.model_validate(with_policy(ready, 'return_to_start', 'min_total_airtime')))
    free = plan(Scenario.model_validate(with_policy(ready, 'best_main_site', 'min_total_airtime')))
    assert free['metrics']['total_airtime_s'] < home['metrics']['total_airtime_s']
    moved = [m for m in free['missions'] if m['landing_site_id'] != m['site_id']]
    assert moved
    assert any(m['aircraft_id'] in w and 'На другую площадку' in w for m in moved for w in free['warnings'])


@pytest.mark.parametrize('objective', ['min_completion_time', 'min_total_airtime'])
def test_plane_parachute_recovery_onto_another_site_is_valid(objective):
    raw = with_policy(fixed_wing_sample(), 'best_main_site', objective)
    result = plan(Scenario.model_validate(raw))
    assert_valid(raw, result)
    for mission in result['missions']:
        if mission['airframe'] == 'fixed_wing':
            assert [l['phase'] for l in mission['legs'][-2:]] == ['parachute', 'landing']


def test_plane_lands_at_the_copter_pad_for_minimum_airtime():
    raw = with_policy(fixed_wing_sample(), 'best_main_site', 'min_total_airtime')
    plane = next(m for m in plan(Scenario.model_validate(raw))['missions'] if m['airframe'] == 'fixed_wing')
    assert (plane['site_id'], plane['landing_site_id']) == ('plane-base', 'copter-base')


def test_main_site_outside_free_airspace_is_skipped_not_rejected(ready):
    # A third main site inside the no-fly rectangle: unusable, but no aircraft depends on it.
    ready['sites'].append(dict(id='blocked', name='Blocked', kind='launch_landing', coordinates=[37.3015, 55.7975]))
    raw = with_policy(ready, 'best_main_site', 'min_total_airtime')
    result = plan(Scenario.model_validate(raw))
    assert all(m['landing_site_id'] != 'blocked' for m in result['missions'])
    assert_valid(raw, result)


def test_parachute_landing_is_refused_when_the_drift_line_leaves_free_airspace():
    raw = fixed_wing_sample()
    scenario = Scenario.model_validate(raw)
    geo = LocalGeometry(scenario)
    edge = geo.free.bounds[2]
    # Wind from the west puts the release point 60 m west of the pad: fine for a pad near the
    # east boundary, outside the allowed area for a pad hugging the west boundary.
    inside = (edge - 5, (geo.free.bounds[1] + geo.free.bounds[3]) / 2)
    assert landing(inside, scenario.settings, scenario.weather, geo, 0.0, 100, 22) is not None
    outside_release = (geo.free.bounds[0] + 5, inside[1])
    assert landing(outside_release, scenario.settings, scenario.weather, geo, 0.0, 100, 22) is None


def test_report_and_export_show_the_landing_site(tmp_path, ready):
    raw = with_policy(ready, 'best_main_site', 'min_total_airtime')
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'landing.db').as_posix()}")) as client:
        sid = client.post('/api/scenarios', json=raw).json()['id']
        result = client.post('/api/planning-runs', json={'scenario_id': sid}).json()
        moved = next(m for m in result['missions'] if m['landing_site_id'] != m['site_id'])
        report = client.get(f"/api/plans/{result['id']}/report").text
        assert '<th scope="col">Посадка</th>' in report
        assert 'На любую основную площадку' in report
        exported = client.get(f"/api/plans/{result['id']}/export", params={'aircraft_id': moved['aircraft_id']}).json()
        assert {f['properties'].get('landing_site_id') for f in exported['features'] if f['geometry']['type'] == 'LineString'} == {moved['landing_site_id']}
