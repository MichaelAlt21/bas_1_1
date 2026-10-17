"""Bounded local 2D geometry model for demonstration plans.

Free space is the allowed airspace shrunk by the boundary clearance, minus every applicable
blocker (drawn no-fly zones, restriction zones and obstacles) grown by its clearance. Shortest
paths run over the vertices of that free space with A*; a vertex's visible neighbours are
computed the first time it is expanded, so thousands of obstacle vertices cost only what the
routes actually touch.
"""
import heapq
import math
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform, unary_union

MAX_INPUT_VERTICES = 5000
MAX_AREA_M = 30000
MAX_ALLOWED_M = 40000
MAX_NODES = 6000
# Polygon parts of the surveyable area smaller than this are left out rather than planned.
MIN_PART_M2 = 500


class PlanningError(ValueError):
    """A plan is impossible or was not found. `details` name the subjects and values behind it
    (aircraft, sites, requirements, actual, required…); `diagnostics` are ready structured ones."""
    def __init__(self, code, message, diagnostics=None, **details):
        self.code = code
        self.details = details
        self.diagnostics = diagnostics or []
        super().__init__(message)


def reject(code, message, **details):
    raise PlanningError(code, message, **details)


def _grown(geometry, clearance):
    if geometry.geom_type == 'Point':
        # Octagon circumscribing the clearance circle, so the circle is never cut.
        return geometry.buffer(clearance / math.cos(math.pi / 8), quad_segs=2)
    if geometry.geom_type == 'LineString':
        return geometry.buffer(clearance, cap_style=3, join_style=2)
    return geometry.buffer(clearance, join_style=2)


def _polygons(geometry):
    if geometry.is_empty: return []
    if geometry.geom_type == 'Polygon': return [geometry]
    return [p for g in getattr(geometry, 'geoms', []) for p in _polygons(g)]


