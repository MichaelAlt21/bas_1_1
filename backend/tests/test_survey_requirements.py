"""Version 1.2: survey requirements, sensor profiles, diagnostics."""
import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, shape
from shapely.ops import unary_union

from bas.domain.migration import upgrade
from bas.domain.models import Scenario
from bas.main import create_app
from bas.planning import service
from bas.planning.geometry import LocalGeometry, PlanningError
from bas.planning.diagnostics import from_error

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark import fingerprint  # noqa: E402

CONTRACTS = ROOT / 'contracts'


def rect(x, y, w, h):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]}


def demo(**changes):
    data = json.loads((CONTRACTS / 'scenario.multisurvey.example.json').read_text(encoding='utf-8'))
    data.update(changes)
    return data


def without(data, *ids):
    """The scenario without some aircraft, their cost coefficients removed as the interface does."""
    data = copy.deepcopy(data)
    data['fleet'] = [a for a in data['fleet'] if a['id'] not in ids]
    cost = data.get('cost_model') or {}
    cost['diagonal'] = {k: v for k, v in cost.get('diagonal', {}).items() if k not in ids}
    cost['pairs'] = [p for p in cost.get('pairs', []) if p['a'] not in ids and p['b'] not in ids]
    return data


def plan_of(data):
    return service.plan(Scenario.model_validate(data))


# --- compatibility

@pytest.mark.parametrize('path', sorted(p.name for p in CONTRACTS.glob('scenario*.json') if 'multisurvey' not in p.name))
@pytest.mark.parametrize('objective', ['min_completion_time', 'min_total_airtime'])
def test_converted_scenarios_plan_exactly_as_before(path, objective):
    data = json.loads((CONTRACTS / path).read_text(encoding='utf-8'))
    data['objective'] = objective
    assert fingerprint(plan_of(data)) == fingerprint(plan_of(upgrade(data)))


def test_conversion_keeps_settings_and_cameras():
    data = upgrade(json.loads((CONTRACTS / 'scenario.fixedwing.example.json').read_text(encoding='utf-8')))
    (requirement,) = data['requirements']
    assert requirement['survey_type'] == 'rgb' and requirement['area_ids'] == data['area_ids']
    assert [a['sensors'] for a in data['fleet']] == [[a['camera_id']] for a in data['fleet']]
    manual = upgrade(json.loads((CONTRACTS / 'scenario.example.json').read_text(encoding='utf-8')))
    assert manual['requirements'][0]['swath_width_m'] == 100 and manual['fleet'][0]['sensors'] == ['generic_rgb']


# --- several requirements

def test_every_requirement_is_covered_by_its_own_survey_type():
    data = demo()
    result = plan_of(data)
    rows = {r['id']: r for r in result['metrics']['coverage_by_requirement']}
    assert set(rows) == {'field-rgb', 'field-ms', 'plant-ir'}
    assert all(r['coverage_percent'] >= 99.9 for r in rows.values())
    scenario = Scenario.model_validate(data)
    geo = LocalGeometry(scenario)
    kinds = {r['id']: r['survey_type'] for r in data['requirements']}
    sensors = {s: t for s, t in (('geoscan_pf1b_v1', 'rgb'), ('geoscan_pollux_v1', 'multispectral'), ('geoscan_801_thermal_v1', 'thermal'))}
    for mission in result['missions']:
        # A sortie carries one sensor, and serves only requirements of its type.
        assert {kinds[r] for r in mission['requirement_ids']} == {sensors[mission['sensor_id']]}
        for leg in mission['legs']:
            if leg['phase'] == 'survey':
                assert kinds[leg['requirement_id']] == sensors[mission['sensor_id']]
    # Thermal coverage counts thermal legs only: the RGB sorties over the field do not cover the plant.
    thermal = [l for m in result['missions'] for l in m['legs'] if l.get('requirement_id') == 'plant-ir']
    assert thermal and all(m['sensor_id'] == 'geoscan_801_thermal_v1' for m in result['missions'] if 'plant-ir' in m['requirement_ids'])
    assert result['metrics']['coverage_percent'] >= 99.9


def test_areas_of_different_survey_types_are_not_added_up():
    data = demo()
    result = plan_of(data)
    geo = LocalGeometry(Scenario.model_validate(data))
    assert result['metrics']['area_m2'] == pytest.approx(geo.area.area)
    rows = result['metrics']['coverage_by_requirement']
    assert sum(r['area_m2'] for r in rows) > result['metrics']['area_m2']


