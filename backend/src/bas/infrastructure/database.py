import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, select, func
from sqlalchemy.engine import make_url
from bas.infrastructure.db_settings import read_config, profile_engine, SCHEMA

from bas.domain.models import AircraftProfile, Scenario, StoredScenario

metadata = MetaData()
scenarios = Table(
    "scenarios", metadata,
    Column("id", String(36), primary_key=True),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
)
plans = Table('plans', metadata, Column('id', String(36), primary_key=True),
              Column('scenario_id', String(36), nullable=False), Column('payload', JSON, nullable=False))
aircraft_profiles = Table('aircraft_profiles', metadata, Column('id', String(100), primary_key=True),
                         Column('payload', JSON, nullable=False))


def list_profiles(engine):
    with engine.connect() as connection:
        return [AircraftProfile.model_validate(row[0]) for row in connection.execute(select(aircraft_profiles.c.payload))]


def save_profile(engine, profile):
    with engine.begin() as connection:
        connection.execute(aircraft_profiles.insert().values(id=profile.id, payload=profile.model_dump(mode='json')))
    return profile


def make_engine(url: str | None = None):
    if url is None and not os.getenv('BAS_DATABASE_URL'):
        config=read_config()
        if config: return profile_engine(config)
    url = url or os.getenv("BAS_DATABASE_URL", "sqlite:///./bas.db")
    backend=make_url(url).get_backend_name()
    engine=create_engine(url,connect_args={'check_same_thread':False} if backend=='sqlite' else {'connect_timeout':5},pool_pre_ping=True,hide_parameters=True)
    return engine.execution_options(schema_translate_map={None:SCHEMA}) if backend=='postgresql' else engine


def save_scenario(engine, scenario: Scenario) -> StoredScenario:
    record = StoredScenario(id=uuid4(), created_at=datetime.now(timezone.utc).isoformat(), scenario=scenario)
    with engine.begin() as connection:
        connection.execute(scenarios.insert().values(
            id=str(record.id), created_at=record.created_at, payload=scenario.model_dump(mode="json")))
    return record


def get_scenario(engine, scenario_id: str) -> StoredScenario | None:
    with engine.connect() as connection:
        row = connection.execute(select(scenarios).where(scenarios.c.id == scenario_id)).mappings().first()
    return StoredScenario(id=row["id"], created_at=row["created_at"], scenario=row["payload"]) if row else None


def list_scenarios(engine):
    with engine.connect() as connection:
        rows = connection.execute(select(scenarios).order_by(scenarios.c.created_at.desc()).limit(50)).mappings().all()
    return [dict(id=r['id'],name=r['payload']['name'],created_at=r['created_at']) for r in rows]


def save_plan(engine, scenario_id, result):
    result['scenario_id'] = str(scenario_id)
    result['created_at'] = datetime.now(timezone.utc).isoformat()
    with engine.begin() as connection:
        connection.execute(plans.insert().values(id=result['id'],scenario_id=str(scenario_id),payload=result))
    return result


def list_plans(engine, limit=12, offset=0):
    # Older plans have no timestamp; their immutable scenario supplies a fallback.
    created = func.coalesce(plans.c.payload['created_at'].as_string(), scenarios.c.created_at)
    source = plans.join(scenarios, plans.c.scenario_id == scenarios.c.id)
    query = select(plans.c.id, plans.c.scenario_id, created.label('created_at'),
                   scenarios.c.payload['name'].as_string().label('name'),
                   plans.c.payload['objective'].as_string().label('objective'),
                   plans.c.payload['metrics'].label('metrics'),
                   plans.c.payload['created_at'].as_string().label('calculated_at'))
    with engine.connect() as connection:
        total = connection.scalar(select(func.count()).select_from(source))
        rows = connection.execute(query.select_from(source).order_by(created.desc(), plans.c.id.desc())
                                  .offset(offset).limit(limit)).mappings().all()
    return {'items': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset}


def get_plan(engine, plan_id):
    with engine.connect() as connection:
        row = connection.execute(select(plans.c.payload).where(plans.c.id==str(plan_id))).first()
    return row[0] if row else None
