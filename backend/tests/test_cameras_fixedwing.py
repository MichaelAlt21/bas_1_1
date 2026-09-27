import json
import math
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString
from shapely.geometry import box
from types import SimpleNamespace
import random
from bas.catalog.cameras import camera_geometry
from bas.catalog.service import builtin_profiles
from bas.domain.models import AircraftInstance, Scenario, SurveySettings
from bas.main import create_app
from bas.planning.geometry import LocalGeometry, PlanningError
from bas.planning.flight_models import dubins
from bas.planning.flight_models.fixed_wing import bearing, connect
from bas.planning.service import plan
from bas.planning.scheduling.conflicts import conflict


def sample():
    return json.loads((Path(__file__).parents[2]/'contracts/scenario.fixedwing.example.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('pid,cid,width,gsd',[('geoscan_201','sony_rx1rm2_v1',102.5714286,1.292824),('gemini','geoscan_pf1b_v1',117.5,1.9583333)])
def test_camera_geometry_uses_sensor_and_real_focal_length(pid,cid,width,gsd):
    a=AircraftInstance(id='a',profile_id=pid,battery_percent=100,payload='rgb',camera_id=cid)
    p=next(p for p in builtin_profiles() if p.id==pid)
    result=camera_geometry(a,p,SurveySettings(coverage_mode='camera'))
    assert result['swath_width_m']==pytest.approx(width)
    assert result['gsd_cm']==pytest.approx(gsd,rel=1e-4)
    assert result['photo_step_m']==pytest.approx(result['footprint_length_m']*.3)


def test_umc_r10c_camera_geometry():
    a=AircraftInstance(id='a',profile_id='gemini',battery_percent=100,payload='rgb',camera_id='sony_umc_r10c_v1')
    p=next(p for p in builtin_profiles() if p.id=='gemini')
    result=camera_geometry(a,p,SurveySettings(coverage_mode='camera'))
    assert result['swath_width_m']==pytest.approx(116.)
    assert result['gsd_cm']==pytest.approx(2.1261,rel=1e-4)


def test_pollux_camera_geometry_matches_pixel_derived_sensor():
    a=AircraftInstance(id='a',profile_id='gemini',battery_percent=100,payload='multispectral',camera_id='geoscan_pollux_v1')
    p=next(p for p in builtin_profiles() if p.id=='gemini')
    result=camera_geometry(a,p,SurveySettings(coverage_mode='camera'))
    assert result['swath_width_m']==pytest.approx(62.1)
    assert result['gsd_cm']==pytest.approx(4.3125)


def test_camera_sensor_type_must_match_payload():
    a=AircraftInstance(id='a',profile_id='gemini',battery_percent=100,payload='thermal',camera_id='geoscan_pf1b_v1')
    p=next(p for p in builtin_profiles() if p.id=='gemini')
    with pytest.raises(PlanningError) as error:
        camera_geometry(a,p,SurveySettings(coverage_mode='camera'))
    assert error.value.code=='CAMERA_SENSOR_MISMATCH'


@pytest.mark.parametrize('start,end',[
    ((0,0,0),(1000,100,math.pi)),((0,0,math.pi/2),(10,30,-math.pi/2)),
    ((0,0,0),(900,0,0)),((0,0,-2),(450,-700,1.2)),
])
def test_dubins_paths_are_continuous_tangent_and_have_correct_radius(start,end):
    # Still air: every turn is a circle of the turn radius over the ground too.
    candidates=list(dubins.paths(start,end,150,22,(0.,0.)))
    assert len(candidates)>=2
    for _,raw in candidates:
        parts=[dict(phase=phase,xy=[(x,y) for x,y,_ in points]) for phase,points in raw]
        assert parts[0]['xy'][0]==pytest.approx(start[:2])
        assert parts[-1]['xy'][-1]==pytest.approx(end[:2])
        points=[]
        for i,part in enumerate(parts):
            if i: assert part['xy'][0]==pytest.approx(parts[i-1]['xy'][-1])
            points.extend(part['xy'][:-1])
            if part['phase']=='turn':
                # Circumradius through any three consecutive samples.
                for a,b,c in zip(part['xy'],part['xy'][1:],part['xy'][2:]):
                    twice=abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
                    assert math.dist(a,b)*math.dist(b,c)*math.dist(c,a)/(2*twice)==pytest.approx(150,rel=1e-5)
        points.append(parts[-1]['xy'][-1])
        difference=lambda a,b:abs((a-b+math.pi)%(2*math.pi)-math.pi)
        assert difference(bearing(points[0],points[1]),start[2])<.05
        assert difference(bearing(points[-2],points[-1]),end[2])<.05


@pytest.mark.parametrize('objective',['min_completion_time','min_total_airtime'])
def test_plane_missions_and_cameras_are_consistent(objective):
    data=sample();data['objective']=objective
    scenario=Scenario.model_validate(data);result=plan(scenario);geo=LocalGeometry(scenario)
    assert result['metrics']['coverage_percent']>=99.9
    assert any(m['profile_id']=='geoscan_201' for m in result['missions'])
    timed=[]
    for mission in result['missions']:
        assert mission['flight_time_s']<=mission['budget_s']
        assert mission['survey']['photo_count']>0
        previous=None;local=[]
        for leg in mission['legs']:
            assert leg['end']>leg['start']
            if previous:
                assert leg['start']==pytest.approx(previous['end'])
                assert leg['coordinates'][0]==pytest.approx(previous['coordinates'][-1])
                assert leg['altitude_start_m']==pytest.approx(previous['altitude_end_m'])
            a,b=[geo.forward(*p) for p in leg['coordinates']]
            assert geo.free.buffer(.001).covers(LineString([a,b]))
            local.append(dict(leg,xy=[a,b]));previous=leg
            if leg['phase']=='survey':
                assert leg['photo_step_m']<=mission['survey']['photo_step_m']+1e-7
                assert leg['photo_count']>=2
        if mission['airframe']=='fixed_wing':
            phases={l['phase'] for l in mission['legs']}
            assert {'turn','climb','parachute'}<=phases
            assert 'hold' not in phases
            assert all(math.dist(*l['xy'])>0 for l in local if l['phase'] in ('turn','survey','climb'))
            falling=next(l for l in local if l['phase']=='parachute')
            assert math.dist(*falling['xy'])==pytest.approx(60,abs=.001)
        timed.append(local)
    assert all(not conflict(a,b) for i,a in enumerate(timed) for b in timed[:i])


@pytest.mark.parametrize('mutation,code',[
    ('camera','CAMERA_INCOMPATIBLE'),('gsd','GSD_LIMIT'),('altitude','FIXED_WING_ALTITUDE'),
    ('speed','SPEED_LIMIT'),('trigger','NO_PLAN_FOUND'),('missing','CAMERA_REQUIRED'),
])
def test_rejects_unusable_camera_and_aircraft_settings(mutation,code):
    data=sample();data['fleet']=data['fleet'][:1]
    if mutation=='camera':data['fleet'][0]['camera_id']='geoscan_pf1b_v1'
    if mutation=='gsd':data['settings']['max_gsd_cm']=1
    if mutation=='altitude':data['settings']['altitude_m']=80
    if mutation=='speed':data['fleet'][0]['airspeed_mps']=10
    if mutation=='trigger':data['fleet'][0]['trigger_interval_s']=60
    if mutation=='missing':data['fleet'][0]['camera_id']=None
    with pytest.raises(PlanningError) as error:plan(Scenario.model_validate(data))
    assert error.value.code==code


def test_plane_export_and_reopen(tmp_path):
    url=f"sqlite:///{(tmp_path/'cameras.sqlite').as_posix()}"
    with TestClient(create_app(url)) as client:
        assert len(client.get('/api/catalog/cameras').json())==4
        sid=client.post('/api/scenarios',json=sample()).json()['id']
        response=client.post('/api/planning-runs',json={'scenario_id':sid})
        assert response.status_code==200,response.text
        result=response.json()
        assert result['missions'][0]['survey']['camera']['source']
        pid=result['id']
    with TestClient(create_app(url)) as client:
        assert client.get(f'/api/plans/{pid}').json()['missions']==result['missions']
        assert client.get(f'/api/plans/{pid}/export?format=kml').status_code==200
        exported=client.get(f'/api/plans/{pid}/export').json()
        assert any(f['properties'].get('phase')=='turn' for f in exported['features'])
        assert any(f['properties'].get('photo_count',0)>0 for f in exported['features'])
        report=client.get(f'/api/plans/{pid}/report').text
        assert 'Sony RX1R II' in report and 'Geoscan PF1B' in report
        assert 'Допущения самолетной модели' in report


def test_curve_cannot_fit_a_narrow_corridor_even_with_free_endpoints():
    geo=SimpleNamespace(free=box(-20,-10,1020,10))
    wind=SimpleNamespace(wind_speed_mps=0,wind_from_deg=0)
    assert connect((0,0,0),(1000,0,math.pi),150,geo,22,wind) is None


def test_conflict_sweep_matches_all_segment_pairs():
    rng=random.Random(20260921)
    def track():
        t=rng.uniform(0,10);result=[]
        for i in range(30):
            end=t+rng.uniform(.1,5)
            result.append(dict(start=t,end=end,xy=[(rng.uniform(-100,100),rng.uniform(-100,100)),(rng.uniform(-100,100),rng.uniform(-100,100))]))
            t=end
        return result
    for _ in range(30):
        a,b=track(),track()
        assert conflict(a,b)==any(conflict([x],[y]) for x in a for y in b)
