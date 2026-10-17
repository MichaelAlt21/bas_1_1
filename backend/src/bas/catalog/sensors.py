"""Survey sensor profiles: the built-in catalog and the swath/GSD of a sensor on a requirement.

Every built-in parameter carries its origin: confirmed (manufacturer document), computed (with the
formula), assumed (stated assumption) or unknown. Demonstration profiles stand in for equipment no
document confirms for the Geoscan aircraft of this project; they are marked and only fit user
aircraft profiles. The camera profiles keep the ids of version 1.1 (`catalog/cameras.py`).
"""
import math
from datetime import date

from bas.catalog.cameras import CAMERAS
from bas.domain.models import ParameterSource, SensorProfile

CAMERAS_CHECKED = date(2026, 9, 21)
CHECKED = date(2026, 10, 17)


def _confirmed(source, *names):
    return {name: ParameterSource(status='confirmed', source=source) for name in names}


def _camera(camera):
    parameters = _confirmed(camera['source'], 'sensor_width_mm', 'sensor_height_mm', 'focal_length_mm', 'image_width_px', 'image_height_px')
    if camera['id'] == 'geoscan_pollux_v1':
        # The sheet gives the pixel pitch and resolution; the sensor size is their product.
        formula = 'размер пикселя 3,45 мкм × разрешение 1440 × 1080'
        parameters['sensor_width_mm'] = ParameterSource(status='computed', source=camera['source'], formula=formula)
        parameters['sensor_height_mm'] = ParameterSource(status='computed', source=camera['source'], formula=formula)
    parameters['compatible_profiles'] = ParameterSource(status='confirmed', source=camera['source'])
    return SensorProfile(id=camera['id'], version=1, origin='builtin', name=camera['name'], sensor_type=camera['sensor_type'], geometry='frame',
                         sensor_width_mm=camera['sensor_width_mm'], sensor_height_mm=camera['sensor_height_mm'],
                         focal_length_mm=camera['focal_length_mm'], image_width_px=camera['image_width_px'],
                         image_height_px=camera['image_height_px'], compatible_profiles=list(camera['compatible_profiles']),
                         parameters=parameters, source=camera['source'], source_url=camera['source_url'], checked_at=CAMERAS_CHECKED)


def builtin_sensors():
    profiles = [_camera(c) for c in CAMERAS]
    profiles.append(SensorProfile(
        id='geoscan_801_thermal_v1', version=1, origin='builtin', name='Тепловизор подвеса 801 · геометрия не подтверждена',
        sensor_type='thermal', geometry='unknown', compatible_profiles=['geoscan_801'],
        parameters={'compatible_profiles': ParameterSource(status='confirmed', source='Geoscan_801_Manual.pdf, с. 10: гиростабилизированный подвес для съемки в видимом диапазоне и тепловизионного мониторинга'),
                    'sensor_width_mm': ParameterSource(status='unknown', source='Разрешение, размер пикселя и объектив в документации 801 не приведены'),
                    'focal_length_mm': ParameterSource(status='unknown')},
        source='Geoscan_801_Manual.pdf, с. 10, 87–90: тепловизионный канал подвеса есть, параметры матрицы и объектива не указаны. '
               'Ширину полосы задайте в требовании съемки.',
        checked_at=CHECKED))
    profiles.append(SensorProfile(
        id='demo_lidar_v1', version=1, origin='builtin', name='Демонстрационный LiDAR · угол 70°', sensor_type='lidar', geometry='scanner',
        field_of_view_deg=70, demonstration=True, compatible_profiles=[],
        parameters={'field_of_view_deg': ParameterSource(status='assumed', source='Демонстрационное значение, не паспорт конкретного сканера'),
                    'compatible_profiles': ParameterSource(status='unknown', source='Для Gemini, 801 и 201 лидар в документации не указан; '
                                                           'у Геоскана лидарная съемка ведется на Геоскан 401 (geoinfo.ru, обзор Геоскан 401)')},
        source='Демонстрационный профиль. Полоса = 2·H·tg(угол/2). Подходит только пользовательским профилям БПЛА.',
        source_url='https://geoinfo.ru/files/geoskan-401-samyj-populyarnyj-otechestvennyj-nositel-dlya-vozdushnogo-lazernogo-skanirovaniya-i-cifrovoj-aehrofotosemki.pdf',
        checked_at=CHECKED))
    profiles.append(SensorProfile(
        id='demo_magnetometer_v1', version=1, origin='builtin', name='Демонстрационный магнитометр · съемка по линиям',
        sensor_type='geophysical', geometry='line', demonstration=True, compatible_profiles=[],
        parameters={'compatible_profiles': ParameterSource(status='unknown', source='Для Gemini, 801 и 201 магнитометр в документации не указан; '
                                                           'Геоскан показывал аэромагнитную съемку с квантовым магнитометром (Гоглев, Geoscan Weekend 2019)')},
        source='Демонстрационный профиль. Шаг линий задается требованием съемки. Подходит только пользовательским профилям БПЛА.',
        source_url='https://www.geoscan.ru/themes/geoscan/assets/seminary/geoscan-weekend-2019/Гоглев. Аэромагнитная съемка.pdf',
        checked_at=CHECKED))
    return profiles