def test_one_aircraft_flies_two_sensors_in_different_sorties_after_a_payload_change():
    result = plan_of(demo())
    flights = sorted((m for m in result['missions'] if m['aircraft_id'] == 'Gemini-01'), key=lambda m: m['sortie'])
    assert len({m['sensor_id'] for m in flights}) == 2
    changed = [m for m in flights[1:] if m['sensor_id'] != flights[flights.index(m) - 1]['sensor_id']]
    assert changed and all(m['change_s'] == pytest.approx(15 * 60) for m in changed)
    first, second = flights[0], next(m for m in flights if m.get('change_s'))
    assert second['start_s'] >= result['missions'][result['missions'].index(first)]['finish_s'] + 15 * 60 - 1e-6


def test_all_three_criteria_plan_the_same_requirements():
    for objective in ('min_completion_time', 'min_total_airtime', 'min_cost'):
        result = plan_of(demo(objective=objective))
        assert all(r['coverage_percent'] >= 99.9 for r in result['metrics']['coverage_by_requirement'])
        assert all(m['flight_time_s'] <= m['budget_s'] + 1e-6 for m in result['missions'])


# --- diagnostics

def failure(data):
    with pytest.raises(PlanningError) as error:
        plan_of(data)
    error.value.diagnostics = from_error(error.value)
    return error.value


def test_missing_sensor_names_the_requirement_and_how_to_provide_it():
    error = failure(without(demo(), '801-01'))
    (diagnostic,) = [d for d in error.diagnostics if d['code'] == 'EQUIPMENT_MISSING']
    assert diagnostic['category'] == 'equipment' and diagnostic['subjects']['requirements'] == ['plant-ir']
    assert 'ИК' in diagnostic['message']
    # No Gemini can carry the 801 thermal camera: no "add the sensor" patch is offered, only the profiles.
    assert not any(a['kind'] == 'apply' for a in diagnostic['actions'])
    assert diagnostic['target']['field'] == 'requirement:plant-ir:survey_type'


def test_a_compatible_sensor_another_aircraft_lacks_is_offered_as_an_explicit_patch():
    error = failure(without(demo(), 'Gemini-01', 'Gemini-03'))
    (diagnostic,) = [d for d in error.diagnostics if 'field-ms' in d['subjects'].get('requirements', [])]
    patches = [a for a in diagnostic['actions'] if a['kind'] == 'apply']
    assert patches and patches[0]['patch'] == dict(op='add_sensor', aircraft='Gemini-02', sensor='geoscan_pollux_v1')
    assert 'действительно есть' in patches[0]['description']


def test_incompatible_sensor_is_reported_not_used():
    data = demo()
    for aircraft in data['fleet']:
        if aircraft['id'] == '801-01':
            aircraft['sensors'] = ['geoscan_801_thermal_v1', 'geoscan_pf1b_v1']
    error = failure(without(data, 'Gemini-01', 'Gemini-02'))
    rgb = next(d for d in error.diagnostics if 'field-rgb' in d['subjects'].get('requirements', []))
    assert 'не подтвержден' in rgb['message'] and '801' in rgb['message']


def test_quality_conflict_shows_actual_required_and_the_altitude_that_fits():
    data = demo()
    data['requirements'][0]['max_gsd_cm'] = 1.0
    error = failure(data)
    gsd = next(d for d in error.diagnostics if 'field-rgb' in d['subjects'].get('requirements', []))
    assert gsd['code'] == 'GSD_LIMIT' and gsd['required'] == '≤ 1 см/пикс' and '1.96' in gsd['actual']
    assert any('не выше 51' in a['label'] for a in gsd['actions'])


def test_unknown_geometry_asks_for_a_swath_not_more_aircraft():
    data = demo()
    del data['requirements'][2]['swath_width_m']
    error = failure(data)
    thermal = next(d for d in error.diagnostics if 'plant-ir' in d['subjects'].get('requirements', []))
    assert 'не подтверждена' in thermal['message']
    assert any(a['target'].get('field') == 'requirement:plant-ir:swath_width_m' for a in thermal['actions'])
    assert not any('Парк' == a['label'] for a in thermal['actions'])


