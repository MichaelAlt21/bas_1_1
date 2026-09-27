"""Whole-task estimate: work packages, sorties and working days for a task too large for one plan.

The task area is cut by a square grid; each non-empty cell is a work package flown from one site
at its centroid. Survey time comes from the line length (area / line spacing) plus a turn per
line; each sortie adds take-off, climb, descent and the round trip from the site to the package.
This is an order-of-magnitude estimate for choosing the fleet and the packages, not a flight plan:
restrictions, wind and deconfliction are applied only by the detailed plan of a package.
"""
import math

import shapely
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union

from bas.imports.layers import LocalMetres, _polygons

TURN_S = {'multirotor': 10.0, 'fixed_wing': 25.0}
FLEET_SIZES = [1, 2, 3, 5, 10, 15, 20, 30, 50]
MAX_PACKAGES = 400
# Whole strips rarely fill a sortie exactly: the detailed plan leaves about a tenth of each unused.
FILL = 0.9
SAMPLES_PER_PART = 60


def _mean_distance(pieces, site):
    """Area-weighted mean distance from the site over the pieces, from a grid of sample points."""
    total, weight = 0.0, 0.0
    for piece in pieces:
        step = max(math.sqrt(piece.area / SAMPLES_PER_PART), 1.0)
        x0, y0, x1, y1 = piece.bounds
        points = [(x, y) for x in _range(x0, x1, step) for y in _range(y0, y1, step)]
        xs = [p[0] for p in points]; ys = [p[1] for p in points]
        mask = shapely.contains_xy(piece, xs, ys) if points else []
        inside = [p for p, keep in zip(points, mask) if keep] or [(piece.representative_point().x, piece.representative_point().y)]
        for p in inside:
            total += math.dist(site, p) * piece.area / len(inside)
        weight += piece.area
    return total / weight


def _range(lo, hi, step):
    value = lo + step / 2
    while value < hi:
        yield value
        value += step


def _lonlat(geometry, local):
    return {'type': 'Polygon', 'coordinates': [[list(p) for p in ring] for ring in mapping(transform(local.inverse, geometry))['coordinates']]}


