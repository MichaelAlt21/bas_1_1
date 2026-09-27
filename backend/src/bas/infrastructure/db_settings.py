"""Server-side PostgreSQL settings. Passwords are supplied by environment, never saved."""
import hmac
import os
import secrets
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import URL, create_engine, inspect, text

SCHEMA = 'kumosystems'


def settings_directory():
    return Path(os.getenv('KUMO_SETTINGS_DIR', str(Path.cwd()/'.runtime')))


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: str = Field(min_length=1, max_length=253, pattern=r'^[A-Za-z0-9.:-]+$')
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=100)
    password_env: str = Field(default='KUMO_DB_PASSWORD', pattern=r'^[A-Z][A-Z0-9_]{0,99}$')
    sslmode: Literal['verify-full', 'require'] = 'verify-full'
    sslrootcert: str = Field(default='', max_length=500)

    @field_validator('database','username','sslrootcert')
    @classmethod
    def valid_text(cls, value):
        if any(ord(c)<32 for c in value):
            raise ValueError('Управляющие символы недопустимы')
        return value


class DatabaseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    config: DatabaseConfig
    test_password: SecretStr | None = None


def config_url(config, password=None):
    password = os.getenv(config.password_env) if password is None else password
    if password is None or password == '':
        raise ValueError(f'На сервере не задана переменная {config.password_env}. Пароль для сохраненного подключения берется из окружения сервера.')
    query = {'sslmode':config.sslmode, 'connect_timeout':'5', 'options':'-c statement_timeout=5000 -c lock_timeout=3000'}
    if config.sslrootcert:
        query['sslrootcert'] = config.sslrootcert
    return URL.create('postgresql+psycopg', username=config.username, password=password,
                      host=config.host, port=config.port, database=config.database, query=query)


def read_config(directory=None):
    path = (Path(directory) if directory else settings_directory())/'database.json'
    if not path.exists(): return None
    return DatabaseConfig.model_validate_json(path.read_text(encoding='utf-8'))


def profile_engine(config, password=None):
    engine = create_engine(config_url(config,password), pool_pre_ping=True, hide_parameters=True)
    return engine.execution_options(schema_translate_map={None:SCHEMA})


def schema_report(connection, metadata):
    inspector = inspect(connection)
    if not inspector.has_schema(SCHEMA):
        return {'schema_exists':False,'tables_ready':False,'missing_tables':list(metadata.tables),'incompatible_tables':[], 'writable':False}
    existing = set(inspector.get_table_names(schema=SCHEMA))
    missing, incompatible = [], []
    for table in metadata.sorted_tables:
        if table.name not in existing:
            missing.append(table.name); continue
        columns={c['name']:c['type'] for c in inspector.get_columns(table.name,schema=SCHEMA)}
        pk=set(inspector.get_pk_constraint(table.name,schema=SCHEMA)['constrained_columns'])
        if set(columns)!={c.name for c in table.columns} or any(c.name not in columns or not isinstance(columns[c.name],type(c.type)) or (getattr(columns.get(c.name),'length',None) is not None and getattr(c.type,'length',None) is not None and columns[c.name].length<c.type.length) for c in table.columns) or pk!={'id'}:
            incompatible.append(table.name)
    ready = not missing and not incompatible
    writable = ready and connection.execute(text("SELECT has_schema_privilege(current_user, 'kumosystems', 'USAGE')")).scalar() and all(connection.execute(text('SELECT has_table_privilege(current_user, :table, :privilege)'),
        {'table':f'{SCHEMA}.{t.name}','privilege':privilege}).scalar() for t in metadata.sorted_tables for privilege in ('SELECT','INSERT'))
    return {'schema_exists':True,'tables_ready':ready,'missing_tables':missing,'incompatible_tables':incompatible,'writable':bool(writable)}