def test_preflight_reports_every_input_problem_at_once():
    data = demo()
    data['fleet'][0]['start_site_id'] = None
    data['fleet'][1]['battery_percent'] = 10
    error = failure(data)
    codes = [d['code'] for d in error.diagnostics]
    assert 'SITE_ASSIGNMENT' in codes and 'BATTERY_RESERVE' in codes
    battery = next(d for d in error.diagnostics if d['code'] == 'BATTERY_RESERVE')
    assert battery['target'] == {'tab': 'fleet', 'section': 'fleet', 'field': 'aircraft:Gemini-02:battery'}


def test_heuristic_failure_is_not_called_impossible():
    data = without(demo(), 'Gemini-02', 'Gemini-03')
    data['settings']['sorties_per_aircraft'] = 1
    data['fleet'][0]['battery_percent'] = 40
    error = failure(data)
    first = error.diagnostics[0]
    assert first['category'] == 'search' and first['category_name'] == 'План не найден эвристикой'
    assert any('Вылетов на борт' in a['label'] for d in error.diagnostics for a in d['actions'])


# --- sensor profiles and the API

PROFILE = dict(name='Тепловизор пользователя', sensor_type='thermal', geometry='frame', sensor_width_mm=10.88, sensor_height_mm=8.7,
               focal_length_mm=13, image_width_px=640, image_height_px=512, compatible_profiles=['geoscan_801'],
               parameters={'sensor_width_mm': {'status': 'computed', 'formula': '17 мкм × 640'}}, source='Паспорт заказчика', checked_at='2026-10-17')


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'sensors.db').as_posix()}")) as c:
        yield c


def test_user_profiles_are_versioned_and_built_in_ones_are_protected(client):
    created = client.post('/api/catalog/sensors', json=PROFILE).json()
    assert created['id'] == 'user_teplovizor_polzovatelya' and created['version'] == 1
    changed = client.put(f"/api/catalog/sensors/{created['id']}", json=dict(PROFILE, focal_length_mm=19)).json()
    assert changed['version'] == 2
    assert [v['version'] for v in client.get(f"/api/catalog/sensors/{created['id']}/versions").json()] == [1, 2]
    assert client.put('/api/catalog/sensors/geoscan_pf1b_v1', json=PROFILE).status_code == 409
    assert client.post('/api/catalog/sensors?id=geoscan_pf1b_v1', json=PROFILE).status_code == 409
    bad = client.post('/api/catalog/sensors', json=dict(PROFILE, focal_length_mm=None))
    assert bad.status_code == 422 and 'фокусное' in json.dumps(bad.json(), ensure_ascii=False)


def test_import_never_overwrites_silently(client):
    created = client.post('/api/catalog/sensors', json=PROFILE).json()
    document = client.get(f"/api/catalog/sensors/export?ids={created['id']}").json()
    assert document['format'] == 'kumo-sensor-profiles' and document['format_version'] == 1
    assert client.post('/api/catalog/sensors/import', json=document).json()['unchanged'] == [created['id']]
    document['profiles'][0]['focal_length_mm'] = 25
    conflict = client.post('/api/catalog/sensors/import', json=document)
    assert conflict.status_code == 409 and conflict.json()['detail']['conflicts'][0]['id'] == created['id']
    assert client.get(f"/api/catalog/sensors/{created['id']}/versions").json()[-1]['focal_length_mm'] == 13
    assert client.post('/api/catalog/sensors/import?on_conflict=new_version', json=document).json()['new_versions'] == [dict(id=created['id'], version=2)]
    renamed = client.post('/api/catalog/sensors/import?on_conflict=rename', json=dict(document, profiles=[dict(document['profiles'][0], focal_length_mm=30)])).json()
    assert renamed['renamed'][0]['new_id'] == created['id'] + '_2'
    builtin = dict(document, profiles=[dict(document['profiles'][0], id='geoscan_pf1b_v1')])
    assert client.post('/api/catalog/sensors/import?on_conflict=new_version', json=builtin).status_code == 409
    assert client.post('/api/catalog/sensors/import', json={'format': 'other'}).status_code == 422


def test_a_saved_scenario_keeps_the_profile_version_it_was_planned_with(client):
    created = client.post('/api/catalog/sensors', json=PROFILE).json()
    data = demo()
    data['fleet'][2]['sensors'] = [created['id']]
    del data['requirements'][2]['swath_width_m']
    stored = client.post('/api/scenarios', json=data).json()
    assert {p['id']: p['version'] for p in stored['scenario']['sensor_profiles']}[created['id']] == 1
    first = client.post('/api/planning-runs', json={'scenario_id': stored['id']}).json()
    client.put(f"/api/catalog/sensors/{created['id']}", json=dict(PROFILE, focal_length_mm=40))
    again = client.post('/api/planning-runs', json={'scenario_id': stored['id']}).json()
    assert fingerprint(first) == fingerprint(again)
    thermal = next(m for m in again['missions'] if m['sensor_id'] == created['id'])
    assert thermal['survey']['camera']['version'] == 1


