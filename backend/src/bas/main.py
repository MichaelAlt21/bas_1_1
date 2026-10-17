import json
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, HTTPException, Response, Query, Header, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from bas.infrastructure.db_settings import DatabaseSettings, DatabaseRequest, read_config, verify_active_schema, SCHEMA
from sqlalchemy import text

# Windows' registry-backed mimetypes can report text/plain for .js, which browsers
# refuse to execute as a module script. Force the correct type before any static mount.
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/javascript', '.mjs')
mimetypes.add_type('image/svg+xml', '.svg')

from bas.domain.models import AircraftProfile, AircraftProfileInput, PlanningRequest, RestrictionZone, Scenario, SensorProfile, SensorProfileInput, StoredScenario, TaskEstimateRequest, WeatherObservation
from bas.catalog import costs, sensor_service
from bas.planning.diagnostics import from_error, make as diagnostic, navigate
import logging
import re
from bas.catalog.service import builtin_profiles, resolve_profiles
from bas.catalog.cameras import CAMERAS
from bas.infrastructure.database import list_profiles, save_profile
from bas.infrastructure.weather import WeatherService, WeatherUnavailable
from bas.infrastructure.database import get_scenario, make_engine, metadata, save_scenario, list_scenarios, save_plan, get_plan, list_plans
from bas.imports import layers
from bas.imports.kml import MAX_KML_BYTES, KmlError, read_kml
from bas.planning.analysis import fleet_analysis
from bas.planning.estimate import task_estimate
from bas.planning.service import plan
from bas.planning.geometry import PlanningError
from bas.planning.sun import sun_times
from bas.export.formats import geojson, kml, zones_kml
from bas.export.report import mission_report
from datetime import date
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing import Literal

VERSION = '1.2.0'


class UnicodeJSONResponse(JSONResponse):
    # JSON is UTF-8 by definition, but Windows PowerShell 5.1 decodes a body without a charset as
    # Latin-1 and garbles every Russian message: state it explicitly.
    media_type = 'application/json; charset=utf-8'


