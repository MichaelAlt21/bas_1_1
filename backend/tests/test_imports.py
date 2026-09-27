from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bas.imports import layers
from bas.imports.altitudes import format_compact_limit, parse_altitude_text, parse_compact_limit
from bas.imports.kml import KmlError, read_kml
from bas.main import create_app

CUSTOMER = Path(__file__).parents[3] / 'additional_docs'


def placemark(name, geometry, data='', description='', mode=''):
    extended = f'<ExtendedData>{data}</ExtendedData>' if data else ''
    altitude = f'<altitudeMode>{mode}</altitudeMode>' if mode else ''
    geometry = geometry.replace('%MODE%', altitude)
    return f'<Placemark><name>{name}</name><description>{description}</description>{extended}{geometry}</Placemark>'


def polygon(x, y, w, h, z=0):
    ring = ' '.join(f'{a},{b},{z}' for a, b in [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)])
    return f'<Polygon>%MODE%<outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs></Polygon>'


def kml(*placemarks):
    return ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + ''.join(placemarks) + '</Document></kml>').encode('utf-8')


def data(**values):
    return ''.join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in values.items())


def test_reader_collects_attributes_and_skips_broken_coordinates():
    features = read_kml(kml(
        placemark('A', polygon(37.3, 55.8, .01, .01), data(Type='запретная_зона')),
        placemark('B', '<Point><coordinates>37.31,55.81,120 0.0, bad</coordinates></Point>'),
        '<Placemark><name>C</name><ExtendedData><SchemaData><SimpleData name="Altitudes">На всех высотах</SimpleData></SchemaData></ExtendedData>'
        '<MultiGeometry><LineString><coordinates>37.3,55.8,5 37.31,55.8,7</coordinates></LineString></MultiGeometry></Placemark>'))
    assert [f.name for f in features] == ['A', 'B', 'C']
    assert features[0].attributes == {'Type': 'запретная_зона'} and len(features[0].polygons) == 1
    assert features[1].points == [(37.31, 55.81, 120.0)] and features[1].skipped_coordinates == 2
    assert features[2].attributes['Altitudes'] == 'На всех высотах' and features[2].lines[0][1] == (37.31, 55.8, 7.0)


@pytest.mark.parametrize('payload', [b'<html/>', b'not xml', b'<!DOCTYPE kml [<!ENTITY x "y">]><kml/>', b'x' * 25_000_001],
                         ids=['html', 'text', 'entity', 'oversize'])
def test_reader_rejects_non_kml(payload):
    with pytest.raises(KmlError):
        read_kml(payload)


@pytest.mark.parametrize('text,lower,upper', [
    ('От земли до 500 м (1700 фут) AMSL', ('GND', 0), ('AMSL', 500)),
    ('Oт 400 м (1400 фyт) AMSL до FL200', ('AMSL', 400), ('FL', 200)),
    ('На всех высотах\nДействует круглосуточно', ('GND', 0), ('UNL', 0)),
    ('От 150 м AGL до 3000 м', ('AGL', 150), ('AGL', 3000)),
    ('От 1000 фут до UNL', ('AMSL', 304.8), ('UNL', 0)),
])
def test_altitude_text(text, lower, upper):
    parsed = parse_altitude_text(text)
    assert parsed.parsed
    assert (parsed.lower.reference, parsed.lower.value) == lower and (parsed.upper.reference, parsed.upper.value) == upper


def test_unrecognised_altitude_blocks_every_height_and_keeps_text():
    parsed = parse_altitude_text('некорректная геометрия! По описанию военных')
    assert not parsed.parsed and parsed.lower.reference == 'GND' and parsed.upper.reference == 'UNL'
    assert 'source_marked_invalid_geometry' in parsed.flags and 'военных' in parsed.notes


@pytest.mark.parametrize('text', ['GND', 'UNL', 'FL095', '120 AGL', '400 AMSL'])
def test_compact_limits_round_trip(text):
    assert format_compact_limit(parse_compact_limit(text)) == text


def test_zones_from_customer_and_own_attributes():
    features = read_kml(kml(
        placemark('UUR1', polygon(37.30, 55.80, .01, .01), data(Name='UUR1', Type='пост_ограничение', Altitudes='От земли до 300 м AMSL')),
        placemark('T1', polygon(37.32, 55.80, .01, .01), data(Type='врем_ограничение', Altitudes='От земли до FL100')),
        placemark('Own', polygon(37.34, 55.80, .01, .01), data(zone_id='own-1', category='danger', lower='120 AGL', upper='UNL',
                                                              weekly='MON-FRI 08:00-18:00; SAT 10:00-14:00')),
        placemark('Far', polygon(40.0, 60.0, .01, .01), data(Type='запретная_зона'))))
    result = layers.restriction_zones(features, bbox=(37.2, 55.7, 37.5, 55.9))
    zones = {z['id']: z for z in result['zones']}
    assert set(zones) == {'UUR1', 'T1', 'own-1'} and result['stats']['outside_area'] == 1
    assert zones['UUR1']['category'] == 'restricted' and zones['UUR1']['upper'] == {'reference': 'AMSL', 'value': 300}
    assert zones['T1']['category'] == 'temporary' and zones['T1']['validity']['kind'] == 'unknown'
    own = zones['own-1']
    assert own['category'] == 'danger' and own['lower'] == {'reference': 'AGL', 'value': 120}
    assert own['validity']['kind'] == 'scheduled' and own['validity']['weekly'][0]['days'] == ['MON', 'TUE', 'WED', 'THU', 'FRI']
    assert any('временных зон' in w for w in result['warnings'])


