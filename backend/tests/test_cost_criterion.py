"""Version 1.2: the linear-quadratic cost criterion J = α·T + tᵀQt."""
import json
from pathlib import Path
import copy
import itertools
import math

import numpy as np
import pytest
from pydantic import ValidationError

from bas.catalog.service import resolve_profiles
from bas.domain.models import CostModel, CostPair, Scenario
from bas.planning import service
from bas.planning.cost import breakdown, estimator, matrix, validate_cost_model
from bas.planning.geometry import LocalGeometry
from bas.planning.scheduling.conflicts import conflict

H = 3600.0


def rect(x, y, w, h):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]}


# --- the formula on transparent numbers

def test_breakdown_matches_the_formula_by_hand():
    model = CostModel(alpha_per_h=100, default_diagonal=10, diagonal={'A': 40}, pairs=[CostPair(a='A', b='B', q=5)])
    # T = 1.5 h; t_A = 1 h, t_B = 0.5 h, t_C unused.
    result = breakdown(model, ['A', 'B', 'C'], 1.5 * H, {'A': 1 * H, 'B': 0.5 * H})
    assert result['linear'] == pytest.approx(150)
    assert [x['value'] for x in result['individual']] == pytest.approx([40 * 1, 10 * 0.25, 0])
    # The pair enters twice: q_AB·t_A·t_B + q_BA·t_B·t_A.
    assert result['pairs'][0]['value'] == pytest.approx(2 * 5 * 1 * 0.5)
    assert result['total'] == pytest.approx(150 + 40 + 2.5 + 5)
    assert result['aircraft_used'] == ['A', 'B'] and result['airtime_h'] == pytest.approx(1.5)


def test_quadratic_form_equals_t_q_t():
    model = CostModel(alpha_per_h=0, default_diagonal=3, diagonal={'B': 7}, pairs=[CostPair(a='A', b='C', q=-1), CostPair(a='B', b='C', q=2)])
    ids = ['A', 'B', 'C']
    t = np.array([0.4, 0.9, 0.25])
    q = matrix(model, ids)
    assert np.allclose(q, q.T)
    result = breakdown(model, ids, 0, {a: x * H for a, x in zip(ids, t)})
    assert result['quadratic'] == pytest.approx(float(t @ q @ t))


def test_estimator_agrees_with_breakdown():
    model = CostModel(alpha_per_h=60, default_diagonal=30, pairs=[CostPair(a='A', b='B', q=8)])
    value = estimator(model, ['A', 'B'])(2.5 * H, [1.2 * H, 0.7 * H])
    assert value == pytest.approx(breakdown(model, ['A', 'B'], 2.5 * H, {'A': 1.2 * H, 'B': 0.7 * H})['total'])


def test_zero_weights_cost_nothing_and_unused_aircraft_add_nothing():
    zero = CostModel(alpha_per_h=0, default_diagonal=0)
    assert breakdown(zero, ['A', 'B'], 5 * H, {'A': H})['total'] == 0
    model = CostModel(alpha_per_h=10, default_diagonal=50)
    assert breakdown(model, ['A', 'B'], H, {'A': H})['total'] == breakdown(model, ['A'], H, {'A': H})['total']


def test_cost_does_not_depend_on_the_order_of_the_fleet():
    model = CostModel(alpha_per_h=60, default_diagonal=30, diagonal={'C': 90}, pairs=[CostPair(a='A', b='C', q=4)])
    times = {'A': 0.5 * H, 'B': 0.8 * H, 'C': 0.3 * H}
    values = {round(breakdown(model, list(order), 1.1 * H, times)['total'], 12) for order in itertools.permutations('ABC')}
    assert len(values) == 1


# --- admissible coefficients

def test_positive_semidefinite_matrix_is_accepted_and_indefinite_rejected():
    validate_cost_model(CostModel(default_diagonal=4, pairs=[CostPair(a='A', b='B', q=4)]), ['A', 'B'])  # eigenvalues 0 and 8
    with pytest.raises(ValueError, match='положительно полуопределена'):
        validate_cost_model(CostModel(default_diagonal=4, pairs=[CostPair(a='A', b='B', q=5)]), ['A', 'B'])
    # Three pairs each within q_ij² ≤ q_ii·q_jj can still make Q indefinite: the check is on the whole matrix.
    pairs = [CostPair(a='A', b='B', q=-1), CostPair(a='A', b='C', q=-1), CostPair(a='B', b='C', q=-1)]
    with pytest.raises(ValueError):
        validate_cost_model(CostModel(default_diagonal=1, pairs=pairs), ['A', 'B', 'C'])