def compatible(sensor, aircraft_profile):
    """Confirmed for this aircraft model, or the aircraft profile is the operator's own."""
    return aircraft_profile.origin == 'user' or aircraft_profile.id in sensor.compatible_profiles


def survey_geometry(sensor, requirement, altitude, trigger_interval_s=None):
    """(survey dict, None) or (None, problem dict) for `sensor` serving `requirement` at `altitude`.

    The survey dict has the keys of version 1.1 camera results: mode, swath_width_m and for frame
    cameras GSD, footprint length and photo step from the requirement's front overlap.
    """
    base = dict(sensor_id=sensor.id, sensor_name=sensor.name, sensor_type=sensor.sensor_type, altitude_m=altitude,
                demonstration=sensor.demonstration)
    if requirement.swath_width_m is not None and sensor.geometry != 'line':
        return dict(base, mode='manual', swath_width_m=requirement.swath_width_m), None
    if sensor.geometry == 'frame':
        width = altitude * sensor.sensor_width_mm / sensor.focal_length_mm
        length = altitude * sensor.sensor_height_mm / sensor.focal_length_mm
        gsd = max(width / sensor.image_width_px, length / sensor.image_height_px) * 100
        if requirement.max_gsd_cm is not None and gsd > requirement.max_gsd_cm + 1e-9:
            limit = requirement.max_gsd_cm / gsd * altitude
            return None, dict(kind='gsd', actual=round(gsd, 2), required=requirement.max_gsd_cm, altitude_m=altitude, max_altitude_m=round(limit, 1))
        camera = dict(id=sensor.id, name=sensor.name, sensor_type=sensor.sensor_type, source=sensor.source, source_url=sensor.source_url,
                      sensor_width_mm=sensor.sensor_width_mm, sensor_height_mm=sensor.sensor_height_mm, focal_length_mm=sensor.focal_length_mm,
                      image_width_px=sensor.image_width_px, image_height_px=sensor.image_height_px, version=sensor.version)
        return dict(base, mode='camera', camera=camera, swath_width_m=width, footprint_length_m=length, gsd_cm=gsd,
                    front_overlap_percent=requirement.front_overlap_percent,
                    photo_step_m=length * (1 - requirement.front_overlap_percent / 100), trigger_interval_s=trigger_interval_s,
                    orientation='nadir_landscape_cross_track'), None
    if sensor.geometry == 'scanner':
        return dict(base, mode='scanner', swath_width_m=2 * altitude * math.tan(math.radians(sensor.field_of_view_deg) / 2)), None
    if sensor.geometry == 'line':
        if requirement.line_spacing_m is None:
            return None, dict(kind='line_spacing')
        return dict(base, mode='line', swath_width_m=requirement.line_spacing_m, line_spacing_m=requirement.line_spacing_m), None
    return None, dict(kind='geometry_unknown')
