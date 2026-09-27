import copy
import json
from pathlib import Path
from xml.etree import ElementTree
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString
from bas.domain.models import Scenario
from bas.main import create_app
from bas.planning.geometry import LocalGeometry, PlanningError
from bas.planning.service import plan, mission_color, MAX_FLEET
from bas.planning.flight_models.multirotor import duration
from bas.planning.scheduling.conflicts import conflict


def rect(x,y,w,h):
    return {'type':'Polygon','coordinates':[[[x,y],[x+w,y],[x+w,y+h],[x,y+h],[x,y]]]}


@pytest.fixture
def ready():
    return dict(schema_version='0.1', name='Integration test', survey_area=rect(37.30,55.80,.008,.005),
        allowed_airspace=rect(37.294,55.796,.02,.014),no_fly_zones=[rect(37.300,55.797,.003,.001)],
        sites=[dict(id='west',name='West',kind='launch_landing',coordinates=[37.297,55.801]),dict(id='east',name='East',kind='launch_landing',coordinates=[37.311,55.804])],
        fleet=[dict(id='Gemini-01',profile_id='gemini',battery_percent=100,payload='rgb',start_site_id='west'),dict(id='801-01',profile_id='geoscan_801',battery_percent=90,payload='rgb',start_site_id='east')],
        weather=dict(wind_speed_mps=3,wind_from_deg=270),objective='min_completion_time')


@pytest.mark.parametrize('objective',['min_completion_time','min_total_airtime'])
def test_plan_physical_invariants(ready,objective):
    ready['objective']=objective
    scenario=Scenario.model_validate(ready); result=plan(scenario); geometry=LocalGeometry(scenario)
    assert result['metrics']['coverage_percent']>=99.9
    assert result['status']=='demonstration'
    assert result['metrics']['total_airtime_s']==pytest.approx(sum(m['flight_time_s'] for m in result['missions']))
    for mission in result['missions']:
        assert mission['flight_time_s']<=mission['budget_s']
        assert mission['legs'][0]['phase']=='takeoff'
        assert mission['legs'][-1]['phase']=='landing'
        assert mission['legs'][0]['coordinates'][0]==pytest.approx(mission['legs'][-1]['coordinates'][-1])
        previous=None
        for leg in mission['legs']:
            assert leg['end']>leg['start']
            if previous:
                assert leg['start']==pytest.approx(previous['end'])
                assert leg['coordinates'][0]==pytest.approx(previous['coordinates'][-1])
            previous=leg
            a,b=[tuple(geometry.forward(*p)) for p in leg['coordinates']]
            assert geometry.free.buffer(.001).covers(LineString([a,b]))
    timed=[]
    for mission in result['missions']:
        timed.append([dict(l,xy=[geometry.forward(*p) for p in l['coordinates']]) for l in mission['legs']])
    assert all(not conflict(a,b) for i,a in enumerate(timed) for b in timed[:i])


def test_wind_is_directional():
    assert duration((0,0),(1000,0),10,3,270)<duration((1000,0),(0,0),10,3,270)
    assert duration((0,0),(1000,0),10,0,0)==100


def test_objectives_compare_the_same_candidate_pool(ready):
    completion = plan(Scenario.model_validate(ready))['metrics']
    ready['objective'] = 'min_total_airtime'
    airtime = plan(Scenario.model_validate(ready))['metrics']
    assert completion['completion_time_s'] <= airtime['completion_time_s']
    assert airtime['total_airtime_s'] <= completion['total_airtime_s']


@pytest.mark.parametrize('field,value,code',[
 ('wind',11,'WIND_LIMIT'),('charge',20,'BATTERY_RESERVE'),('plane','geoscan_201','FIXED_WING_SITE'),('sensor','lidar','SURVEY_UNSUPPORTED'),('site','missing','SITE_ASSIGNMENT')])
def test_rejects_invalid_planning_conditions(ready,field,value,code):
    if field=='wind': ready['weather']['wind_speed_mps']=value
    elif field=='charge': ready['fleet'][0]['battery_percent']=value
    elif field=='plane': ready['fleet'][0]['profile_id']=value
    elif field=='sensor':
        ready['survey_type']=value
        ready['settings']={'coverage_mode':'camera'}
    else: ready['fleet'][0]['start_site_id']=value
    with pytest.raises(PlanningError) as error: plan(Scenario.model_validate(ready))
    assert error.value.code==code


@pytest.mark.parametrize('survey_type,keyword',[
    ('multispectral','Мультиспектральная'),('thermal','ИК'),('lidar','LiDAR'),('geophysical','Геофизическая')])
def test_non_rgb_survey_types_plan_with_manual_swath(ready,survey_type,keyword):
    ready['survey_type']=survey_type
    for aircraft in ready['fleet']: aircraft['payload']=survey_type
    result=plan(Scenario.model_validate(ready))
    assert result['metrics']['coverage_percent']>=99.9
    assert any(keyword in w for w in result['warnings'])


def test_payload_must_match_survey_type(ready):
    ready['survey_type']='lidar'
    ready['fleet'][0]['payload']='lidar'
    with pytest.raises(PlanningError) as error: plan(Scenario.model_validate(ready))
    assert error.value.code=='PAYLOAD_MISMATCH'


def test_mission_color_has_no_collisions_up_to_max_fleet():
    colors = [mission_color(i) for i in range(MAX_FLEET)]
    assert len(set(colors)) == len(colors)
    assert all(len(c) == 7 and c[0] == '#' for c in colors)