@pytest.mark.parametrize('model,message', [
    (dict(diagonal={'X': 1}), 'нет в парке'),
    (dict(pairs=[dict(a='A', b='A', q=1)]), 'индивидуальный'),
    (dict(pairs=[dict(a='A', b='B', q=1), dict(a='B', b='A', q=2)]), 'дважды'),
])
def test_cost_model_rejects_unknown_aircraft_self_pairs_and_duplicates(model, message):
    with pytest.raises(ValueError, match=message):
        validate_cost_model(CostModel(**model), ['A', 'B'])


@pytest.mark.parametrize('model', [dict(alpha_per_h=-1), dict(default_diagonal=-1), dict(diagonal={'A': -2}), dict(alpha_per_h=float('nan'))])
def test_negative_or_missing_weights_are_invalid(model):
    with pytest.raises(ValidationError):
        CostModel(**model)


def test_scenario_validates_the_cost_model_against_its_fleet():
    data = small_scenario()
    data['cost_model'] = dict(alpha_per_h=60, diagonal={'nobody': 3})
    with pytest.raises(ValidationError, match='нет в парке'):
        Scenario.model_validate(data)


# --- search and plans

def small_scenario(fleet=3, objective='min_cost', cost=None):
    sites = [dict(id='W', name='Запад', kind='launch_landing', coordinates=[37.296, 55.801]),
             dict(id='E', name='Восток', kind='launch_landing', coordinates=[37.312, 55.804])]
    return dict(schema_version='0.1', name='Малая задача', survey_area=rect(37.300, 55.800, .006, .004),
                allowed_airspace=rect(37.290, 55.795, .030, .015), sites=sites,
                fleet=[dict(id=f'G{n}', profile_id='gemini', battery_percent=100, payload='rgb', start_site_id='W' if n % 2 else 'E') for n in range(1, fleet + 1)],
                weather=dict(wind_speed_mps=2, wind_from_deg=270), survey_type='rgb', objective=objective,
                settings=dict(altitude_m=100, swath_width_m=100, side_overlap_percent=30),
                cost_model=cost or dict(alpha_per_h=60, default_diagonal=30))


def assert_feasible(data, result):
    scenario = Scenario.model_validate(data)
    geo = LocalGeometry(scenario)
    assert result['metrics']['coverage_percent'] >= 99.9
    legs = []
    for mission in result['missions']:
        assert mission['flight_time_s'] <= mission['budget_s'] + 1e-6
        local = [dict(l, xy=[geo.forward(*p) for p in l['coordinates']]) for l in mission['legs']]
        legs.append(local)
    assert not any(conflict(a, b) for i, a in enumerate(legs) for b in legs[:i])


def test_cost_criterion_plans_are_feasible_and_report_the_breakdown():
    data = small_scenario()
    result = service.plan(Scenario.model_validate(data))
    assert_feasible(data, result)
    cost = result['cost']
    assert cost['total'] == pytest.approx(cost['linear'] + cost['individual_total'] + cost['pairs_total'])
    assert cost['completion_h'] == pytest.approx(result['metrics']['completion_time_s'] / H)
    assert cost['airtime_h'] == pytest.approx(result['metrics']['total_airtime_s'] / H)
    assert cost['search'] is not None and cost['search']['final'] <= cost['search']['initial'] + 1e-9


def test_every_plan_reports_the_same_cost_model():
    plans = {objective: service.plan(Scenario.model_validate(small_scenario(objective=objective))) for objective in ('min_completion_time', 'min_total_airtime', 'min_cost')}
    models = {str(p['cost']['model']) for p in plans.values()}
    assert len(models) == 1
    # The cost criterion is never worse by its own measure than the plans of the other two.
    assert plans['min_cost']['cost']['total'] <= min(p['cost']['total'] for p in plans.values()) + 1e-6


