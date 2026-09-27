from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from bas.main import create_app
from bas.domain.models import Scenario
from bas.infrastructure.weather import WeatherService, WeatherUnavailable
from bas.planning.geometry import PlanningError
from bas.planning.service import plan


def sample():
    return json.loads((Path(__file__).parents[2]/'contracts/scenario.example.json').read_text(encoding='utf-8'))


def provider_data():
    return dict(current_units=dict(wind_speed_10m='m/s',wind_gusts_10m='m/s',wind_direction_10m='°',temperature_2m='°C',precipitation='mm'),
        current=dict(time=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),wind_speed_10m=3,wind_direction_10m=270,wind_gusts_10m=5,temperature_2m=20,precipitation=0))


def test_user_profile_persists_and_drives_budget(tmp_path):
    url=f"sqlite:///{(tmp_path/'db.sqlite').as_posix()}"
    with TestClient(create_app(url)) as client:
        response=client.post('/api/catalog/aircraft',json=dict(name='Test copter',airframe='multirotor',max_flight_time_min=80,max_wind_mps=7,max_horizontal_speed_mps=12,source='Test manual'))
        assert response.status_code==201,response.text
        profile=response.json()
        data=sample(); data['fleet']=data['fleet'][:1]; data['fleet'][0]['profile_id']=profile['id']
        stored=client.post('/api/scenarios',json=data).json()
        assert stored['scenario']['aircraft_profiles']==[profile]
        result=client.post('/api/planning-runs',json={'scenario_id':stored['id']}).json()
        assert result['missions'][0]['budget_s']==80*60*.75*.75
        assert result['aircraft_profiles']==[profile]
        # Snapshot remains portable, independent of the original catalog database.
        assert plan(Scenario.model_validate(stored['scenario']))['missions']
    with TestClient(create_app(url)) as client:
        assert profile in client.get('/api/catalog/aircraft').json()


@pytest.mark.parametrize('field,value',[('max_flight_time_min',0),('max_wind_mps',-1),('max_horizontal_speed_mps',0),('airframe','magic')])
def test_invalid_profile(tmp_path,field,value):
    profile=dict(name='Bad',airframe='multirotor',max_flight_time_min=30,max_wind_mps=8,max_horizontal_speed_mps=12,source='Test')
    profile[field]=value
    with TestClient(create_app(f"sqlite:///{tmp_path/'db.sqlite'}")) as client:
        assert client.post('/api/catalog/aircraft',json=profile).status_code==422


@pytest.mark.parametrize('field,value,code',[('max_wind_mps',2,'WIND_LIMIT'),('max_horizontal_speed_mps',5,'SPEED_LIMIT'),('airframe','fixed_wing','SPEED_LIMIT')])
def test_profile_limits_apply(field,value,code):
    data=sample();data['fleet']=data['fleet'][:1];data['fleet'][0]['profile_id']='custom_test'
    profile=dict(id='custom_test',name='Test',airframe='multirotor',max_flight_time_min=40,max_wind_mps=10,max_horizontal_speed_mps=15,source='Test')
    profile[field]=value;data['aircraft_profiles']=[profile]
    with pytest.raises(PlanningError) as error: plan(Scenario.model_validate(data))
    assert error.value.code==code


def test_unknown_profile_rejected_on_save(tmp_path):
    data=sample();data['fleet'][0]['profile_id']='unknown'
    with TestClient(create_app(f"sqlite:///{tmp_path/'db.sqlite'}")) as client:
        response=client.post('/api/scenarios',json=data)
        assert response.status_code==422
        assert response.json()['detail']['code']=='PROFILE_UNKNOWN'


def test_weather_cache_and_units():
    calls=[]
    def fetch(lat,lon): calls.append((lat,lon));return provider_data()
    service=WeatherService(fetch)
    first=service.current(55.80,37.30)
    second=service.current(55.80,37.30)
    assert first==second and len(calls)==1
    assert first.coordinates==(37.30,55.80)
    assert first.wind_height_m==10 and first.wind_speed_mps==3
    service.current(56,38)
    assert len(calls)==2


@pytest.mark.parametrize('failure',['units','null','old','future','network'])
def test_weather_rejects_bad_upstream(failure):
    def fetch(*_):
        data=provider_data()
        if failure=='units': data['current_units']['wind_speed_10m']='km/h'
        if failure=='null': data['current']['wind_speed_10m']=None
        if failure=='old': data['current']['time']=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        if failure=='future': data['current']['time']=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
        if failure=='network': raise TimeoutError()
        return data
    with pytest.raises(WeatherUnavailable): WeatherService(fetch).current(55,37)


@pytest.mark.parametrize('change,code',[('gusts','WIND_LIMIT'),('old','WEATHER_STALE'),('place','WEATHER_LOCATION')])
def test_weather_constraints(change,code):
    observation=WeatherService(lambda *_:provider_data()).current(55.8025,37.304).model_dump(mode='json')
    if change=='gusts': observation['wind_gusts_mps']=11
    if change=='old': observation['valid_at']=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    if change=='place': observation['coordinates']=[0,0]
    data=sample();data['weather']['observation']=observation
    with pytest.raises(PlanningError) as error: plan(Scenario.model_validate(data))
    assert error.value.code==code


def test_weather_api_and_export(tmp_path):
    weather=WeatherService(lambda *_:provider_data())
    with TestClient(create_app(f"sqlite:///{tmp_path/'db.sqlite'}",weather)) as client:
        assert client.get('/api/weather/current?latitude=91&longitude=0').status_code==422
        observation=client.get('/api/weather/current?latitude=55.8025&longitude=37.304').json()
        data=sample();data['weather']['observation']=observation
        stored=client.post('/api/scenarios',json=data).json()
        result=client.post('/api/planning-runs',json={'scenario_id':stored['id']}).json()
        exported=client.get(f"/api/plans/{result['id']}/export").json()
        assert exported['weather']['observation']==observation
        assert exported['aircraft_profiles']==stored['scenario']['aircraft_profiles']
        data['weather']['wind_speed_mps']=4
        assert client.post('/api/scenarios',json=data).status_code==422


def test_weather_outage_is_explicit(tmp_path):
    def failing(*_): raise TimeoutError()
    with TestClient(create_app(f"sqlite:///{tmp_path/'db.sqlite'}",WeatherService(failing))) as client:
        assert client.get('/api/weather/current?latitude=55&longitude=37').status_code==503
