"""Linear-quadratic cost of a plan: J = α·T + cᵀt + tᵀQt.

T   — completion time of the operation: from its start to the landing of the last sortie, h.
t_i — flight time of aircraft i summed over all its sorties, h. Waiting on the ground, battery
      swaps and charging are not flight time: they enter only through T.
α   — cost of an hour of the operation, у.е./ч (conventional cost units).
c   — cost of an hour of flight of each aircraft, у.е./ч: wear, battery cycles, expected loss.
Q   — symmetric positive semidefinite matrix, у.е./ч², indexed by aircraft id:
      tᵀQt = Σ_i q_ii·t_i² + 2·Σ_{i<j} q_ij·t_i·t_j.

α·T and cᵀt make J a balance between the completion time and the airtime of the fleet: with
c = 0 it follows the time alone, with α = 0 the cost of flying. q_ii makes the cost of an
aircraft grow faster than its flight time (wear concentrated on one aircraft, the work flown
again if it fails); q_ij couples two aircraft: their product of flight times, not the time they spend in the
air together. Q ⪰ 0 keeps tᵀQt ≥ 0 for every t, so the cost never rewards flying more; it also
makes the cost convex in t, which does not make the routing problem convex.
"""
import math

import numpy as np

HOUR = 3600.0


def coefficients(model, ids):
    """(diagonal list, [(i, j, q_ij)]) for aircraft `ids` in this order."""
    index = {a: n for n, a in enumerate(ids)}
    diagonal = [model.diagonal.get(a, model.default_diagonal) for a in ids]
    pairs = [(index[p.a], index[p.b], p.q) for p in model.pairs if p.a in index and p.b in index and p.q]
    return diagonal, pairs


def resolve(model, fleet):
    """The model with type coefficients written out per aircraft: own entry, else its type's, else the default."""
    if not model.by_profile:
        return model
    airtime, diagonal = dict(model.airtime), dict(model.diagonal)
    for aircraft in fleet:
        typed = model.by_profile.get(aircraft.profile_id)
        if typed is None:
            continue
        if typed.airtime is not None and aircraft.id not in airtime:
            airtime[aircraft.id] = typed.airtime
        if typed.diagonal is not None and aircraft.id not in diagonal:
            diagonal[aircraft.id] = typed.diagonal
    return model.model_copy(update=dict(airtime=airtime, diagonal=diagonal))


def airtime_rates(model, ids):
    """c_i for aircraft `ids` in this order, у.е./ч."""
    return [model.airtime.get(a, model.airtime_default) for a in ids]


def matrix(model, ids):
    diagonal, pairs = coefficients(model, ids)
    q = np.diag(np.asarray(diagonal, dtype=float)) if ids else np.zeros((0, 0))
    for i, j, value in pairs:
        q[i, j] = q[j, i] = value
    return q


def validate_cost_model(model, ids):
    """Raise ValueError with a reason unless the model fits the fleet and Q is positive semidefinite."""
    known = set(ids)
    unknown = sorted({key for key in model.diagonal if key not in known} | {key for key in model.airtime if key not in known}
                     | {x for p in model.pairs for x in (p.a, p.b) if x not in known})
    if unknown:
        raise ValueError('Модель стоимости ссылается на борта, которых нет в парке: ' + ', '.join(unknown))
    seen = set()
    for p in model.pairs:
        if p.a == p.b:
            raise ValueError(f'Пара {p.a}–{p.b}: для одного борта задайте индивидуальный коэффициент, а не парный')
        if not math.isfinite(p.q):
            raise ValueError(f'Пара {p.a}–{p.b}: коэффициент должен быть числом')
        key = frozenset((p.a, p.b))
        if key in seen:
            raise ValueError(f'Пара {p.a}–{p.b} задана дважды')
        seen.add(key)
    if not ids:
        return
    q = matrix(model, ids)
    smallest = float(np.linalg.eigvalsh(q).min())
    scale = max(1.0, float(np.abs(q).max()))
    if smallest < -1e-9 * scale:
        raise ValueError(f'Матрица Q не положительно полуопределена: наименьшее собственное значение {smallest:.3g} у.е./ч². '
                         'Отрицательные парные коэффициенты должны быть малы по сравнению с индивидуальными: '
                         'для пары достаточно q_ij² ≤ q_ii·q_jj, а для всей матрицы нужна проверка целиком.')