def test_a_heavier_diagonal_moves_work_away_from_that_aircraft():
    time_of = lambda plan, a: sum(m['flight_time_s'] for m in plan['missions'] if m['aircraft_id'] == a)
    light = service.plan(Scenario.model_validate(small_scenario(cost=dict(alpha_per_h=10, default_diagonal=5))))
    busiest = max((m['aircraft_id'] for m in light['missions']), key=lambda a: time_of(light, a))
    data = small_scenario(cost=dict(alpha_per_h=10, default_diagonal=5, diagonal={busiest: 2000}))
    heavy = service.plan(Scenario.model_validate(data))
    assert time_of(heavy, busiest) < time_of(light, busiest)
    assert_feasible(data, heavy)


def test_time_weight_alone_prefers_finishing_early():
    slow = service.plan(Scenario.model_validate(small_scenario(cost=dict(alpha_per_h=0, default_diagonal=1))))
    fast = service.plan(Scenario.model_validate(small_scenario(cost=dict(alpha_per_h=10000, default_diagonal=1))))
    assert fast['metrics']['completion_time_s'] <= slow['metrics']['completion_time_s'] + 1e-6


def brute_force(data):
    """Best J over every assignment of strips to aircraft, one sortie each, strips in the planner's order."""
    scenario = Scenario.model_validate(data)
    profiles = resolve_profiles(scenario)
    sites = {s.id: s for s in scenario.sites if s.kind == 'launch_landing'}
    altitudes = {a.id: scenario.settings.altitude_m for a in scenario.fleet}
    best = math.inf
    for angle in (0, 90):
        count = service._attempt(scenario, profiles, sites, altitudes, None, forced=dict(angle=angle, count_only=True))['strip_count']
        for assignment in itertools.product(range(len(scenario.fleet)), repeat=count):
            try:
                run = service._attempt(scenario, profiles, sites, altitudes, None, forced=dict(angle=angle, assignment=list(assignment)))
            except service.PlanningError:
                continue
            best = min(best, run['value'])
    return best


@pytest.mark.parametrize('cost', [dict(alpha_per_h=60, default_diagonal=30), dict(alpha_per_h=20, default_diagonal=5, diagonal={'G2': 400}),
                                  dict(alpha_per_h=200, default_diagonal=10, pairs=[dict(a='G1', b='G2', q=8)])])
def test_heuristic_is_close_to_brute_force_over_assignments(cost):
    data = small_scenario(fleet=2, cost=cost)
    reference = brute_force(data)
    found = service.plan(Scenario.model_validate(data))['cost']['total']
    # The reference covers assignments only (single sortie, planner's strip order); the heuristic may
    # also reorder strips within a sortie, so it can only match or beat it — or fall short by a little.
    assert found <= reference * 1.02 + 1e-6


@pytest.mark.parametrize('name', ['scenario.multisurvey.example.json', 'scenario.echelons.example.json', 'scenario.fixedwing.example.json'])
def test_each_criterion_is_best_by_its_own_measure(name):
    # One set of plans for all three: no criterion's plan beats another's by that other's measure.
    data = json.loads((Path(__file__).resolve().parents[2] / 'contracts' / name).read_text(encoding='utf-8'))
    plans = {o: service.plan(Scenario.model_validate(dict(data, objective=o))) for o in ('min_completion_time', 'min_total_airtime', 'min_cost')}
    T = {o: p['metrics']['completion_time_s'] for o, p in plans.items()}
    L = {o: p['metrics']['total_airtime_s'] for o, p in plans.items()}
    J = {o: p['cost']['total'] for o, p in plans.items()}
    assert T['min_completion_time'] <= min(T.values()) + 1e-6
    assert L['min_total_airtime'] <= min(L.values()) + 1e-6
    assert J['min_cost'] <= min(J.values()) + 1e-6


# --- the cost of flying cᵀt and the open-data preset

def test_airtime_cost_by_hand_and_in_the_estimator():
    model = CostModel(alpha_per_h=100, airtime_default=20, airtime={'A': 50}, default_diagonal=0)
    result = breakdown(model, ['A', 'B', 'C'], 2 * H, {'A': 1 * H, 'B': 0.5 * H})
    # α·T = 200; cᵀt = 50·1 + 20·0.5 + 20·0 = 60; Q = 0.
    assert result['linear'] == pytest.approx(200) and result['airtime_total'] == pytest.approx(60)
    assert [x['airtime_value'] for x in result['individual']] == pytest.approx([50, 10, 0])
    assert result['total'] == pytest.approx(260)
    assert estimator(model, ['A', 'B', 'C'])(2 * H, [1 * H, 0.5 * H, 0]) == pytest.approx(260)