def test_obstacles_keep_the_height_reference_of_their_altitude_mode():
    features = read_kml(kml(
        placemark('UU1 OTHER:COMMUNICATION_TOWER', polygon(37.300, 55.80, .0002, .0002, 80), mode='relativeToGround'),
        placemark('UU2 OTHER:TREE', polygon(37.301, 55.80, .0002, .0002, 190), mode='relativeToSeaFloor'),
        placemark('UU3 OTHER:BUILDING', polygon(37.302, 55.80, .01, .01, 210), mode='absolute'),
        placemark('UU4 OTHER:FENCE', '<LineString><coordinates>37.3,55.8,12 37.31,55.8,12</coordinates></LineString>'),
        placemark('UU5 OTHER:NOTHING', polygon(37.303, 55.80, .0002, .0002, 0))))
    result = layers.obstacles(features)
    items = {o['id']: o for o in result['obstacles']}
    assert set(items) == {'UU1', 'UU2', 'UU3', 'UU4'} and result['stats']['dropped_without_height'] == 1
    assert items['UU1']['geometry']['type'] == 'Point' and 0 < items['UU1']['radius_m'] <= 100 and items['UU1']['height_reference'] == 'AGL'
    assert items['UU2']['height_reference'] == 'AMSL' and items['UU2']['kind'] == 'TREE'
    assert items['UU3']['geometry']['type'] == 'Polygon' and items['UU3']['radius_m'] == 0 and items['UU3']['height_reference'] == 'AMSL'
    assert items['UU4']['geometry']['type'] == 'LineString' and items['UU4']['height_reference'] == 'AGL'
    assert result['stats']['height_reference'] == {'AGL': 2, 'AMSL': 2}


def test_survey_import_repairs_and_reports():
    bow_tie = '<Polygon><outerBoundaryIs><LinearRing><coordinates>37.30,55.80 37.31,55.81 37.31,55.80 37.30,55.81 37.30,55.80</coordinates></LinearRing></outerBoundaryIs></Polygon>'
    result = layers.survey_areas(read_kml(kml(placemark('ok', polygon(37.32, 55.80, .01, .01)), placemark('bow', bow_tie))))
    stats = result['stats']
    assert stats['polygons_read'] == 2 and stats['repaired'] == 1 and stats['imported'] == 3
    assert stats['area_ha'] > 0 and all(a['geometry']['type'] == 'Polygon' for a in result['areas'])
    with pytest.raises(KmlError):
        layers.survey_areas(read_kml(kml(placemark('point', '<Point><coordinates>37.3,55.8</coordinates></Point>'))))


def test_import_endpoint(tmp_path):
    body = kml(placemark('UUR1', polygon(37.30, 55.80, .01, .01), data(Type='запретная_зона', Altitudes='На всех высотах')))
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'i.db').as_posix()}")) as client:
        response = client.post('/api/imports/kml?kind=zones&west=37&south=55&east=38&north=56', content=body)
        assert response.status_code == 200 and response.json()['kind'] == 'zones'
        assert response.json()['zones'][0]['category'] == 'prohibited'
        assert client.post('/api/imports/kml?kind=zones&west=38&south=55&east=37&north=56', content=body).status_code == 422
        assert client.post('/api/imports/kml?kind=survey', content=b'<html/>').status_code == 422
        assert client.post('/api/imports/kml?kind=other', content=body).status_code == 422


@pytest.mark.skipif(not CUSTOMER.is_dir(), reason='customer files are not in the repository')
def test_customer_files():
    survey = layers.survey_areas(read_kml((CUSTOMER / 'Границы полетов.kml').read_bytes()))
    assert survey['stats']['imported'] > 500 and 20000 < survey['stats']['area_ha'] < 30000
    zones = layers.restriction_zones(read_kml((CUSTOMER / 'Московская зона.kml').read_bytes()))
    assert zones['stats']['imported'] > 300 and zones['stats']['altitude_unparsed'] <= 10
    obstacles = layers.obstacles(read_kml((CUSTOMER / 'obstacles_Московская область.kml').read_bytes()))
    assert obstacles['stats']['height_reference']['AMSL'] > 2000 and obstacles['stats']['height_reference']['AGL'] > 2000


def test_zone_export_round_trip(tmp_path):
    body = kml(
        placemark('UUR1', polygon(37.30, 55.80, .01, .01), data(Type='запретная_зона', Altitudes='Oт 400 м (1400 фyт) AMSL до FL200')),
        placemark('T1', polygon(37.32, 55.80, .01, .01), data(zone_id='t-1', category='temporary', lower='GND', upper='150 AGL',
                                                              periods='2026-09-24T08:00:00+03:00/2026-09-24T12:00:00+03:00', weekly='MON-FRI 08:00-18:00')),
        placemark('X', polygon(37.34, 55.80, .01, .01), data(Altitudes='По описанию военных')))
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'z.db').as_posix()}")) as client:
        zones = client.post('/api/imports/kml?kind=zones', content=body).json()['zones']
        exported = client.post('/api/exports/zones-kml', json=zones)
        assert exported.status_code == 200 and b'<Data name="lower">' in exported.content
        again = client.post('/api/imports/kml?kind=zones', content=exported.content).json()['zones']
    assert again == zones
