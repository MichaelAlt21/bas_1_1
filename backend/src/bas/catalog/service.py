"""Validated catalog and immutable profile snapshots for reproducible calculations."""
import json
from importlib.resources import files
from bas.domain.models import AircraftProfile
from bas.planning.geometry import reject


def builtin_profiles():
    raw = json.loads(files('bas.catalog').joinpath('aircraft.json').read_text(encoding='utf-8'))
    return [AircraftProfile(id=p['id'], name=p['name'], airframe=p['airframe'],
        max_flight_time_min=p['max_flight_time_min'], max_wind_mps=p['max_wind_mps'],
        max_horizontal_speed_mps=p.get('max_horizontal_speed_mps', max(p.get('airspeed_range_mps', [15]))),
        source=p['source'], origin='builtin',
        maintenance_interval=p.get('maintenance', {}).get('interval'), maintenance_unit=p.get('maintenance', {}).get('unit'),
        maintenance_source=p.get('maintenance', {}).get('source', ''),
        charge_time_min=p.get('charge', {}).get('time_min'), charge_source=p.get('charge', {}).get('source', '')) for p in raw]


def resolve_profiles(scenario, catalog=None, ignore_missing=False):
    available = {p.id:p for p in (builtin_profiles() if catalog is None else catalog)}
    available.update({p.id:p for p in scenario.aircraft_profiles})
    result = {}
    for aircraft in scenario.fleet:
        if aircraft.profile_id not in available:
            if ignore_missing: continue
            reject('PROFILE_UNKNOWN', f'Профиль {aircraft.profile_id} отсутствует в каталоге и снимке сценария.', aircraft=[aircraft.id])
        result[aircraft.profile_id] = available[aircraft.profile_id]
    return result
