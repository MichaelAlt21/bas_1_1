"""Version 1.1: echelons and separation by height, the fixed-wing model in wind."""
import copy
import json
import math
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from bas.catalog.cameras import crabbed_width
from bas.domain.models import Scenario
from bas.main import create_app
from bas.planning.flight_models import dubins
from bas.planning.flight_models.multirotor import duration
from bas.planning.geometry import LocalGeometry
from bas.planning.scheduling.conflicts import conflict, first_conflict, minimal_shift, schedule
from bas.planning.service import plan

CONTRACTS = Path(__file__).parents[2] / 'contracts'


def rect(x, y, w, h):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]}


def crossing(**settings):
    """Pads on two corners, areas on the other two: four aircraft from each pad share one long corridor."""
    return dict(schema_version='0.1', name='Общие коридоры', survey_area=rect(37.300, 55.830, .012, .006),
                additional_survey_areas=[rect(37.360, 55.790, .012, .006)], allowed_airspace=rect(37.290, 55.780, .095, .060),
                sites=[dict(id='A', name='A', kind='launch_landing', coordinates=[37.302, 55.792]),
                       dict(id='B', name='B', kind='launch_landing', coordinates=[37.370, 55.834])],
                fleet=[dict(id=f'G{i:02d}', profile_id='gemini', battery_percent=100, payload='rgb', start_site_id='A' if i % 2 else 'B') for i in range(1, 9)],
                weather=dict(wind_speed_mps=3, wind_from_deg=270), survey_type='rgb', objective='min_completion_time',
                settings=dict(altitude_m=100, swath_width_m=100, **settings))


def fixed_wing(**changes):
    data = json.loads((CONTRACTS / 'scenario.fixedwing.example.json').read_text(encoding='utf-8'))
    data['fleet'] = data['fleet'][:1]
    data['settings']['fixed_wing'].update(changes)
    return data


def local_missions(scenario, result):
    geo = LocalGeometry(scenario)
    return geo, [[dict(leg, xy=[geo.forward(*p) for p in leg['coordinates']]) for leg in m['legs']] for m in result['missions']]


def assert_valid(data, result, vertical):
    scenario = Scenario.model_validate(data)
    geo, legs = local_missions(scenario, result)
    assert result['metrics']['coverage_percent'] >= 99.9
    for mission, local in zip(result['missions'], legs):
        assert mission['flight_time_s'] <= mission['budget_s'] + 1e-6
        for previous, leg in zip(local, local[1:]):
            assert leg['start'] == pytest.approx(previous['end'])
            assert leg['altitude_start_m'] == pytest.approx(previous['altitude_end_m'])
        for leg in local:
            assert geo.free.buffer(.001).covers(LineString(leg['xy']))
        cruise = {leg['altitude_start_m'] for leg in local if leg['phase'] in ('transit', 'survey', 'hold')}
        assert cruise == {mission['altitude_m']}
    assert not any(conflict(a, b, 30., vertical) for i, a in enumerate(legs) for b in legs[:i])


# --- Dubins paths in wind

def test_every_dubins_word_ends_exactly_at_the_target():
    random.seed(7)
    for _ in range(500):
        start = (random.uniform(-800, 800), random.uniform(-800, 800), random.uniform(-4, 4))
        finish = (random.uniform(-800, 800), random.uniform(-800, 800), random.uniform(-4, 4))
        for word, segments in dubins.words(start, finish, 150).items():
            pose = start
            for kind, amount in zip(word, segments):
                pose = dubins._advance(pose, kind, amount * (150 if kind == 'S' else 1), 150)
            assert math.dist(pose[:2], finish[:2]) < 1e-6
            assert abs((pose[2] - finish[2] + math.pi) % (2 * math.pi) - math.pi) < 1e-9


def test_loops_are_shorter_than_any_arc_straight_arc_to_the_neighbouring_strip():
    # The next strip 72 m away, flown the other way: closer than the turn diameter.
    found = dubins.words((0, 0, 0), (0, 72, math.pi), 150)
    csc = min(sum(v) for k, v in found.items() if 'S' in k)
    loop = min(sum(v) for k, v in found.items() if 'S' not in k)
    assert loop < csc * .7


def test_turns_in_wind_are_timed_like_the_rest_of_the_route():
    class Weather:
        wind_speed_mps, wind_from_deg = 8, 250
    wind = dubins.wind_vector(Weather)
    random.seed(3)
    for _ in range(40):
        start = (random.uniform(-800, 800), random.uniform(-800, 800), random.uniform(-4, 4))
        finish = (random.uniform(-800, 800), random.uniform(-800, 800), random.uniform(-4, 4))
        for arrival, parts in dubins.paths(start, finish, 150, 22, wind):
            chords = sum(duration(a[:2], b[:2], 22, 8, 250) for _, points in parts for a, b in zip(points, points[1:]))
            assert chords == pytest.approx(arrival, rel=2e-3)
            assert parts[0][1][0][:2] == pytest.approx(start[:2]) and parts[-1][1][-1][:2] == pytest.approx(finish[:2])


