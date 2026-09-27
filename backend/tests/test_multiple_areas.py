import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from bas.domain.models import Scenario
from bas.main import create_app
from bas.planning.geometry import LocalGeometry, PlanningError
from bas.planning.service import plan
from test_planning import ready, rect


def split_scenario(ready):
    ready['survey_area']=rect(37.30,55.80,.002,.003)
    ready['additional_survey_areas']=[rect(37.306,55.80,.002,.003)]
    ready['no_fly_zones']=[rect(37.303,55.7995,.001,.004)]
    return Scenario.model_validate(ready)


@pytest.mark.parametrize('objective',['min_completion_time','min_total_airtime'])
def test_separated_zones_have_coverage_and_do_not_bridge_obstacle(ready,objective):
    ready['objective']=objective
    scenario=split_scenario(ready)
    geometry=LocalGeometry(scenario)
    result=plan(scenario)
    assert result['metrics']['survey_area_count']==2
    assert all(a['coverage_percent']>=99.9 for a in result['metrics']['coverage_by_area'])
    strips=[]
    for mission in result['missions']:
        assert mission['flight_time_s']<=mission['budget_s']
        for leg in mission['legs']:
            points=[geometry.forward(*p) for p in leg['coordinates']]
            assert geometry.free.buffer(.001).covers(Point(points[0]) if points[0]==points[-1] else LineString(points))
            if leg['phase']=='survey':
                line=LineString(points)
                # No survey strip across the empty space between zones.
                assert any(zone.buffer(.1).covers(line) for zone in geometry.areas)
                strips.append(line.buffer(scenario.settings.swath_width_m/2,cap_style=3))
    footprint=unary_union(strips)
    assert all(zone.intersection(footprint).area/zone.area>=.999 for zone in geometry.areas)


def test_duplicate_zones_do_not_double_area_or_work(ready):
    single=plan(Scenario.model_validate(ready))
    ready['additional_survey_areas']=[ready['survey_area']]
    double=plan(Scenario.model_validate(ready))
    assert double['metrics']['area_m2']==pytest.approx(single['metrics']['area_m2'])
    assert double['metrics']['total_distance_m']==pytest.approx(single['metrics']['total_distance_m'])
    assert len(double['metrics']['coverage_by_area'])==2


def test_overlapping_zones_report_union_area(ready):
    ready['additional_survey_areas']=[rect(37.304,55.80,.006,.005)]
    scenario=Scenario.model_validate(ready)
    geometry=LocalGeometry(scenario)
    result=plan(scenario)
    assert result['metrics']['area_m2']==pytest.approx(unary_union(geometry.areas).area)
    assert result['metrics']['area_m2']<sum(zone.area for zone in geometry.areas)


def test_invalid_additional_zone_is_not_silently_dropped(ready):
    ready['additional_survey_areas']=[rect(37.32,55.80,.002,.003)]
    with pytest.raises(PlanningError) as error:
        plan(Scenario.model_validate(ready))
    assert error.value.code=='AREA_BLOCKED'


def test_multiple_zones_survive_restart_and_export(tmp_path,ready):
    scenario=split_scenario(ready)
    url=f"sqlite:///{(tmp_path/'zones.db').as_posix()}"
    with TestClient(create_app(url)) as client:
        stored=client.post('/api/scenarios',json=scenario.model_dump(mode='json')).json()
        response=client.post('/api/planning-runs',json={'scenario_id':stored['id']})
        assert response.status_code==200
        result=response.json()
    with TestClient(create_app(url)) as client:
        assert client.get(f"/api/scenarios/{stored['id']}").json()==stored
        assert client.get(f"/api/plans/{result['id']}").json()==result
        exported=client.get(f"/api/plans/{result['id']}/export?format=geojson").json()
        assert exported['metrics']['survey_area_count']==2
        assert len(exported['metrics']['coverage_by_area'])==2
        assert 'Зона 2' in client.get(f"/api/plans/{result['id']}/report").text
        assert 'coverage_by_area' in client.get(f"/api/plans/{result['id']}/export?format=kml").text


def test_zone_limit(ready):
    ready['additional_survey_areas']=[ready['survey_area']]*200
    with pytest.raises(ValueError): Scenario.model_validate(ready)
