"""Sunrise and sunset from the NOAA sunrise equation, accurate to about a minute.

The sun's upper limb at the horizon with standard refraction (-0.833°); civil twilight uses -6°.
"""
import math
from datetime import date, datetime, timedelta, timezone

J2000 = 2451545.0
UNIX_EPOCH_JD = 2440587.5


def _julian_day(day: date) -> float:
    # Midnight UTC (JD x.5): with noon, the ceiling below would land on the next day.
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() / 86400 + UNIX_EPOCH_JD


def _to_datetime(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - UNIX_EPOCH_JD) * 86400, timezone.utc).replace(microsecond=0)


def sun_times(latitude: float, longitude: float, day: date, depression_deg: float = 0.833) -> dict:
    """UTC sunrise/sunset of the given civil date at the place; polar day/night leave them None."""
    n = math.ceil(_julian_day(day) - J2000 + 0.0008)
    mean_noon = n - longitude / 360
    anomaly = math.radians((357.5291 + 0.98560028 * mean_noon) % 360)
    centre = 1.9148 * math.sin(anomaly) + 0.02 * math.sin(2 * anomaly) + 0.0003 * math.sin(3 * anomaly)
    ecliptic = math.radians((math.degrees(anomaly) + centre + 180 + 102.9372) % 360)
    transit = J2000 + mean_noon + 0.0053 * math.sin(anomaly) - 0.0069 * math.sin(2 * ecliptic)
    declination = math.asin(math.sin(ecliptic) * math.sin(math.radians(23.4397)))
    phi = math.radians(latitude)
    cos_hour = (math.sin(math.radians(-depression_deg)) - math.sin(phi) * math.sin(declination)) / (math.cos(phi) * math.cos(declination))
    noon = _to_datetime(transit)
    if cos_hour > 1:
        return dict(sunrise=None, sunset=None, solar_noon=noon, polar='night')
    if cos_hour < -1:
        return dict(sunrise=None, sunset=None, solar_noon=noon, polar='day')
    hour = math.degrees(math.acos(cos_hour)) / 360
    return dict(sunrise=_to_datetime(transit - hour), sunset=_to_datetime(transit + hour), solar_noon=noon, polar=None)


def sunset_after(latitude: float, longitude: float, moment: datetime) -> datetime | None:
    """The first sunset after `moment` within two days; None in polar day or night."""
    local_day = (moment.astimezone(timezone.utc) + timedelta(hours=longitude / 15)).date()
    for offset in (0, 1):
        sunset = sun_times(latitude, longitude, local_day + timedelta(days=offset))['sunset']
        if sunset and sunset > moment:
            return sunset
    return None