def test_crab_angle_keeps_the_ground_track():
    wind = (0., 9.)
    heading = dubins.air_heading(0., wind, 22)
    ground = (22 * math.cos(heading) + wind[0], 22 * math.sin(heading) + wind[1])
    assert ground[1] == pytest.approx(0, abs=1e-9) and ground[0] > 0
    assert math.degrees(abs(dubins.crab_angle(0., wind, 22))) == pytest.approx(24.1, abs=.1)


def test_crabbed_frames_cover_less_only_when_exposures_are_sparse():
    assert crabbed_width(100, 70, 21, 0.) == 100
    # With 70% front overlap turned frames still sweep the nominal band...
    assert crabbed_width(100, 70, 21, math.radians(20)) >= 100
    # ...with 20% they no longer meet at the edges.
    assert crabbed_width(100, 70, 56, math.radians(20)) < 100
    # Exposures farther apart than the frame never meet, turned or not.
    assert crabbed_width(100, 70, 80, 0.) == 0
    assert crabbed_width(100, 70, 100, math.radians(40)) == 0


# --- separation by height

def leg(xy0, xy1, t0, t1, h0, h1, phase='transit'):
    return dict(phase=phase, xy=[xy0, xy1], start=t0, end=t1, altitude_start_m=h0, altitude_end_m=h1)


def test_crossing_routes_twenty_metres_apart_in_height_do_not_clash():
    a = [leg((-500, 0), (500, 0), 0, 100, 100, 100)]
    b = [leg((0, -500), (0, 500), 0, 100, 120, 120)]
    assert conflict(a, b)
    assert not conflict(a, b, 30., 20.)
    assert conflict(a, [leg((0, -500), (0, 500), 0, 100, 110, 110)], 30., 20.)


def test_climb_over_a_shared_pad_clears_the_pad_once_twenty_metres_up():
    climbing = [leg((0, 0), (0, 0), 0, 50, 0, 100, 'climb')]
    waiting = dict(leg((0, 0), (0, 0), 0, 20, 0, 0, 'takeoff'))
    assert first_conflict([waiting], climbing, 30., 20.)
    assert minimal_shift(waiting, climbing[0], 30., 20.) == pytest.approx(10, abs=.02)


def test_schedule_with_height_separation_launches_a_shared_pad_faster():
    def sortie():
        return dict(legs=[leg((0, 0), (0, 0), 0, 20, 0, 0, 'takeoff'), leg((0, 0), (0, 0), 20, 70, 0, 100, 'climb'),
                          leg((0, 0), (900, 0), 70, 160, 100, 100)])
    horizontal = schedule([sortie(), sortie()])
    by_height = schedule([sortie(), sortie()], vertical=20.)
    assert by_height[1]['start_s'] < horizontal[1]['start_s']
    assert by_height[1]['start_s'] == pytest.approx(30, abs=.1)


# --- echelons in the planner

def test_single_level_keeps_the_historical_horizontal_rule():
    result = plan(Scenario.model_validate(json.loads((CONTRACTS / 'scenario.example.json').read_text(encoding='utf-8'))))
    assert result['separation']['vertical_m'] is None and result['echelons'] is None
    assert {m['altitude_m'] for m in result['missions']} == {100}


def test_automatic_echelons_split_crossing_routes():
    data = crossing(echelons=True)
    result = plan(Scenario.model_validate(data))
    report = result['echelons']
    assert report['applied'] and report['levels'] == [100, 120, 140]
    assert report['shared_same_level_after_m'] < report['shared_same_level_before_m'] / 10
    assert len({m['altitude_m'] for m in result['missions']}) > 1
    assert result['metrics']['completion_time_s'] <= report['single_level']['completion_s'] * 1.05
    assert_valid(data, result, 20.)
    assert any('Высоты полета' in w for w in result['warnings'])


def test_echelons_shorten_a_plan_that_waits_at_crossings():
    single = plan(Scenario.model_validate(crossing()))
    levels = plan(Scenario.model_validate(crossing(echelons=True)))
    assert levels['metrics']['completion_time_s'] < single['metrics']['completion_time_s'] * .6


def test_manual_levels_are_flown_and_separated_by_height():
    data = crossing()
    data['fleet'][0]['altitude_m'] = 140
    data['settings'].update(coverage_mode='camera')
    for a in data['fleet']:
        a['camera_id'] = 'geoscan_pf1b_v1'
    result = plan(Scenario.model_validate(data))
    assert result['separation']['vertical_m'] == 20
    high = [m for m in result['missions'] if m['aircraft_id'] == 'G01']
    assert high and all(m['altitude_m'] == 140 for m in high)
    assert high[0]['survey']['gsd_cm'] == pytest.approx(1.9583333 * 1.4, rel=1e-4)
    assert result['airspace']['flight_altitude_max_m'] == 140
    assert_valid(data, result, 20.)


def test_echelons_stay_below_the_ceiling_and_restriction_zones():
    data = crossing(echelons=True, altitude_ceiling_m=130)
    assert plan(Scenario.model_validate(data))['echelons']['levels'] == [100, 120]
    data = crossing(echelons=True)
    zone = dict(id='z', name='Зона выше', geometry=rect(37.33, 55.80, .01, .01), lower=dict(reference='AGL', value=175))
    data['restriction_zones'] = [zone]
    result = plan(Scenario.model_validate(data))
    assert result['echelons']['levels'] == [100, 120]
    assert result['airspace']['zones_above'] and not result['airspace']['zones_applied']