def breakdown(model, ids, completion_s, airtime_s):
    """Value of J and every contribution. `airtime_s` maps aircraft id → total flight time, s."""
    diagonal, pairs = coefficients(model, ids)
    rates = airtime_rates(model, ids)
    t = [airtime_s.get(a, 0.0) / HOUR for a in ids]
    big_t = completion_s / HOUR
    linear = model.alpha_per_h * big_t
    individual = [dict(aircraft_id=a, t_h=t[n], q=diagonal[n], value=diagonal[n] * t[n] ** 2, c=rates[n], airtime_value=rates[n] * t[n])
                  for n, a in enumerate(ids)]
    paired = [dict(a=ids[i], b=ids[j], q=q, t_a_h=t[i], t_b_h=t[j], value=2 * q * t[i] * t[j]) for i, j, q in pairs]
    quadratic = sum(x['value'] for x in individual) + sum(x['value'] for x in paired)
    airtime_total = sum(x['airtime_value'] for x in individual)
    used = [a for a in ids if airtime_s.get(a, 0.0) > 0]
    # `linear` stays α·T as in plans saved before cᵀt existed; cᵀt is `airtime_total`.
    return dict(total=linear + airtime_total + quadratic, linear=linear, airtime_total=airtime_total, quadratic=quadratic,
                individual_total=sum(x['value'] for x in individual), pairs_total=sum(x['value'] for x in paired),
                completion_h=big_t, airtime_h=sum(t), aircraft_used=used, alpha_per_h=model.alpha_per_h,
                individual=individual, pairs=paired, units='у.е.')


def tradeoff(model, ids, variants, relevelled=False):
    """The plans the criteria chose from, as points (T, cost of flying), and where J picks each.

    `variants` — [{completion_s, airtime_s: {id: s}, origin, chosen}]. A point's cost of flying is
    J − α·T = cᵀt + tᵀQt. J = α·T + that is minimised by a point of the lower convex hull of the
    points; a hull point stays the choice of J while α is inside its `alpha_range` (у.е./ч), the
    other coefficients fixed. Identical points are merged; dominated ones are marked.
    """
    points = []
    for v in variants:
        b = breakdown(model, ids, v['completion_s'], v['airtime_s'])
        point = dict(t_h=round(b['completion_h'], 6), l_h=round(b['airtime_h'], 6), flying=round(b['total'] - b['linear'], 6),
                     j=round(b['total'], 6), aircraft_used=len(b['aircraft_used']), origins=[v.get('origin', 'candidate')], chosen=bool(v.get('chosen')))
        same = next((p for p in points if abs(p['t_h'] - point['t_h']) < 1e-6 and abs(p['flying'] - point['flying']) < 1e-6), None)
        if same:
            same['origins'] = sorted(set(same['origins'] + point['origins']))
            same['chosen'] = same['chosen'] or point['chosen']
        else:
            points.append(point)
    points.sort(key=lambda p: (p['t_h'], p['flying']))
    for p in points:
        p['dominated'] = any(q is not p and q['t_h'] <= p['t_h'] + 1e-9 and q['flying'] <= p['flying'] + 1e-9
                             and (q['t_h'] < p['t_h'] - 1e-9 or q['flying'] < p['flying'] - 1e-9) for q in points)
        p['alpha_range'] = None
    # Lower convex hull of the non-dominated points, T ascending: slopes give the α of each switch.
    front = [p for p in points if not p['dominated']]
    hull = []
    for p in front:
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            if (b['flying'] - a['flying']) * (p['t_h'] - a['t_h']) >= (p['flying'] - a['flying']) * (b['t_h'] - a['t_h']) - 1e-12:
                hull.pop()
            else:
                break
        hull.append(p)
    for k, p in enumerate(hull):
        upper = (p['flying'] - hull[k - 1]['flying']) / (hull[k - 1]['t_h'] - p['t_h']) if k > 0 else None
        lower = (hull[k + 1]['flying'] - p['flying']) / (p['t_h'] - hull[k + 1]['t_h']) if k + 1 < len(hull) else 0.0
        # J = α·T + flying is least at p for α between the slopes to its neighbours.
        p['alpha_range'] = [round(max(lower, 0.0), 3), None if upper is None else round(upper, 3)]
    return dict(points=points, alpha_per_h=model.alpha_per_h, relevelled=relevelled,
                note='Точки — планы, из которых выбирали критерии (варианты галсов и распределения и итоги локального поиска). '
                     'По оси Y — стоимость налета cᵀt + tᵀQt.')


def estimator(model, ids):
    """Fast J for the allocation: f(T_s, [t_i_s in `ids` order]) → у.е."""
    diagonal, pairs = coefficients(model, ids)
    rates = airtime_rates(model, ids)
    alpha = model.alpha_per_h

    def value(completion_s, times_s):
        t = [x / HOUR for x in times_s]
        total = alpha * completion_s / HOUR
        for n, c in enumerate(rates):
            total += c * t[n]
        for n, q in enumerate(diagonal):
            total += q * t[n] * t[n]
        for i, j, q in pairs:
            total += 2 * q * t[i] * t[j]
        return total
    return value
