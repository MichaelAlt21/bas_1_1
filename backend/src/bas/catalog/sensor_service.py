"""User sensor profiles: create, edit as new versions, export and import without silent overwrites.

Exchange format (JSON):

    {"format": "kumo-sensor-profiles", "format_version": 1, "exported_at": "…",
     "profiles": [SensorProfile, …]}

Import never replaces a profile in place. A profile with a new id is added. The same id with the same
content is skipped. A conflict — the id of a built-in profile, or a user profile with other content —
is resolved only as asked: `reject` (default: nothing is imported and the conflicts are listed),
`new_version` (a user profile gets the imported content as its next version; built-in profiles
cannot) or `rename` (the imported profile is stored under a free id).
"""
import re
from datetime import datetime, timezone

from pydantic import ValidationError

from bas.catalog.sensors import builtin_sensors
from bas.domain.models import SensorProfile, SensorProfileInput
from bas.infrastructure.database import list_sensor_versions, save_sensor

FORMAT = 'kumo-sensor-profiles'
FORMAT_VERSION = 1


class SensorCatalogError(ValueError):
    def __init__(self, message, status=422, conflicts=None):
        super().__init__(message)
        self.status = status
        self.conflicts = conflicts or []


def _content(profile):
    return profile.model_dump(mode='json', exclude={'id', 'version', 'origin'})


def latest(engine):
    """{id: latest version}: built-in profiles, then user profiles."""
    result = {p.id: p for p in builtin_sensors()}
    for profile in list_sensor_versions(engine):
        result[profile.id] = profile
    return result


def catalog(engine):
    return list(latest(engine).values())


def versions(engine, sensor_id):
    found = [p for p in list_sensor_versions(engine) if p.id == sensor_id]
    if not found and sensor_id in {p.id for p in builtin_sensors()}:
        found = [p for p in builtin_sensors() if p.id == sensor_id]
    return found


TRANSLIT = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                    ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't', 'u', 'f', 'h', 'ts', 'ch',
                     'sh', 'sch', '', 'y', '', 'e', 'yu', 'ya']))


def _slug(name):
    latin = ''.join(TRANSLIT.get(c, c) for c in name.lower())
    base = re.sub(r'[^a-z0-9]+', '_', latin).strip('_')
    return 'user_' + (base[:60] or 'sensor')


def _free_id(existing, wanted):
    if wanted not in existing:
        return wanted
    n = 2
    while f'{wanted}_{n}' in existing:
        n += 1
    return f'{wanted}_{n}'


def create(engine, data: SensorProfileInput, sensor_id=None):
    existing = latest(engine)
    if sensor_id is not None:
        if sensor_id in existing:
            raise SensorCatalogError(f'Профиль {sensor_id} уже есть. Измените его (будет новая версия) или выберите другой идентификатор.', status=409)
        new_id = sensor_id
    else:
        new_id = _free_id(existing, _slug(data.name))
    profile = SensorProfile(**data.model_dump(), id=new_id, version=1, origin='user')
    return save_sensor(engine, profile)


def update(engine, sensor_id, data: SensorProfileInput):
    current = latest(engine).get(sensor_id)
    if current is None:
        raise SensorCatalogError(f'Профиль {sensor_id} не найден.', status=404)
    if current.origin == 'builtin':
        raise SensorCatalogError('Встроенный профиль нельзя изменить: создайте пользовательскую копию.', status=409)
    if _content(SensorProfile(**data.model_dump(), id=sensor_id)) == _content(current):
        return current
    return save_sensor(engine, SensorProfile(**data.model_dump(), id=sensor_id, version=current.version + 1, origin='user'))


def export_document(profiles):
    return dict(format=FORMAT, format_version=FORMAT_VERSION, exported_at=datetime.now(timezone.utc).isoformat(),
                profiles=[p.model_dump(mode='json') for p in profiles])


def import_document(engine, document, on_conflict='reject'):
    if not isinstance(document, dict) or document.get('format') != FORMAT:
        raise SensorCatalogError(f'Это не файл профилей KUMO: ожидается "format": "{FORMAT}".')
    if document.get('format_version') != FORMAT_VERSION:
        raise SensorCatalogError(f'Версия формата {document.get("format_version")!r} не поддерживается (нужна {FORMAT_VERSION}).')
    raw = document.get('profiles')
    if not isinstance(raw, list) or not raw:
        raise SensorCatalogError('В файле нет профилей.')
    parsed, errors = [], []
    for n, item in enumerate(raw, 1):
        try:
            profile = SensorProfile.model_validate(dict(item, origin='user') if isinstance(item, dict) else item)
        except ValidationError as error:
            first = error.errors()[0]
            errors.append(f"профиль {n} ({item.get('id', '?') if isinstance(item, dict) else '?'}): {'.'.join(str(x) for x in first['loc'])} — {first['msg']}")
            continue
        parsed.append(profile)
    if errors:
        raise SensorCatalogError('Файл не прошел проверку: ' + '; '.join(errors[:10]))
    ids = [p.id for p in parsed]
    if len(ids) != len(set(ids)):
        raise SensorCatalogError('В файле повторяются идентификаторы профилей.')
    existing = latest(engine)
    plan, conflicts = [], []
    for profile in parsed:
        current = existing.get(profile.id)
        if current is None:
            plan.append(('add', profile))
        elif _content(current) == _content(profile):
            plan.append(('same', profile))
        else:
            conflicts.append(dict(id=profile.id, existing_version=current.version, builtin=current.origin == 'builtin', name=profile.name))
            plan.append(('conflict', profile))
    if conflicts and on_conflict == 'reject':
        raise SensorCatalogError('Профили с такими идентификаторами уже есть и отличаются. Ничего не импортировано: выберите «как новые версии» или «под новыми ID».',
                                 status=409, conflicts=conflicts)
    if on_conflict == 'new_version' and any(c['builtin'] for c in conflicts):
        raise SensorCatalogError('Встроенные профили нельзя заменить новой версией: импортируйте их с переименованием.', status=409,
                                 conflicts=[c for c in conflicts if c['builtin']])
    report = dict(added=[], new_versions=[], renamed=[], unchanged=[])
    taken = set(existing)
    for kind, profile in plan:
        if kind == 'same':
            report['unchanged'].append(profile.id)
        elif kind == 'add':
            save_sensor(engine, profile.model_copy(update=dict(version=1, origin='user')))
            report['added'].append(profile.id)
        elif on_conflict == 'new_version':
            version = existing[profile.id].version + 1
            save_sensor(engine, profile.model_copy(update=dict(version=version, origin='user')))
            report['new_versions'].append(dict(id=profile.id, version=version))
        else:
            new_id = _free_id(taken, profile.id)
            taken.add(new_id)
            save_sensor(engine, profile.model_copy(update=dict(id=new_id, version=1, origin='user')))
            report['renamed'].append(dict(id=profile.id, new_id=new_id))
    return report


def snapshot(engine, scenario):
    """Sensor profiles the fleet refers to, as the scenario will keep them: its own snapshot first."""
    known = {p.id: p for p in scenario.sensor_profiles}
    available = latest(engine)
    for aircraft in scenario.fleet:
        for sensor_id in (aircraft.sensors if aircraft.sensors is not None else [aircraft.camera_id] if aircraft.camera_id else []):
            if sensor_id not in known and sensor_id in available:
                known[sensor_id] = available[sensor_id]
    return list(known.values())