def test_fleet_beyond_max_is_rejected(ready):
    ready['fleet'] = [dict(ready['fleet'][0], id=f'G-{i:02d}') for i in range(MAX_FLEET + 1)]
    with pytest.raises(PlanningError) as error: plan(Scenario.model_validate(ready))
    assert error.value.code == 'FLEET_TOO_LARGE'


def test_more_than_five_aircraft_can_be_used_with_distinct_colors(ready):
    # A tight per-aircraft budget forces the heuristic to spread strips across
    # more aircraft than the old hard-coded 5-aircraft cap allowed.
    ready['fleet'] = [dict(id=f'G-{i:02d}', profile_id='gemini', battery_percent=43, payload='rgb',
                            start_site_id='west' if i % 2 == 0 else 'east') for i in range(10)]
    result = plan(Scenario.model_validate(ready))
    assert result['metrics']['aircraft_used'] > 5
    colors = [m['color'] for m in result['missions']]
    assert len(set(colors)) == len(colors)


def test_multispectral_example_scenario_plans_with_pollux():
    raw=json.loads((Path(__file__).parents[2]/'contracts/scenario.multispectral.example.json').read_text(encoding='utf-8'))
    result=plan(Scenario.model_validate(raw))
    assert result['metrics']['coverage_percent']>=99.9
    mission=result['missions'][0]
    assert mission['survey']['mode']=='camera'
    assert mission['survey']['camera']['id']=='geoscan_pollux_v1'


def test_no_fly_area_is_not_silently_removed(ready):
    ready['no_fly_zones']=[rect(37.302,55.802,.002,.002)]
    with pytest.raises(PlanningError) as error: plan(Scenario.model_validate(ready))
    assert error.value.code=='AREA_BLOCKED'


def test_no_reserve_sites_means_unchecked(ready):
    result = plan(Scenario.model_validate(ready))
    assert all(m['alternate'] == dict(checked=False, reachable=None, site_id=None, margin_s=None) for m in result['missions'])
    assert any('Резервные площадки не заданы' in w for w in result['warnings'])


def test_reachable_alternate_site_reports_positive_margin(ready):
    ready['sites'].append(dict(id='reserve', name='Reserve', kind='alternate', coordinates=[37.297, 55.7975]))
    result = plan(Scenario.model_validate(ready))
    for mission in result['missions']:
        alternate = mission['alternate']
        assert alternate['checked'] and alternate['reachable'] is True
        assert alternate['site_id'] == 'reserve'
        assert alternate['margin_s'] > 0
    assert any('Достижимость резервных площадок' in w for w in result['warnings'])
    assert not any('Ни одна резервная площадка' in w for w in result['warnings'])


def test_unreachable_alternate_site_is_flagged(ready):
    # Inside the scenario's no-fly rectangle: outside free space, so no path connects to it.
    ready['sites'].append(dict(id='blocked', name='Blocked', kind='alternate', coordinates=[37.3015, 55.7975]))
    result = plan(Scenario.model_validate(ready))
    for mission in result['missions']:
        alternate = mission['alternate']
        assert alternate['checked'] and alternate['reachable'] is False
        assert alternate['site_id'] is None
        assert alternate['margin_s'] is None
    assert any('Ни одна резервная площадка' in w for w in result['warnings'])


def test_transit_detours_around_obstacle(ready):
    ready['no_fly_zones']=[rect(37.298,55.798,.0006,.006)]
    geo=LocalGeometry(Scenario.model_validate(ready))
    start=tuple(geo.forward(37.297,55.801)); end=tuple(geo.forward(37.300,55.801))
    assert not geo.visible(start,end)
    path=geo.path(start,end)
    assert path and len(path)>2
    assert all(geo.visible(a,b) for a,b in zip(path,path[1:]))


def test_continuous_conflict_detects_midsegment_crossing():
    a=[dict(start=0,end=10,xy=[(-100,0),(100,0)])]
    b=[dict(start=0,end=10,xy=[(0,-100),(0,100)])]
    assert conflict(a,b)
    assert not conflict(a,[dict(start=11,end=21,xy=[(0,-100),(0,100)])])


def test_plan_persistence_and_exports(tmp_path,ready):
    url=f"sqlite:///{(tmp_path/'db.sqlite').as_posix()}"
    with TestClient(create_app(url)) as client:
        sid=client.post('/api/scenarios',json=ready).json()['id']
        response=client.post('/api/planning-runs',json={'scenario_id':sid})
        assert response.status_code==200,response.text
        pid=response.json()['id']
        assert client.get('/api/scenarios').json()[0]['id']==sid
    with TestClient(create_app(url)) as client:
        result=client.get(f'/api/plans/{pid}').json()
        exported=client.get(f'/api/plans/{pid}/export?format=geojson').json()
        assert exported['warnings']==result['warnings']
        lines=[f for f in exported['features'] if f['geometry']['type']=='LineString']
        assert len(lines)==sum(len(m['legs']) for m in result['missions'])
        assert all(len(p)==2 for f in lines for p in f['geometry']['coordinates'])
        assert all(f['properties']['altitude_reference']=='AGL' for f in lines)
        xml=ElementTree.fromstring(client.get(f'/api/plans/{pid}/export?format=kml').content)
        assert xml.findall('.//{http://www.opengis.net/kml/2.2}Placemark')
        aircraft=result['missions'][0]['aircraft_id']
        subset=client.get(f'/api/plans/{pid}/export',params={'aircraft_id':aircraft}).json()
        assert all(f['properties']['aircraft_id']==aircraft for f in subset['features'])
        assert client.get(f'/api/plans/{pid}/export?format=bad').status_code==422