def test_flights_above_150_m_are_flagged():
    data = json.loads((CONTRACTS / 'scenario.example.json').read_text(encoding='utf-8'))
    data.setdefault('settings', {})['altitude_m'] = 160
    warnings = plan(Scenario.model_validate(data))['warnings']
    assert any('150 м' in w and 'разрешени' in w for w in warnings)


def test_plan_api_and_report_carry_levels(tmp_path):
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'levels.db').as_posix()}")) as client:
        sid = client.post('/api/scenarios', json=crossing(echelons=True)).json()['id']
        result = client.post('/api/planning-runs', json={'scenario_id': sid}).json()
        assert result['echelons']['applied'] and result['separation'] == {'horizontal_m': 30.0, 'vertical_m': 20.0}
        report = client.get(f"/api/plans/{result['id']}/report").text
        assert 'Высота' in report and 'по высоте' in report and 'Автоматически, шаг 20 м' in report
        exported = client.get(f"/api/plans/{result['id']}/export").json()
        assert {f['properties']['altitude_m'] for f in exported['features'] if 'phase' in f['properties']} >= {100, 120}


# --- fixed-wing model

def test_every_strip_is_entered_from_a_straight_lead_in():
    result = plan(Scenario.model_validate(fixed_wing()))
    legs = result['missions'][0]['legs']
    scenario = Scenario.model_validate(fixed_wing())
    geo = LocalGeometry(scenario)
    for before, survey in zip(legs, legs[1:]):
        if survey['phase'] != 'survey':
            continue
        assert before['phase'] == 'transit'
        a, b = [geo.forward(*p) for p in before['coordinates']]
        c, d = [geo.forward(*p) for p in survey['coordinates']]
        assert math.dist(a, b) == pytest.approx(50, abs=.01)
        assert math.atan2(b[1] - a[1], b[0] - a[0]) == pytest.approx(math.atan2(d[1] - c[1], d[0] - c[0]), abs=1e-6)


def test_the_plane_flies_strips_out_of_order_to_avoid_loops():
    result = plan(Scenario.model_validate(fixed_wing()))
    legs = result['missions'][0]['legs']
    surveys = [i for i, l in enumerate(legs) if l['phase'] == 'survey']
    gaps = [legs[b]['start'] - legs[a]['end'] for a, b in zip(surveys, surveys[1:])]
    # A loop to the neighbouring strip took ~75 s; a racetrack turn takes about half.
    assert sum(gaps) / len(gaps) < 40
    assert result['metrics']['total_airtime_s'] < 20 * 60


def test_the_plane_climbs_in_a_spiral_when_the_launch_corridor_is_closed():
    data = fixed_wing()
    site = next(s for s in data['sites'] if s['id'] == 'plane-base')['coordinates']
    ring = data['allowed_airspace']['coordinates'][0]
    data['allowed_airspace']['coordinates'][0] = [[max(x, site[0] - .006), y] for x, y in ring]
    result = plan(Scenario.model_validate(data))
    mission = result['missions'][0]
    assert mission['fixed_wing_assumptions']['spiral_climb']
    climb = [l for l in mission['legs'] if l['phase'] == 'climb']
    assert len(climb) > 10 and climb[-1]['altitude_end_m'] == 100
    assert any('по спирали' in w for w in result['warnings'])


def test_lower_parachute_release_circles_down_first_and_drifts_less():
    high = plan(Scenario.model_validate(fixed_wing()))['missions'][0]
    low = plan(Scenario.model_validate(fixed_wing(parachute_release_m=40)))['missions'][0]
    fall = [l for l in low['legs'] if l['phase'] == 'parachute'][0]
    assert fall['altitude_start_m'] == 40 and fall['end'] - fall['start'] == pytest.approx(8)
    descent = [l for l in low['legs'] if l['phase'] == 'descent']
    assert descent and descent[0]['altitude_start_m'] == pytest.approx(100) and descent[-1]['altitude_end_m'] == 40
    assert all(l['speed_mps'] == low['airspeed_mps'] and l['speed_reference'] == 'air' for l in descent)
    assert low['fixed_wing_assumptions']['descent_turns'] >= 1
    high_fall = [l for l in high['legs'] if l['phase'] == 'parachute'][0]
    assert high_fall['end'] - high_fall['start'] == pytest.approx(20)


def test_strong_crosswind_adds_strip_directions_along_and_across_the_wind():
    data = fixed_wing()
    data['weather'] = dict(wind_speed_mps=9, wind_from_deg=200)
    result = plan(Scenario.model_validate(data))
    assert result['metrics']['strip_angle_deg'] in (0, 90, 70.0, 160.0)
    mission = result['missions'][0]
    assert mission['survey']['crab_deg'] > 5
    assert mission['survey']['swath_width_m'] <= mission['survey']['nominal_swath_width_m']
    assert_valid(data, result, None)