def verify_active_schema(engine, metadata):
    with engine.connect() as connection:
        report=schema_report(connection,metadata)
        if not report['tables_ready'] or not report['writable']:
            raise RuntimeError('PostgreSQL: схема kumosystems не готова. Подготовьте ее через настройки приложения или python -m bas.configure_database prepare. Автоматическое изменение внешней БД при старте отключено.')


class DatabaseSetupError(ValueError):
    pass


class DatabaseSettings:
    def __init__(self, directory=None, engine_factory=profile_engine):
        self.directory=Path(directory) if directory else settings_directory()
        self.engine_factory=engine_factory

    def initialize_admin(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        path=self.directory/'admin-token'
        if not os.getenv('KUMO_ADMIN_TOKEN') and not path.exists():
            try:
                with path.open('x',encoding='utf-8') as stream:
                    stream.write(secrets.token_urlsafe(32))
                path.chmod(0o600)
            except FileExistsError:
                pass

    def authorized(self, supplied):
        expected=os.getenv('KUMO_ADMIN_TOKEN')
        if not expected:
            path=self.directory/'admin-token'
            expected=path.read_text(encoding='utf-8').strip() if path.exists() else None
        return bool(expected and supplied and hmac.compare_digest(expected.encode('utf-8'),supplied.encode('utf-8')))

    def probe(self, request, metadata, prepare=False, use_environment=False):
        secret=None if use_environment or request.test_password is None else request.test_password.get_secret_value()
        # Resolve before the sanitized connection error boundary, to report missing env names.
        config_url(request.config,secret)
        engine=None
        try:
            engine=self.engine_factory(request.config,secret)
            with engine.begin() as connection:
                connection.execute(text('SELECT 1'))
                report=schema_report(connection,metadata)
                if prepare:
                    if report['incompatible_tables']:
                        raise DatabaseSetupError('В схеме kumosystems есть несовместимые таблицы. Они не изменены; требуется миграция администратора.')
                    connection.execute(text('CREATE SCHEMA IF NOT EXISTS kumosystems'))
                    metadata.create_all(connection)
                    report=schema_report(connection,metadata)
                return {'connected':True,**report}
        except DatabaseSetupError:
            raise
        except Exception:
            raise ValueError('Подключение или проверка PostgreSQL не выполнены. Проверьте адрес, сеть/VPN, учетные данные, TLS и права на схему kumosystems. Пароль и детали драйвера не возвращаются.') from None
        finally:
            if engine is not None: engine.dispose()

    def save(self, request, metadata):
        if os.getenv('BAS_DATABASE_URL'):
            raise ValueError('BAS_DATABASE_URL задан на сервере и имеет приоритет. Для управления из интерфейса сначала уберите эту переменную и перезапустите сервер.')
        report=self.probe(request,metadata,use_environment=True)
        if not report['tables_ready'] or not report['writable']:
            raise ValueError('Соединение доступно, но схема или права SELECT/INSERT не готовы. Сначала подготовьте схему.')
        self.directory.mkdir(parents=True,exist_ok=True)
        temporary=self.directory/('database-'+secrets.token_hex(8)+'.tmp')
        try:
            temporary.write_text(request.config.model_dump_json(indent=2),encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(self.directory/'database.json')
        finally:
            temporary.unlink(missing_ok=True)
        return {'saved':True,'requires_restart':True,'message':'Настройки проверены и сохранены. Подключение сменится после перезапуска API. Перенос данных не выполнялся.'}

    def reset(self):
        if os.getenv('BAS_DATABASE_URL'):
            raise ValueError('Сначала уберите BAS_DATABASE_URL из окружения сервера.')
        path=self.directory/'database.json'
        # Keep a recovery copy; no scenario/plan records are touched.
        if path.exists(): path.replace(self.directory/('database-disabled-'+secrets.token_hex(6)+'.json'))
        return {'saved':True,'requires_restart':True,'message':'После перезапуска API будет использована локальная SQLite. Данные PostgreSQL и SQLite не изменены.'}