def task_estimate(request, profile):
    s = request.settings
    speed = s.fixed_wing.airspeed_mps if profile.airframe == 'fixed_wing' else s.airspeed_mps
    spacing = s.swath_width_m * (1 - s.side_overlap_percent / 100)
    budget = profile.max_flight_time_min * 60 * s.endurance_factor * (100 - s.reserve_percent) / 100
    overhead = s.altitude_m + 40
    reach_m = budget * speed
    cell = request.cell_km * 1000 if request.cell_km else min(15000, max(1000, round(0.3 * reach_m, -2)))
    polygons = [shape(p.model_dump()) for p in request.areas]
    center = unary_union(polygons).centroid
    local = LocalMetres(center.x, center.y)
    metric = [transform(local.forward, p) for p in polygons]
    x0, y0, x1, y1 = unary_union(metric).bounds
    cells = {}
    for polygon in metric:
        bx0, by0, bx1, by1 = polygon.bounds
        for i in range(int((bx0 - x0) // cell), int((bx1 - x0) // cell) + 1):
            for j in range(int((by0 - y0) // cell), int((by1 - y0) // cell) + 1):
                square = box(x0 + i * cell, y0 + j * cell, x0 + (i + 1) * cell, y0 + (j + 1) * cell)
                pieces = [p for p in _polygons(polygon.intersection(square)) if p.area >= 1]
                if pieces: cells.setdefault((i, j), []).extend(pieces)
    if len(cells) > MAX_PACKAGES:
        raise ValueError(f'Получилось {len(cells)} пакетов (максимум {MAX_PACKAGES}): увеличьте размер пакета.')
    # Per line: the turn (or two hovers of a multirotor) and the hop to the next line.
    turn = TURN_S.get(profile.airframe, 10.0) + spacing / speed
    packages = []
    for (i, j), pieces in cells.items():
        area = sum(p.area for p in pieces)
        cx = sum(p.centroid.x * p.area for p in pieces) / area
        cy = sum(p.centroid.y * p.area for p in pieces) / area
        lines_m = area / spacing
        turns = sum(max(1, math.sqrt(p.area) / spacing) for p in pieces)
        survey_s = lines_m / speed + turns * turn
        mean_distance = _mean_distance(pieces, (cx, cy))
        far = max(math.dist((cx, cy), q) for p in pieces for q in p.exterior.coords)
        transit_s = 2 * mean_distance / speed
        usable = (budget - overhead - transit_s) * FILL
        reachable = usable > 0 and 2 * far / speed + overhead < budget
        sorties = math.ceil(survey_s / usable) if reachable else None
        airtime = survey_s + sorties * (overhead + transit_s) if reachable else None
        site = local.inverse(cx, cy)
        packages.append(dict(cell=[i, j], area_ha=area / 1e4, parts=len(pieces), lines_km=lines_m / 1000,
                             survey_h=survey_s / 3600, sorties=sorties, airtime_h=None if airtime is None else airtime / 3600,
                             reachable=reachable, farthest_km=far / 1000, site=[site[0], site[1]],
                             areas=[_lonlat(p, local) for p in pieces]))
    packages.sort(key=lambda p: (-p['area_ha'], p['cell']))
    for number, package in enumerate(packages, 1):
        package['id'] = f'P{number:03d}'
    flown = [p for p in packages if p['reachable']]
    sorties = sum(p['sorties'] for p in flown)
    airtime_h = sum(p['airtime_h'] for p in flown)
    sortie_h = airtime_h / sorties if sorties else 0
    charge_min = profile.charge_time_min or s.charge_time_min
    if s.recharge_mode == 'charge':
        # The aircraft waits for its own battery: handling plus charging what the sortie used.
        full_h = profile.max_flight_time_min * s.endurance_factor / 60
        cycle_h = sortie_h + s.ground_handling_min / 60 + charge_min / 60 * sortie_h / full_h
    else:
        cycle_h = sortie_h + s.turnaround_min / 60
    per_day = max(1, math.floor(request.workday_h / cycle_h)) if cycle_h else 0
    sizes = sorted({n for n in FLEET_SIZES if n <= max(request.fleet_size, 1)} | {request.fleet_size})
    days = [dict(aircraft=n, days=math.ceil(sorties / (n * per_day)) if per_day and sorties else 0) for n in sizes]
    interval, unit = profile.maintenance_interval, profile.maintenance_unit
    maintenance = None if interval is None else sorties / interval if unit == 'flights' else airtime_h / interval
    return dict(profile_id=profile.id, cell_km=cell / 1000, line_spacing_m=spacing, sortie_budget_min=budget / 60,
                packages=packages, unreachable_packages=[p['id'] for p in packages if not p['reachable']],
                totals=dict(area_ha=sum(p['area_ha'] for p in packages), lines_km=sum(p['lines_km'] for p in packages),
                            sorties=sorties, airtime_h=airtime_h, mean_sortie_min=sortie_h * 60, sorties_per_aircraft_day=per_day,
                            maintenance_intervals=maintenance),
                days_by_fleet=days,
                assumptions=['Один участок пакета — одна площадка в его центре масс; переезды расчетов между пакетами не учтены.',
                             'Галсы — площадь / шаг линий; на каждую линию разворот и переход к следующей; перелеты — среднее расстояние от площадки по площади пакета; вылет загружен на 90%. Ветер, облет ограничений и расхождение бортов не учтены.',
                             f'Вылет: ресурс {budget / 60:.0f} мин (свежая АКБ, резерв {s.reserve_percent:g}%), '
                             + (f'между вылетами — обслуживание {s.ground_handling_min:g} мин и зарядка своей АКБ (полная {charge_min:g} мин), очередь к ЗУ не учтена'
                                if s.recharge_mode == 'charge' else f'замена АКБ между вылетами {s.turnaround_min:g} мин')
                             + f', рабочий день {request.workday_h:g} ч.'],
                cycle_min=cycle_h * 60)
