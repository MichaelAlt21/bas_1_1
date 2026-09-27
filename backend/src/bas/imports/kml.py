"""Tolerant KML reader for customer task, restriction-zone and obstacle layers.

Real files mix KML 2.2 and gx: elements, carry attributes both as Data and SchemaData,
and contain occasional broken coordinate tokens; those are skipped and counted, not fatal.
"""
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MAX_KML_BYTES = 25_000_000


class KmlError(ValueError):
    pass


@dataclass
class KmlFeature:
    name: str
    description: str
    attributes: dict[str, str]
    polygons: list[tuple[list[tuple[float, float, float]], list[list[tuple[float, float, float]]]]] = field(default_factory=list)
    lines: list[list[tuple[float, float, float]]] = field(default_factory=list)
    points: list[tuple[float, float, float]] = field(default_factory=list)
    altitude_mode: str = ''
    skipped_coordinates: int = 0


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _text(element):
    return ''.join(element.itertext()).strip() if element is not None else ''


def _child(element, name):
    return next((c for c in element if _local(c.tag) == name), None)


def _coordinates(element, feature):
    points = []
    for token in (element.text or '').split():
        parts = token.split(',')
        try:
            lon, lat = float(parts[0]), float(parts[1])
            alt = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
        except (ValueError, IndexError):
            feature.skipped_coordinates += 1
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90) or not math.isfinite(alt):
            feature.skipped_coordinates += 1
            continue
        points.append((lon, lat, alt))
    return points


def _ring(boundary, feature):
    coordinates = next((c for c in boundary.iter() if _local(c.tag) == 'coordinates'), None)
    return _coordinates(coordinates, feature) if coordinates is not None else []


def _geometries(element, feature):
    kind = _local(element.tag)
    if kind == 'Polygon':
        outer = next((b for b in element if _local(b.tag) == 'outerBoundaryIs'), None)
        ring = _ring(outer, feature) if outer is not None else []
        if len(ring) >= 3:
            holes = [h for h in (_ring(b, feature) for b in element if _local(b.tag) == 'innerBoundaryIs') if len(h) >= 3]
            feature.polygons.append((ring, holes))
    elif kind == 'LineString':
        coordinates = _child(element, 'coordinates')
        line = _coordinates(coordinates, feature) if coordinates is not None else []
        if len(line) >= 2:
            feature.lines.append(line)
    elif kind == 'Point':
        coordinates = _child(element, 'coordinates')
        point = _coordinates(coordinates, feature) if coordinates is not None else []
        if point:
            feature.points.append(point[0])
    elif kind == 'MultiGeometry':
        for child in element:
            _geometries(child, feature)


def read_kml(data: bytes) -> list[KmlFeature]:
    if len(data) > MAX_KML_BYTES:
        raise KmlError(f'Файл больше {MAX_KML_BYTES // 1_000_000} МБ.')
    # KML never needs entity declarations; refusing them rules out entity-expansion tricks.
    if b'<!ENTITY' in data:
        raise KmlError('Файл содержит объявления сущностей XML, KML их не использует.')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise KmlError(f'Файл не является корректным XML/KML: {error}.') from None
    if _local(root.tag) != 'kml':
        raise KmlError('Ожидается KML-документ с корневым элементом <kml>.')
    features = []
    for placemark in (e for e in root.iter() if _local(e.tag) == 'Placemark'):
        attributes = {}
        for item in placemark.iter():
            tag = _local(item.tag)
            if tag == 'Data' and item.get('name'):
                attributes[item.get('name')] = _text(_child(item, 'value'))
            elif tag == 'SimpleData' and item.get('name'):
                attributes[item.get('name')] = _text(item)
        feature = KmlFeature(name=_text(_child(placemark, 'name')), description=_text(_child(placemark, 'description')),
                             attributes=attributes)
        mode = next((m for m in placemark.iter() if _local(m.tag) == 'altitudeMode'), None)
        feature.altitude_mode = _text(mode)
        for child in placemark:
            _geometries(child, feature)
        features.append(feature)
    return features