class LocalGeometry:
    def __init__(self, scenario, blockers=None):
        if blockers is None:
            from bas.planning.airspace import airspace
            blockers, _ = airspace(scenario)
        settings = scenario.settings
        survey_areas = [scenario.survey_area, *scenario.additional_survey_areas]
        inputs = [*survey_areas, *scenario.no_fly_zones]
        if scenario.allowed_airspace: inputs.append(scenario.allowed_airspace)
        if sum(len(ring) for p in inputs for ring in p.coordinates) > MAX_INPUT_VERTICES:
            reject('GEOMETRY_TOO_COMPLEX', f'Для интерактивного расчета используйте не более {MAX_INPUT_VERTICES} вершин в районах съемки, запретах и разрешенном пространстве.')
        area = unary_union([shape(p.model_dump()) for p in survey_areas])
        lon, lat = area.centroid.coords[0]
        if abs(lat) > 80 or area.bounds[2] - area.bounds[0] > 1:
            reject('REGION_TOO_LARGE', 'Нужен локальный участок вне полярных областей и без пересечения 180°.')
        crs = CRS.from_proj4(f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m')
        self.forward = Transformer.from_crs(4326, crs, always_xy=True).transform
        self.inverse = Transformer.from_crs(crs, 4326, always_xy=True).transform
        self.areas = [transform(self.forward, shape(p.model_dump())) for p in survey_areas]
        self.area = unary_union(self.areas)
        if max(self.area.bounds[2] - self.area.bounds[0], self.area.bounds[3] - self.area.bounds[1]) > MAX_AREA_M:
            reject('REGION_TOO_LARGE', f'Размер участка ограничен {MAX_AREA_M // 1000} км. Большое задание разбейте на пакеты работ.')
        if scenario.allowed_airspace is None:
            reject('AIRSPACE_REQUIRED', 'Нарисуйте разрешенное пространство с местом для подходов и возвращения.')
        allowed = transform(self.forward, shape(scenario.allowed_airspace.model_dump()))
        if max(allowed.bounds[2] - allowed.bounds[0], allowed.bounds[3] - allowed.bounds[1]) > MAX_ALLOWED_M:
            reject('REGION_TOO_LARGE', f'Размер разрешенного пространства ограничен {MAX_ALLOWED_M // 1000} км.')
        margin = settings.boundary_clearance_m
        # Blockers far outside the allowed airspace cannot touch free space: skip them before projecting.
        west, south, east, north = shape(scenario.allowed_airspace.model_dump()).bounds
        self.blockers, grown = [], []
        for blocker in blockers:
            reach_lat = blocker.clearance_m / 110000
            reach_lon = blocker.clearance_m / (111320 * max(math.cos(math.radians(max(abs(south), abs(north)))), 0.1))
            bx0, by0, bx1, by1 = blocker.geometry.bounds
            if bx0 > east + reach_lon or bx1 < west - reach_lon or by0 > north + reach_lat or by1 < south - reach_lat:
                continue
            local = _grown(transform(self.forward, blocker.geometry), blocker.clearance_m)
            if local.intersects(allowed):
                self.blockers.append((blocker, local)); grown.append(local)
        self.free = allowed.buffer(-margin, join_style=2).difference(unary_union(grown))
        # Only free-space components holding a main site can be flown: blockers may cut off pockets.
        # A site outside free space is reported by the planner itself, so it does not count here.
        homes = [Point(self.forward(*s.coordinates)) for s in scenario.sites if s.kind == 'launch_landing']
        parts = _polygons(self.free)
        linked = [p for p in parts if any(p.covers(h) for h in homes)]
        self.reachable = unary_union(linked) if linked and len(linked) < len(parts) else self.free
        self.original_areas = self.areas
        # 1-based numbers of the drawn areas that remain; an area can vanish under blockers.
        self.area_numbers = list(range(1, len(self.areas) + 1))
        self.excluding, self.excluded_m2, self.excluded_by, self.excluded_outside = False, 0.0, [], False
        self.cut_off_m2 = 0.0
        if self.free.is_empty or not self.free.covers(self.area) or not self.reachable.covers(self.area):
            hits = [b for b, local in self.blockers if local.intersects(self.area)]
            outside = not allowed.buffer(-margin, join_style=2).covers(self.area)
            cut_off = self.free.covers(self.area) and not self.reachable.covers(self.area)
            if settings.blocked_area_policy != 'exclude' or self.free.is_empty:
                if cut_off:
                    reject('AREA_UNREACHABLE', 'Часть области съемки отрезана ограничениями от всех основных площадок: к ней нет свободного пути. '
                           'Добавьте площадку в этой части, измените разрешенное пространство или выберите политику «Исключать заблокированные участки».')
                names = ', '.join(b.label for b in hits[:6]) + ('…' if len(hits) > 6 else '')
                reasons = ' и '.join(filter(None, [f'пересекает ограничения ({names})' if hits else '',
                                                    'выходит из разрешенного пространства' if outside else '']))
                reject('AREA_BLOCKED', f'Область съемки {reasons or "не помещается в свободное пространство"} с учетом защитных отступов. '
                       'Выберите политику «Исключать заблокированные участки» или измените район. Непокрытые участки без этой политики не исключаются.')
            kept, numbers = [], []
            for number, zone in enumerate(self.areas, 1):
                parts = [p for p in _polygons(zone.intersection(self.reachable)) if p.area >= MIN_PART_M2]
                if parts: kept.append(unary_union(parts)); numbers.append(number)
            if not kept:
                reject('AREA_BLOCKED', 'Вся область съемки закрыта ограничениями с учетом защитных отступов или отрезана ими от площадок.')
            self.areas, self.area_numbers = kept, numbers
            self.area = unary_union(kept)
            self.cut_off_m2 = unary_union(self.original_areas).intersection(self.free).area - self.area.area
            self.excluded_m2 = unary_union(self.original_areas).area - self.area.area
            self.excluded_by = [dict(kind=b.kind, id=b.ref, label=b.label) for b in hits]
            self.excluded_outside, self.excluding = outside, True
        shapely.prepare(self.free)
        self.nodes = []
        for polygon in _polygons(self.free):
            for ring in [polygon.exterior, *polygon.interiors]:
                self.nodes.extend(tuple(p) for p in list(ring.coords)[:-1])
        if len(self.nodes) > MAX_NODES:
            reject('GEOMETRY_TOO_COMPLEX', f'Слишком сложное свободное пространство: {len(self.nodes)} вершин после отступов (максимум {MAX_NODES}). Уменьшите разрешенное пространство вокруг района.')
        self.coords = np.array(self.nodes, dtype=float).reshape(-1, 2)
        self.neighbours = {}
        self.path = lru_cache(maxsize=20000)(self.path)

    def visible(self, a, b):
        return self.free.covers(Point(a) if a == b else LineString([a, b]))

    def _sight(self, point, skip=None):
        """(index, distance) of every free-space vertex in a straight line of sight from point."""
        if not self.nodes: return []
        here = np.asarray(point, dtype=float)
        lines = shapely.linestrings(np.stack([np.broadcast_to(here, self.coords.shape), self.coords], axis=1))
        seen = shapely.covers(self.free, lines)
        same = np.all(self.coords == here, axis=1)
        if same.any(): seen[same] = self.free.covers(Point(point))
        if skip is not None: seen[skip] = False
        return [(j, math.dist(point, self.nodes[j])) for j in np.flatnonzero(seen).tolist()]

    def _edges(self, node):
        if node not in self.neighbours:
            self.neighbours[node] = self._sight(self.nodes[node], skip=node)
        return self.neighbours[node]

    def path(self, a, b):
        if self.visible(a, b): return [a, b]
        # Vertices that see the target, so each expansion checks the goal edge in constant time.
        to_goal = dict(self._sight(b))
        if not to_goal: return None
        start, end = -1, -2
        point = lambda i: a if i == start else b if i == end else self.nodes[i]
        queue = [(math.dist(a, b), 0., start)]; distances = {start: 0.}; previous = {}
        while queue:
            _, distance, node = heapq.heappop(queue)
            if distance > distances[node]: continue
            if node == end:
                chain = [end]
                while chain[-1] != start: chain.append(previous[chain[-1]])
                return [point(i) for i in reversed(chain)]
            edges = self._sight(a) if node == start else list(self._edges(node))
            if node in to_goal: edges.append((end, to_goal[node]))
            for other, cost in edges:
                candidate = distance + cost
                if candidate < distances.get(other, math.inf):
                    distances[other] = candidate; previous[other] = node
                    heapq.heappush(queue, (candidate + math.dist(point(other), b), candidate, other))
        return None
