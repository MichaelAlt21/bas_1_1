"""Fleet sizing: finish time and airtime of the fastest plan for 1..N aircraft of the fleet.

Answers the customer's two questions: how many aircraft are needed to finish by the deadline,
and how long the work takes with a given number of aircraft. Each point is a full plan with
the number of aircraft capped; once a plan leaves an aircraft idle, a larger cap cannot change
it, so the sweep stops there.
"""
import time

from bas.planning.airspace import deadline
from bas.planning.geometry import PlanningError
from bas.planning.service import plan

COARSE = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20]
TIME_BUDGET_S = 90


def _variant(scenario, count, objective='min_completion_time'):
    settings = scenario.settings.model_copy(update=dict(max_aircraft=count))
    return scenario.model_copy(update=dict(objective=objective, settings=settings))


def _row(scenario, count, limit_s, objective='min_completion_time'):
    try:
        # Many plans for one table: without the local search, so a row is an upper estimate of the plan the button computes.
        result = plan(_variant(scenario, count, objective), search=False)
    except PlanningError as error:
        return dict(aircraft=count, feasible=False, code=error.code, message=str(error))
    m = result['metrics']
    return dict(aircraft=count, feasible=True, aircraft_used=m['aircraft_used'], sorties=m['sortie_count'],
                completion_s=m['completion_time_s'], total_airtime_s=m['total_airtime_s'],
                maintenance_share=m['maintenance_share_total'], coverage_percent=m['coverage_percent'],
                meets_deadline=None if limit_s is None else m['completion_time_s'] <= limit_s)


def fleet_analysis(scenario, time_budget_s=TIME_BUDGET_S):
    started = time.monotonic()
    size = len(scenario.fleet)
    due = deadline(scenario)
    limit = due['limit_s']
    rows, truncated, saturated = {}, False, None
    for count in [k for k in COARSE if k < size] + [size]:
        if time.monotonic() - started > time_budget_s:
            truncated = True; break
        rows[count] = _row(scenario, count, limit)
        if rows[count]['feasible'] and rows[count]['aircraft_used'] < count:
            saturated = rows[count]['aircraft_used']; break
    # Between the last count that misses the deadline and the first that meets it, bisect.
    if limit is not None:
        while not truncated:
            ordered = sorted(rows)
            meeting = [k for k in ordered if rows[k].get('meets_deadline')]
            if not meeting: break
            below = [k for k in ordered if k < meeting[0]]
            if not below or meeting[0] - below[-1] <= 1: break
            middle = (below[-1] + meeting[0]) // 2
            if time.monotonic() - started > time_budget_s:
                truncated = True; break
            rows[middle] = _row(scenario, middle, limit)
    table = [rows[k] for k in sorted(rows)]
    # The heuristic is not monotone: more aircraft can yield a slower plan. With a cap of N the
    # best plan found for any smaller count is still valid, so the table carries that too.
    best = None
    for row in table:
        if row['feasible'] and (best is None or row['completion_s'] < best['completion_s']):
            best = row
        row['best_completion_s'] = best['completion_s'] if best else None
        row['best_aircraft'] = best['aircraft'] if best else None
    feasible = [r for r in table if r['feasible']]
    needed = next((r['aircraft'] for r in table if r.get('meets_deadline')), None)
    balanced = None
    if needed is not None and not truncated:
        # Least airtime that still meets the deadline: the airtime objective at the smallest sufficient fleet.
        candidate = _row(scenario, needed, limit, 'min_total_airtime')
        balanced = candidate if candidate.get('meets_deadline') else next(r for r in table if r['aircraft'] == needed)
    requested = scenario.settings.max_aircraft
    with_requested = None
    if requested is not None:
        with_requested = rows.get(requested) or (_row(scenario, requested, limit) if not truncated else None)
    fastest = min(feasible, key=lambda r: r['completion_s'], default=None)
    if limit is None:
        verdict = 'Срок не задан: таблица показывает время завершения для разного числа бортов.'
    elif needed is not None:
        verdict = f'В срок укладывается план с {needed} бортами из {size}.'
    elif fastest:
        verdict = (f'В срок не укладывается ни один вариант: самый быстрый ({fastest["aircraft"]} бортов) завершает работу за '
                   f'{fastest["completion_s"] / 60:.0f} мин при сроке {limit / 60:.0f} мин. Нужны дополнительные борта, повторные вылеты или площадки ближе к району.')
    else:
        verdict = 'Ни один вариант не рассчитан: см. причины в таблице.'
    return dict(fleet_size=size, deadline_s=limit, deadline_source=due['source'], sunset=due['sunset'], rows=table,
                aircraft_needed=needed, balanced=balanced, with_requested=with_requested, saturated_at=saturated,
                truncated=truncated, verdict=verdict, calculation_time_s=time.monotonic() - started)
