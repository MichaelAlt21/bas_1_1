"""Convert KML features into survey areas, restriction zones and obstacles.

Every conversion reports what it repaired, dropped or assumed; nothing is changed silently.
"""
import math
from datetime import datetime, time

import shapely
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon, box, mapping
from shapely.ops import transform

from bas.domain.models import Obstacle, RestrictionZone, TimeWindow, Validity, WeeklyWindow
from bas.imports.altitudes import parse_altitude_text, parse_compact_limit
from bas.imports.kml import KmlError, KmlFeature

CUSTOMER_CATEGORY = {'запретная_зона': 'prohibited', 'пост_ограничение': 'restricted', 'врем_ограничение': 'temporary'}
CATEGORIES = {'prohibited', 'restricted', 'temporary', 'danger', 'other'}
DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
POINT_OBSTACLE_MAX_M = 100
# In the customer files these modes carry sea-level heights (trees at ~180 m around Moscow),
# while relativeToGround carries heights above the obstacle's ground (towers at ~77 m).
AMSL_MODES = {'absolute', 'relativeToSeaFloor'}
SURVEY_SIMPLIFY_M = 1.0


class LocalMetres:
    def __init__(self, lon, lat):
        crs = CRS.from_proj4(f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m')
        self.forward = Transformer.from_crs(4326, crs, always_xy=True).transform
        self.inverse = Transformer.from_crs(crs, 4326, always_xy=True).transform


def _polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if geometry.geom_type in ('MultiPolygon', 'GeometryCollection'):
        return [p for g in geometry.geoms for p in _polygons(g)]
    return []


def _repair(ring, holes):
    polygon = Polygon([p[:2] for p in ring], [[p[:2] for p in h] for h in holes])
    if polygon.is_valid:
        return [polygon], False
    return [p for p in _polygons(shapely.make_valid(polygon)) if p.area > 0], True


def _geojson(polygon):
    return {'type': 'Polygon', 'coordinates': [[list(p) for p in ring] for ring in mapping(polygon)['coordinates']]}


def _in_bbox(geometry, bbox):
    return bbox is None or geometry.intersects(box(*bbox))


def _unique(base, used):
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f'{base}-{n}', n + 1
    used.add(candidate)
    return candidate


def survey_areas(features: list[KmlFeature]) -> dict:
    raw = [(ring, holes) for f in features for ring, holes in f.polygons]
    if not raw:
        raise KmlError('В файле нет полигонов задания на съемку.')
    repaired, parts, collapsed = 0, [], 0
    for ring, holes in raw:
        pieces, fixed = _repair(ring, holes)
        repaired += fixed
        collapsed += not pieces
        parts.extend(pieces)
    if not parts:
        raise KmlError('Полигоны задания вырождены (нулевая площадь).')
    lons = [p.centroid.x for p in parts]; lats = [p.centroid.y for p in parts]
    local = LocalMetres(sum(lons) / len(lons), sum(lats) / len(lats))
    areas, dropped, vertices_in, vertices_out = [], 0, 0, 0
    for polygon in parts:
        metric = transform(local.forward, polygon)
        vertices_in += len(polygon.exterior.coords) + sum(len(r.coords) for r in polygon.interiors)
        simple = metric.simplify(SURVEY_SIMPLIFY_M, preserve_topology=True)
        if simple.area < 1:
            dropped += 1
            continue
        for piece in _polygons(shapely.make_valid(simple)):
            out = transform(local.inverse, piece)
            vertices_out += len(out.exterior.coords) + sum(len(r.coords) for r in out.interiors)
            areas.append({'geometry': _geojson(out), 'area_m2': piece.area})
    minx, miny, maxx, maxy = shapely.unary_union([transform(local.forward, p) for p in parts]).bounds
    return {'areas': areas, 'stats': {'features': len(features), 'polygons_read': len(raw), 'repaired': repaired,
            'collapsed_in_repair': collapsed, 'parts_after_repair': len(parts), 'dropped_degenerate': dropped, 'imported': len(areas), 'vertices_before': vertices_in, 'vertices_after': vertices_out,
            'area_ha': round(sum(a['area_m2'] for a in areas) / 1e4, 1), 'extent_km': [round((maxx - minx) / 1000, 1), round((maxy - miny) / 1000, 1)],
            'skipped_coordinates': sum(f.skipped_coordinates for f in features)}}


def _validity(attributes, category):
    kind = (attributes.get('validity') or '').strip().lower()
    periods, weekly = [], []
    for item in filter(None, (s.strip() for s in (attributes.get('periods') or '').split(';'))):
        start, _, end = item.partition('/')
        periods.append(TimeWindow(start=datetime.fromisoformat(start), end=datetime.fromisoformat(end)))
    for item in filter(None, (s.strip() for s in (attributes.get('weekly') or '').split(';'))):
        days, _, hours = item.rpartition(' ')
        start, _, end = hours.partition('-')
        selected = []
        for token in days.replace(' ', '').upper().split(','):
            first, _, last = token.partition('-')
            selected += DAYS[DAYS.index(first):DAYS.index(last) + 1] if last else [first]
        weekly.append(WeeklyWindow(days=selected, start=time.fromisoformat(start), end=time.fromisoformat(end)))
    if not kind:
        kind = 'scheduled' if (periods or weekly) else 'unknown' if category == 'temporary' else 'permanent'
    return Validity(kind=kind, periods=periods, weekly=weekly, timezone=attributes.get('timezone') or 'Europe/Moscow')


def restriction_zones(features: list[KmlFeature], bbox=None) -> dict:
    zones, used, repaired, outside, unparsed, marked, errors = [], set(), 0, 0, [], [], []
    for index, feature in enumerate(features):
        attributes = feature.attributes
        base = (attributes.get('zone_id') or attributes.get('Name') or feature.name or f'zone-{index + 1}').strip()[:90]
        category = (attributes.get('category') or '').strip().lower()
        if category not in CATEGORIES:
            category = CUSTOMER_CATEGORY.get((attributes.get('Type') or '').strip().lower(), 'other')
        lower = parse_compact_limit(attributes.get('lower', ''))
        upper = parse_compact_limit(attributes.get('upper', ''))
        if lower and upper:
            # Our own notation, possibly exported earlier: it carries the original text and parse state.
            parsed = dict(lower=lower, upper=upper, notes=attributes.get('notes', ''), parsed=attributes.get('parsed', '').lower() != 'false', flags=[])
            source_text = attributes.get('source_text') or f"{attributes.get('lower')} – {attributes.get('upper')}"
        else:
            result = parse_altitude_text(attributes.get('Altitudes') or feature.description)
            parsed = dict(lower=result.lower, upper=result.upper, notes=result.notes, parsed=result.parsed, flags=result.flags)
            source_text = attributes.get('Altitudes') or feature.description
        try:
            validity = _validity(attributes, category)
        except ValueError as error:
            errors.append(f'{base}: расписание не распознано ({error}); зона считается действующей всегда.')
            validity = Validity(kind='unknown')
        for ring, holes in feature.polygons:
            pieces, fixed = _repair(ring, holes)
            repaired += fixed
            for polygon in pieces:
                if not _in_bbox(polygon, bbox):
                    outside += 1
                    continue
                zone_id = _unique(base, used)
                try:
                    zone = RestrictionZone(id=zone_id, name=(attributes.get('Name') or feature.name or zone_id)[:200], category=category,
                        geometry=_geojson(polygon), lower=parsed['lower'], upper=parsed['upper'], validity=validity,
                        notes=parsed['notes'][:4000], source_text=(source_text or '')[:4000], parsed=parsed['parsed'])
                except ValueError as error:
                    errors.append(f'{zone_id}: зона пропущена — {str(error).splitlines()[0]}')
                    continue
                if not parsed['parsed']: unparsed.append(zone_id)
                if 'source_marked_invalid_geometry' in parsed['flags']: marked.append(zone_id)
                zones.append(zone.model_dump(mode='json'))
    warnings = list(errors)
    if unparsed:
        warnings.append(f'Высоты не распознаны у {len(unparsed)} зон ({", ".join(unparsed[:8])}{"…" if len(unparsed) > 8 else ""}): они учитываются на всех высотах.')
    if marked:
        warnings.append(f'Источник помечает геометрию как некорректную у {len(marked)} зон: {", ".join(marked[:8])}.')
    unknown_time = sum(1 for z in zones if z['validity']['kind'] == 'unknown')
    if unknown_time:
        warnings.append(f'У {unknown_time} временных зон нет времени действия: пока его не задать, они считаются действующими.')
    return {'zones': zones, 'warnings': warnings, 'stats': {'features': len(features), 'imported': len(zones), 'repaired': repaired,
            'outside_area': outside, 'altitude_unparsed': len(unparsed), 'time_unknown': unknown_time,
            'skipped_coordinates': sum(f.skipped_coordinates for f in features)}}


def obstacles(features: list[KmlFeature], bbox=None) -> dict:
    result, used, dropped, outside, modes, invalid = [], set(), 0, 0, {}, 0
    references = {'AGL': 0, 'AMSL': 0}
    for index, feature in enumerate(features):
        heights = [p[2] for ring, holes in feature.polygons for p in ring] + [p[2] for line in feature.lines for p in line] + [p[2] for p in feature.points]
        height = max(heights, default=0.0)
        if height <= 0 or not (feature.polygons or feature.lines or feature.points):
            dropped += 1
            continue
        modes[feature.altitude_mode or 'не указан'] = modes.get(feature.altitude_mode or 'не указан', 0) + 1
        declared = (feature.attributes.get('height_reference') or '').strip().upper()
        reference = declared if declared in ('AGL', 'AMSL') else 'AMSL' if feature.altitude_mode.strip() in AMSL_MODES else 'AGL'
        code, _, kind = feature.name.partition(' ')
        kind = kind.split(':')[-1].strip() if kind else ''
        base = (code or f'obstacle-{index + 1}')[:90]
        shapes = []
        for ring, holes in feature.polygons:
            pieces, _ = _repair(ring, holes)
            shapes += pieces
        shapes += [LineString([p[:2] for p in line]) for line in feature.lines]
        shapes += [Point(p[:2]) for p in feature.points]
        for geometry in shapes:
            if not _in_bbox(geometry, bbox):
                outside += 1
                continue
            radius = 0.0
            if geometry.geom_type == 'Polygon':
                c = geometry.centroid
                scale = (111320 * math.cos(math.radians(c.y)), 110540)
                radius = max(math.hypot((x - c.x) * scale[0], (y - c.y) * scale[1]) for x, y in geometry.exterior.coords)
                if radius <= POINT_OBSTACLE_MAX_M:
                    geojson = {'type': 'Point', 'coordinates': [c.x, c.y]}
                else:
                    geojson, radius = _geojson(geometry), 0.0
            elif geometry.geom_type == 'LineString':
                geojson = {'type': 'LineString', 'coordinates': [list(p) for p in geometry.coords]}
            else:
                geojson = {'type': 'Point', 'coordinates': [geometry.x, geometry.y]}
            try:
                result.append(Obstacle(id=_unique(base, used), name=feature.name[:200], kind=kind[:100], geometry=geojson,
                                       radius_m=round(radius, 1), height_m=round(min(height, 9000), 1), height_reference=reference).model_dump(mode='json'))
                references[reference] += 1
            except ValueError:
                invalid += 1
    warnings = [f'Пропущено {invalid} препятствий с некорректной геометрией.'] if invalid else []
    if references['AMSL']:
        warnings.append(f"{references['AMSL']} препятствий заданы в режиме absolute/relativeToSeaFloor: их высоты — над уровнем моря. "
                        'При расчете они пересчитываются в высоту над землей с минимальной высотой рельефа района, остальные считаются высотой над землей.')
    return {'obstacles': result, 'warnings': warnings, 'stats': {'features': len(features), 'imported': len(result), 'dropped_without_height': dropped, 'invalid': invalid,
            'outside_area': outside, 'altitude_modes': modes, 'height_reference': references, 'skipped_coordinates': sum(f.skipped_coordinates for f in features)}}
