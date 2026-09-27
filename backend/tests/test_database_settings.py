import json
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import String
from bas.main import create_app
from bas.infrastructure import db_settings as settings
from bas.infrastructure.database import metadata, make_engine


def config():
    return settings.DatabaseConfig(host='company.example',database='kumo',username='planner')


class FakeInspector:
    exists=True
    tables=list(metadata.tables)
    bad=False
    def has_schema(self,name): assert name=='kumosystems';return self.exists
    def get_table_names(self,schema): return self.tables
    def get_columns(self,name,schema):
        return [{'name':column.name,'type':String(1) if self.bad else column.type} for column in metadata.tables[name].columns]
    def get_pk_constraint(self,*args,**kwargs): return {'constrained_columns':['id']}


class FakeConnection:
    def __init__(self): self.statements=[];self.denied=None
    def execute(self,query,params=None):
        self.statements.append((str(query),params))
        value=not (params and params.get('privilege')==self.denied)
        return type('Result',(),{'scalar':lambda _:value})()


class FakeEngine:
    def __init__(self): self.connection=FakeConnection();self.disposed=False
    @contextmanager
    def begin(self): yield self.connection
    def dispose(self): self.disposed=True


@pytest.fixture
def manager(tmp_path,monkeypatch):
    monkeypatch.delenv('BAS_DATABASE_URL',raising=False)
    monkeypatch.delenv('KUMO_ADMIN_TOKEN',raising=False)
    monkeypatch.setenv('KUMO_DB_PASSWORD','server-secret')
    inspector=FakeInspector();engine=FakeEngine()
    monkeypatch.setattr(settings,'inspect',lambda _:inspector)
    value=settings.DatabaseSettings(tmp_path,lambda *_:engine)
    return value,engine,inspector


def test_url_special_characters_and_tls(monkeypatch):
    monkeypatch.setenv('KUMO_DB_PASSWORD','a@b:/# ?!')
    url=settings.config_url(config())
    assert url.password=='a@b:/# ?!'
    assert url.host=='company.example'
    assert url.query['sslmode']=='verify-full'
    assert url.query['connect_timeout']=='5'
    assert 'a@b' not in str(url)


def test_probe_is_read_only_and_checks_both_privileges(manager):
    value,engine,_=manager
    report=value.probe(settings.DatabaseRequest(config=config()),metadata)
    assert report['writable']
    assert all(query.startswith('SELECT') for query,_ in engine.connection.statements)
    assert {p['privilege'] for _,p in engine.connection.statements if p}=={'SELECT','INSERT'}
    assert engine.disposed


def test_select_without_insert_is_not_writable(manager):
    value,engine,_=manager;engine.connection.denied='INSERT'
    assert not value.probe(settings.DatabaseRequest(config=config()),metadata)['writable']


def test_prepare_creates_only_own_schema_and_missing_tables(manager,monkeypatch):
    value,engine,inspector=manager;inspector.exists=False
    calls=[]
    def create(connection): calls.append(connection);inspector.exists=True
    monkeypatch.setattr(metadata,'create_all',create)
    assert value.probe(settings.DatabaseRequest(config=config()),metadata,prepare=True)['tables_ready']
    assert calls==[engine.connection]
    mutations=[q for q,_ in engine.connection.statements if not q.startswith('SELECT')]
    assert mutations==['CREATE SCHEMA IF NOT EXISTS kumosystems']


def test_incompatible_schema_not_modified(manager,monkeypatch):
    value,engine,inspector=manager;inspector.bad=True
    monkeypatch.setattr(metadata,'create_all',lambda _:pytest.fail('Must not create or change tables'))
    with pytest.raises(ValueError,match='несовместимые'):
        value.probe(settings.DatabaseRequest(config=config()),metadata,prepare=True)
    assert all(q.startswith('SELECT') for q,_ in engine.connection.statements)


def test_password_never_saved_and_configuration_is_recoverable(manager):
    value,_,_=manager
    result=value.save(settings.DatabaseRequest(config=config(),test_password='browser-secret'),metadata)
    raw=(value.directory/'database.json').read_text(encoding='utf-8')
    assert 'browser-secret' not in raw and 'server-secret' not in raw
    assert result['requires_restart']
    assert settings.read_config(value.directory)==config()
    value.reset()
    assert settings.read_config(value.directory) is None
    assert len(list(value.directory.glob('database-disabled-*.json')))==1


def test_save_requires_server_password_not_transient_password(manager,monkeypatch):
    value,_,_=manager;monkeypatch.delenv('KUMO_DB_PASSWORD')
    with pytest.raises(ValueError,match='KUMO_DB_PASSWORD'):
        value.save(settings.DatabaseRequest(config=config(),test_password='transient'),metadata)
    assert not (value.directory/'database.json').exists()


def test_failed_check_preserves_existing_config(manager):
    value,_,inspector=manager
    value.save(settings.DatabaseRequest(config=config()),metadata)
    before=(value.directory/'database.json').read_bytes()
    inspector.exists=False
    with pytest.raises(ValueError): value.save(settings.DatabaseRequest(config=config().model_copy(update={'host':'new.example'})),metadata)
    assert (value.directory/'database.json').read_bytes()==before


def test_driver_errors_do_not_leak_password(manager):
    value,_,_=manager
    def fail(*_): raise ValueError('postgresql://user:browser-secret@host')
    value.engine_factory=fail
    with pytest.raises(ValueError) as error: value.probe(settings.DatabaseRequest(config=config(),test_password='browser-secret'),metadata)
    assert 'browser-secret' not in str(error.value)


def test_database_routes_require_admin_and_never_echo_secrets(manager,tmp_path):
    value,_,_=manager
    with TestClient(create_app(f"sqlite:///{tmp_path/'local.db'}",database_settings=value)) as client:
        assert client.get('/api/settings/database').status_code==403
        assert client.post('/api/settings/database/check',json={'config':config().model_dump()}).status_code==403
        token=(value.directory/'admin-token').read_text(encoding='utf-8')
        headers={'X-Kumo-Admin-Token':token}
        response=client.get('/api/settings/database',headers=headers)
        assert response.status_code==200
        assert response.json()['active']['driver']=='sqlite'
        assert token not in response.text
        body={'config':config().model_dump(),'test_password':'browser-secret'}
        assert client.post('/api/settings/database/check',json=body,headers=headers).json()['tables_ready']
        assert client.post('/api/settings/database/save',json=body,headers=headers).json()['requires_restart']
        assert client.get('/api/settings/database',headers=headers).json()['active']['driver']=='sqlite'
        invalid=client.post('/api/settings/database/check',json={'test_password':'browser-secret'},headers=headers)
        assert invalid.status_code==422 and 'browser-secret' not in invalid.text
        assert client.post('/api/settings/database/local',headers=headers).status_code==200


def test_env_override_prevents_ambiguous_saved_configuration(manager,monkeypatch):
    value,_,_=manager;monkeypatch.setenv('BAS_DATABASE_URL','sqlite:///override.db')
    with pytest.raises(ValueError,match='BAS_DATABASE_URL'):value.save(settings.DatabaseRequest(config=config()),metadata)


def test_engine_loads_saved_config_with_schema_translation(manager,monkeypatch):
    value,_,_=manager
    value.save(settings.DatabaseRequest(config=config()),metadata)
    monkeypatch.setenv('KUMO_SETTINGS_DIR',str(value.directory))
    engine=make_engine()
    try:
        assert engine.url.host=='company.example'
        assert engine.url.password=='server-secret'
        assert engine.get_execution_options()['schema_translate_map']=={None:'kumosystems'}
    finally:engine.dispose()