def create_app(database_url: str | None = None, weather_service=None, database_settings=None, static_dir: Path | None = None) -> FastAPI:
    engine = make_engine(database_url)
    weather_service = weather_service or WeatherService()
    database_settings = database_settings or (DatabaseSettings() if database_url is None else None)

    @asynccontextmanager
    async def lifespan(app):
        if engine.dialect.name=='postgresql':
            verify_active_schema(engine,metadata)
        else:
            metadata.create_all(engine)
        if database_settings: database_settings.initialize_admin()
        yield
        engine.dispose()

    app = FastAPI(title="KUMOsystems Planning API", version=VERSION, lifespan=lifespan, default_response_class=UnicodeJSONResponse)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's raw `input` can contain connection secrets; never echo it.
        errors = [{'loc':e['loc'],'msg':_russian(e),'type':e['type']} for e in error.errors()]
        return UnicodeJSONResponse(status_code=422,content={'detail':errors,'diagnostics':[_input_diagnostic(e) for e in errors[:20]]})

    @app.exception_handler(Exception)
    async def internal_error(request, error):
        # A fault of the service, not of the scenario: logged in full, answered without internals.
        reference = uuid4().hex[:8]
        logging.getLogger('bas').exception('Внутренняя ошибка %s при %s %s', reference, request.method, request.url.path)
        message = f'Внутренняя ошибка сервиса (код {reference}). Введенные данные сохранены в интерфейсе; повторите действие или сообщите код разработчикам.'
        return UnicodeJSONResponse(status_code=500, content={'detail': {'code': 'INTERNAL', 'message': message, 'diagnostics': [diagnostic('INTERNAL', message)]}})

    def planning_failure(error, scenario=None):
        return HTTPException(422, {'code': error.code, 'message': str(error), 'diagnostics': from_error(error, scenario)})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        return UnicodeJSONResponse(status_code=error.status_code, content={'detail': error.detail}, headers=getattr(error, 'headers', None))

    def admin(x_kumo_admin_token: str | None=Header(default=None)):
        if database_settings is None or not database_settings.authorized(x_kumo_admin_token):
            raise HTTPException(403,'Нужен административный токен сервера. Он не сохраняется браузером.')

    @app.get('/api/settings/database', dependencies=[Depends(admin)])
    def database_status():
        pending=read_config(database_settings.directory)
        url=engine.url
        active={'driver':engine.dialect.name,'host':url.host,'port':url.port,'database':url.database,'username':url.username,
                'schema':SCHEMA if engine.dialect.name=='postgresql' else None}
        return {'active':active,'saved':pending.model_dump() if pending else None,'overridden_by_env':bool(os.getenv('BAS_DATABASE_URL')),
                'password_environment_ready':bool(pending and os.getenv(pending.password_env))}

    def database_action(request, action):
        try:
            return action(request,metadata)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None
        except OSError:
            raise HTTPException(503,'Не удалось сохранить настройки сервера. Проверьте права на каталог настроек.') from None

    @app.post('/api/settings/database/check',dependencies=[Depends(admin)])
    def database_check(request: DatabaseRequest):
        return database_action(request,database_settings.probe)

    @app.post('/api/settings/database/prepare',dependencies=[Depends(admin)])
    def database_prepare(request: DatabaseRequest):
        return database_action(request,lambda req,meta:database_settings.probe(req,meta,prepare=True))

    @app.post('/api/settings/database/save',dependencies=[Depends(admin)])
    def database_save(request: DatabaseRequest):
        return database_action(request,database_settings.save)

    @app.post('/api/settings/database/local',dependencies=[Depends(admin)])
    def database_local():
        return database_action(None,lambda *_:database_settings.reset())

    @app.get("/api/health")
    def health():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "version": VERSION, "planner_ready": True, "planner_mode": "demonstration_mixed_fleet"}

    @app.get('/api/catalog/cameras')
    def cameras():
        return CAMERAS

    def sensor_failure(error):
        return HTTPException(error.status, {'code': 'SENSOR_PROFILE', 'message': str(error), 'conflicts': error.conflicts})

    @app.get('/api/catalog/sensors', response_model=list[SensorProfile])
    def sensors():
        return sensor_service.catalog(engine)

    @app.get('/api/catalog/sensors/export')
    def export_sensors(ids: str | None = Query(None, max_length=5000)):
        chosen = sensor_service.catalog(engine)
        if ids:
            wanted = set(ids.split(','))
            chosen = [p for p in chosen if p.id in wanted]
        return Response(json.dumps(sensor_service.export_document(chosen), ensure_ascii=False, indent=1), media_type='application/json; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="kumo-sensor-profiles.json"'})

    @app.get('/api/catalog/sensors/{sensor_id}/versions', response_model=list[SensorProfile])
    def sensor_versions(sensor_id: str):
        found = sensor_service.versions(engine, sensor_id)
        if not found: raise HTTPException(404, 'Профиль не найден')
        return found

    @app.post('/api/catalog/sensors', response_model=SensorProfile, status_code=201)
    def create_sensor(profile: SensorProfileInput, sensor_id: str | None = Query(None, alias='id', pattern=r'^[a-zA-Z0-9_.:-]{1,100}$')):
        try:
            return sensor_service.create(engine, profile, sensor_id)
        except sensor_service.SensorCatalogError as error:
            raise sensor_failure(error) from None

    @app.put('/api/catalog/sensors/{sensor_id}', response_model=SensorProfile)
    def update_sensor(sensor_id: str, profile: SensorProfileInput):
        try:
            return sensor_service.update(engine, sensor_id, profile)
        except sensor_service.SensorCatalogError as error:
            raise sensor_failure(error) from None

    @app.post('/api/catalog/sensors/import')
    def import_sensors(document: dict = Body(...), on_conflict: Literal['reject', 'new_version', 'rename'] = Query('reject')):
        try:
            return sensor_service.import_document(engine, document, on_conflict)
        except sensor_service.SensorCatalogError as error:
            raise sensor_failure(error) from None

    @app.get("/api/catalog/aircraft", response_model=list[AircraftProfile])
    def aircraft():
        return builtin_profiles()+list_profiles(engine)

    @app.get('/api/catalog/cost-preset')
    def cost_preset():
        # Coefficients of J from open data with the source and status of every input.
        return costs.document(aircraft())

    @app.post('/api/catalog/aircraft', response_model=AircraftProfile, status_code=201)
    def create_profile(profile: AircraftProfileInput):
        return save_profile(engine, AircraftProfile(**profile.model_dump(), id='custom_'+uuid4().hex))

    @app.get('/api/weather/current', response_model=WeatherObservation)
    def current_weather(latitude: float=Query(ge=-90,le=90,allow_inf_nan=False), longitude: float=Query(ge=-180,le=180,allow_inf_nan=False)):
        try:
            return weather_service.current(latitude, longitude)
        except WeatherUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.post("/api/scenarios", response_model=StoredScenario, status_code=201)
    def create_scenario(scenario: Scenario):
        try:
            profiles = resolve_profiles(scenario, aircraft())
            scenario = scenario.model_copy(update={'aircraft_profiles': list(profiles.values()),
                                                   'sensor_profiles': sensor_service.snapshot(engine, scenario)})
        except PlanningError as error:
            raise planning_failure(error, scenario) from error
        return save_scenario(engine, scenario)

    @app.get('/api/scenarios')
    def scenario_list():
        return list_scenarios(engine)

    @app.get("/api/scenarios/{scenario_id}", response_model=StoredScenario)
    def read_scenario(scenario_id: UUID):
        record = get_scenario(engine, str(scenario_id))
        if record is None:
            raise HTTPException(404, "Сценарий не найден")
        return record

    @app.post("/api/planning-runs")
    def planning_run(request: PlanningRequest):
        record = get_scenario(engine, str(request.scenario_id))
        if record is None:
            raise HTTPException(404, "Сценарий не найден")
        try:
            return save_plan(engine, request.scenario_id, plan(record.scenario))
        except PlanningError as error:
            raise planning_failure(error, record.scenario) from error

    @app.post('/api/scenarios/{scenario_id}/fleet-analysis')
    def scenario_fleet_analysis(scenario_id: UUID):
        return fleet_analysis(read_scenario(scenario_id).scenario)

    @app.post('/api/imports/kml')
    async def import_kml(request: Request, kind: Literal['survey', 'zones', 'obstacles'] = Query(),
                         west: float | None = Query(None, ge=-180, le=180, allow_inf_nan=False), south: float | None = Query(None, ge=-90, le=90, allow_inf_nan=False),
                         east: float | None = Query(None, ge=-180, le=180, allow_inf_nan=False), north: float | None = Query(None, ge=-90, le=90, allow_inf_nan=False)):
        # Raw KML body, not multipart: the client posts the file as is.
        declared = request.headers.get('content-length')
        if declared and declared.isdigit() and int(declared) > MAX_KML_BYTES:
            raise HTTPException(413, f'Файл больше {MAX_KML_BYTES // 1_000_000} МБ.')
        data = await request.body()
        bounds = (west, south, east, north)
        if any(v is not None for v in bounds) and (None in bounds or west >= east or south >= north):
            raise HTTPException(422, 'Рамка отбора задается четырьмя границами: west < east, south < north.')
        bbox = None if west is None else bounds

        def convert():
            features = read_kml(data)
            if kind == 'survey': return layers.survey_areas(features)
            if kind == 'zones': return layers.restriction_zones(features, bbox)
            return layers.obstacles(features, bbox)
        try:
            result = await run_in_threadpool(convert)
        except KmlError as error:
            raise HTTPException(422, str(error)) from None
        return dict(result, kind=kind)

    @app.post('/api/exports/zones-kml')
    def export_zones(zones: list[RestrictionZone] = Body(max_length=2000)):
        return Response(zones_kml(zones), media_type='application/vnd.google-earth.kml+xml',
                        headers={'Content-Disposition': 'attachment; filename="kumo-zones.kml"'})

    @app.post('/api/task-estimate')
    def estimate(request: TaskEstimateRequest):
        profiles = {p.id: p for p in aircraft()}
        profiles.update({p.id: p for p in request.aircraft_profiles})
        if request.profile_id not in profiles:
            raise HTTPException(422, f'Профиль {request.profile_id} не найден.')
        try:
            return task_estimate(request, profiles[request.profile_id])
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get('/api/sun')
    def sun(latitude: float=Query(ge=-90,le=90,allow_inf_nan=False), longitude: float=Query(ge=-180,le=180,allow_inf_nan=False), day: date=Query(alias='date')):
        times = sun_times(latitude, longitude, day)
        civil = sun_times(latitude, longitude, day, depression_deg=6)
        iso = lambda v: v.isoformat() if v else None
        return dict(date=day.isoformat(), sunrise=iso(times['sunrise']), sunset=iso(times['sunset']), solar_noon=iso(times['solar_noon']),
                    civil_dawn=iso(civil['sunrise']), civil_dusk=iso(civil['sunset']), polar=times['polar'])

    @app.get('/api/plans')
    def plan_list(limit: int=Query(12,ge=1,le=50), offset: int=Query(0,ge=0,le=1_000_000)):
        return list_plans(engine,limit,offset)

    @app.get('/api/plans/{plan_id}')
    def read_plan(plan_id: UUID):
        result=get_plan(engine,plan_id)
        if result is None: raise HTTPException(404,'План не найден')
        return result

    @app.get('/api/plans/{plan_id}/report')
    def report_plan(plan_id: UUID):
        result = read_plan(plan_id)
        source = get_scenario(engine,result['scenario_id'])
        if source is None: raise HTTPException(404,'Исходный сценарий не найден')
        return Response(mission_report(result,source),media_type='text/html',headers={
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store'})

    @app.get('/api/plans/{plan_id}/export')
    def export_plan(plan_id: UUID, format: Literal['geojson','kml']=Query('geojson'), aircraft_id: str | None=None):
        result=read_plan(plan_id)
        if aircraft_id:
            result=dict(result,missions=[m for m in result['missions'] if m['aircraft_id']==aircraft_id])
            if not result['missions']: raise HTTPException(404,'Миссия аппарата не найдена')
        content=json.dumps(geojson(result),ensure_ascii=False) if format=='geojson' else kml(result)
        return Response(content,media_type='application/geo+json' if format=='geojson' else 'application/vnd.google-earth.kml+xml',
            headers={'Content-Disposition':f'attachment; filename="bas-demo-{plan_id}.{format}"'})

    # Built frontend, if present: lets one process serve the whole app for a
    # single-launcher Windows demo instead of requiring a separate Vite dev server.
    # BAS_STATIC_DIR points the standalone Windows build at its bundled interface.
    if static_dir is None:
        static_dir = Path(os.getenv('BAS_STATIC_DIR') or Path(__file__).resolve().parents[3] / 'frontend' / 'dist')
    if static_dir.is_dir():
        app.mount('/', StaticFiles(directory=static_dir, html=True), name='frontend')

    return app


FIELD_TARGETS = [
    (('cost_model',), ('settings', 'cost', 'cost')),
    (('requirements',), ('settings', 'requirements', None)),
    (('fleet',), ('fleet', 'fleet', None)),
    (('weather',), ('settings', 'weather', 'weather.wind_speed_mps')),
    (('settings', 'fixed_wing'), ('settings', 'survey', None)),
    (('settings',), ('settings', 'survey', None)),
    (('sites',), ('scenario', 'sites', None)),
]


# Pydantic's messages for the common checks, in the operator's language; anything else stays as is.
_MESSAGES = {
    'missing': 'обязательное поле', 'string_too_short': 'не может быть пустым', 'string_too_long': 'слишком длинное значение (не более {max_length})',
    'greater_than': 'должно быть больше {gt}', 'greater_than_equal': 'должно быть не меньше {ge}', 'less_than': 'должно быть меньше {lt}',
    'less_than_equal': 'должно быть не больше {le}', 'too_short': 'нужно не меньше {min_length} элементов', 'too_long': 'не более {max_length} элементов',
    'float_parsing': 'нужно число', 'int_parsing': 'нужно целое число', 'float_type': 'нужно число', 'int_type': 'нужно целое число',
    'literal_error': 'допустимые значения: {expected}', 'string_pattern_mismatch': 'недопустимые символы: латиница, цифры и _ . : -',
    'date_from_datetime_parsing': 'нужна дата ГГГГ-ММ-ДД', 'date_parsing': 'нужна дата ГГГГ-ММ-ДД', 'extra_forbidden': 'неизвестное поле',
}


def _russian(error):
    template = _MESSAGES.get(error['type'])
    if template is None:
        return re.sub(r'^Value error, ', '', error['msg'])
    try:
        return template.format(**{k: v for k, v in (error.get('ctx') or {}).items()})
    except (KeyError, IndexError):
        return error['msg']


def _input_diagnostic(error):
    """An invalid field of the request as a diagnostic that leads to the field in the interface."""
    loc = [str(x) for x in error['loc'] if x != 'body']
    message = f"{'.'.join(loc) or 'запрос'}: {re.sub(r'^Value error, ', '', error['msg'])}"
    for prefix, (tab, section, field) in FIELD_TARGETS:
        if tuple(loc[:len(prefix)]) == prefix:
            if prefix == ('settings',) and len(loc) > 1:
                field = f'settings.{loc[1]}'
            return diagnostic('INPUT_INVALID', message, actions=[navigate('Перейти к полю', tab, section, field)])
    return diagnostic('INPUT_INVALID', message)


app = create_app()