def test_models_saved_without_airtime_keep_the_original_formula():
    old = CostModel.model_validate(dict(alpha_per_h=60, default_diagonal=30))
    assert old.airtime_default == 0 and old.airtime == {}
    assert breakdown(old, ['A'], H, {'A': H})['total'] == pytest.approx(60 + 30)


def test_airtime_cost_is_validated():
    with pytest.raises(ValueError, match='нет в парке: X'):
        validate_cost_model(CostModel(airtime={'X': 1}), ['A'])
    with pytest.raises(ValidationError):
        CostModel(airtime={'A': -1})


def test_open_data_preset_is_derived_from_its_inputs():
    from bas.catalog import costs
    alpha = costs.TEAM['value'] * costs.SALARY['value'] * 1.3 / costs.HOURS_PER_MONTH['value'] + costs.TRANSPORT['value']
    assert costs.alpha() == pytest.approx(alpha)
    # Gemini: λ·price + battery price / cycles / (0.75 · 40 min).
    assert costs.airtime_cost('gemini', 40) == pytest.approx(1e-3 * 1_600_000 + 43_500 / 300 / 0.5)
    assert costs.reflight_cost(1000) == pytest.approx(1e-3 * (alpha + 1000) / 2)
    profiles = resolve_profiles(Scenario.model_validate(small_scenario()))
    document = costs.document(list(profiles.values()))
    for item in [document['failure_rate'], *document['alpha_inputs'].values(), *document['by_profile']['gemini']['inputs'].values()]:
        assert item['status'] in ('confirmed', 'computed', 'assumed') and item['source']


def test_a_scenario_without_a_model_uses_the_preset_by_aircraft_type():
    data = small_scenario()
    data.pop('cost_model')
    data['fleet'][1]['profile_id'] = 'geoscan_801'
    result = service.plan(Scenario.model_validate(data))
    cost = result['cost']
    assert cost['default_model'] and cost['model']['alpha_per_h'] == 2491
    assert cost['model']['airtime'] == {'G1': 1890, 'G2': 3400, 'G3': 1890}
    assert cost['total'] == pytest.approx(cost['linear'] + cost['airtime_total'] + cost['quadratic'])


def test_trade_off_points_explain_the_choice_of_j():
    data = small_scenario()
    data.pop('cost_model')
    result = service.plan(Scenario.model_validate(data))
    tradeoff = result['cost']['tradeoff']
    chosen = [p for p in tradeoff['points'] if p['chosen']]
    assert len(chosen) == 1 and chosen[0]['j'] == pytest.approx(result['cost']['total'], rel=1e-6)
    # The plan of J is on the lower hull, and the current α lies in its range.
    low, high = chosen[0]['alpha_range']
    assert low - 1e-6 <= tradeoff['alpha_per_h'] <= (high if high is not None else math.inf) + 1e-6
    assert all(p['j'] >= chosen[0]['j'] - 1e-6 for p in tradeoff['points'])


def test_type_coefficients_apply_to_every_aircraft_of_the_type_unless_it_has_its_own():
    data = small_scenario(cost=dict(alpha_per_h=100, airtime_default=10, default_diagonal=1, airtime={'G2': 70},
                                    by_profile={'gemini': dict(airtime=40, diagonal=5)}))
    scenario = Scenario.model_validate(data)
    from bas.planning.cost import resolve
    model = resolve(scenario.cost_model, scenario.fleet)
    # G2 keeps its own c; G1 and G3 take the type's c; q comes from the type for all three.
    assert model.airtime == {'G2': 70, 'G1': 40, 'G3': 40} and model.diagonal == {'G1': 5, 'G2': 5, 'G3': 5}
    result = service.plan(scenario)
    assert result['cost']['model']['by_profile']['gemini']['airtime'] == 40
    rates = {x['aircraft_id']: x['c'] for x in result['cost']['individual']}
    assert rates == {'G1': 40, 'G2': 70, 'G3': 40}


def test_type_coefficients_are_checked_for_positive_semidefiniteness():
    bad = small_scenario(cost=dict(alpha_per_h=1, default_diagonal=50, by_profile={'gemini': dict(diagonal=1)},
                                   pairs=[dict(a='G1', b='G2', q=5)]))
    with pytest.raises(ValidationError, match='полуопределена'):
        Scenario.model_validate(bad)
