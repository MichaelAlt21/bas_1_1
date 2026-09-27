"""Open-Meteo model data: fixed upstream, bounded response, short cache, no stale fallback."""
import json
import time
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import urlencode
from urllib.request import urlopen
from bas.domain.models import WeatherObservation


class WeatherUnavailable(ValueError):
    pass


def fetch_forecast(latitude, longitude):
    query = urlencode(dict(latitude=latitude, longitude=longitude, timezone='UTC', wind_speed_unit='ms',
        current='temperature_2m,precipitation,wind_speed_10m,wind_direction_10m,wind_gusts_10m'))
    with urlopen('https://api.open-meteo.com/v1/forecast?'+query, timeout=8) as response:
        data = response.read(1_000_001)
        if len(data)>1_000_000:
            raise WeatherUnavailable('Ответ погодного сервиса слишком большой.')
        return json.loads(data)


class WeatherService:
    def __init__(self, fetcher=fetch_forecast):
        self.fetcher = fetcher
        self.cache = OrderedDict()
        self.lock = Lock()

    def current(self, latitude, longitude):
        key = (round(latitude, 4), round(longitude, 4))
        # Serialize cache misses to avoid a burst of duplicate provider requests.
        with self.lock:
            entry = self.cache.get(key)
            if entry and time.monotonic()-entry[0] < 300:
                self.cache.move_to_end(key)
                return entry[1]
            try:
                raw = self.fetcher(*key)
                current, units = raw['current'], raw['current_units']
                if units['wind_speed_10m'] != 'm/s' or units['wind_gusts_10m'] != 'm/s' or units['wind_direction_10m'] != '°' or units['temperature_2m'] != '°C' or units['precipitation'] != 'mm':
                    raise ValueError('Unexpected weather units')
                valid = datetime.fromisoformat(current['time']).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if not -900 <= (now-valid).total_seconds() <= 3600:
                    raise ValueError('Outdated weather')
                result = WeatherObservation(coordinates=[key[1], key[0]], fetched_at=now, valid_at=valid,
                    wind_speed_mps=current['wind_speed_10m'], wind_from_deg=current['wind_direction_10m'] % 360,
                    wind_gusts_mps=current['wind_gusts_10m'], temperature_c=current['temperature_2m'],
                    precipitation_mm=current['precipitation'])
            except Exception as error:
                raise WeatherUnavailable('Не удалось получить свежую погоду. Проверьте соединение или задайте ветер вручную.') from error
            self.cache[key] = (time.monotonic(), result)
            if len(self.cache)>128:
                self.cache.popitem(last=False)
            return result