def test_api_returns_structured_diagnostics(client):
    data = without(demo(), '801-01')
    stored = client.post('/api/scenarios', json=data).json()
    response = client.post('/api/planning-runs', json={'scenario_id': stored['id']})
    assert response.status_code == 422
    detail = response.json()['detail']
    assert detail['code'] == 'EQUIPMENT_MISSING' and detail['diagnostics'][0]['category'] == 'equipment'
    invalid = dict(data, cost_model=dict(alpha_per_h=60, default_diagonal=1, pairs=[dict(a='Gemini-01', b='Gemini-02', q=5)]))
    response = client.post('/api/scenarios', json=invalid)
    assert response.status_code == 422
    diagnostic = response.json()['diagnostics'][0]
    assert diagnostic['code'] == 'INPUT_INVALID' and diagnostic['target'] == {'tab': 'settings', 'section': 'cost', 'field': 'cost'}


def test_report_and_export_name_sensors_requirements_and_cost(client):
    stored = client.post('/api/scenarios', json=demo(objective='min_cost')).json()
    result = client.post('/api/planning-runs', json={'scenario_id': stored['id']}).json()
    report = client.get(f"/api/plans/{result['id']}/report").text
    assert 'Требования съемки' in report and 'Стоимость J' in report and 'у.е.' in report and 'Geoscan Pollux' in report
    kml = client.get(f"/api/plans/{result['id']}/export?format=kml").text
    assert 'plant-ir' in kml
    exported = client.get(f"/api/plans/{result['id']}/export").json()
    assert {f['properties'].get('requirement_id') for f in exported['features'] if f['properties'].get('phase') == 'survey'} == {'field-rgb', 'field-ms', 'plant-ir'}


def test_plans_saved_before_version_1_2_still_open_and_print(tmp_path):
    from bas.infrastructure import database
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    with TestClient(create_app(url)) as client:
        stored = client.post('/api/scenarios', json=json.loads((CONTRACTS / 'scenario.example.json').read_text(encoding='utf-8'))).json()
        result = client.post('/api/planning-runs', json={'scenario_id': stored['id']}).json()
        old = copy.deepcopy(result)
        for key in ('cost', 'diagnostics', 'echelons', 'separation', 'scenario_id', 'created_at'):
            old.pop(key, None)
        old['metrics'].pop('coverage_by_requirement', None)
        for mission in old['missions']:
            for key in ('sensor_id', 'sensor_name', 'requirement_ids', 'survey_types', 'altitude_m', 'fixed_altitude'):
                mission.pop(key, None)
            for leg in mission['legs']:
                leg.pop('requirement_id', None)
        old['id'] = '11111111-2222-3333-4444-555555555555'
        database.save_plan(database.make_engine(url), stored['id'], old)
        assert client.get(f"/api/plans/{old['id']}").json()['missions'][0]['aircraft_id'] == result['missions'][0]['aircraft_id']
        report = client.get(f"/api/plans/{old['id']}/report")
        assert report.status_code == 200 and 'Стоимость J' not in report.text
        assert client.get(f"/api/plans/{old['id']}/export?format=kml").status_code == 200


def test_validation_messages_are_in_russian_and_lead_to_the_field(client):
    profile = dict(name='Тепловизор', sensor_type='thermal', geometry='scanner', compatible_profiles=[], source='')
    response = client.post('/api/catalog/sensors', json=profile)
    assert response.status_code == 422
    assert response.json()['detail'][0]['msg'] == 'не может быть пустым'
    response = client.post('/api/catalog/sensors', json=dict(profile, source='допущение оператора'))
    assert response.status_code == 422 and 'угол обзора' in response.json()['detail'][0]['msg']
    assert not response.json()['detail'][0]['msg'].startswith('Value error')


def test_cost_preset_is_served_with_sources(client):
    preset = client.get('/api/catalog/cost-preset').json()
    assert preset['alpha_per_h'] == 2491 and preset['by_profile']['gemini']['airtime'] == 1890
    assert preset['by_profile']['geoscan_801']['inputs']['price']['status'] == 'assumed'
