"""Versioned nominal nadir camera profiles; confirmed sensor/lens geometry only."""
import math

CAMERAS = [
    dict(id='sony_rx1rm2_v1', name='Sony RX1R II · 35 мм', compatible_profiles=['geoscan_201'], sensor_type='rgb',
         sensor_width_mm=35.9, sensor_height_mm=24., focal_length_mm=35., image_width_px=7952, image_height_px=5304,
         source='Geoscan_201_Manual.pdf, с. 50–53, 117; Sony DSC-RX1RM2 specifications',
         source_url='https://www.sony.com/en-ma/electronics/cyber-shot-compact-cameras/dsc-rx1rm2/specifications'),
    dict(id='geoscan_pf1b_v1', name='Geoscan PF1B · 20 мм', compatible_profiles=['gemini'], sensor_type='rgb',
         sensor_width_mm=23.5, sensor_height_mm=15.6, focal_length_mm=20., image_width_px=6000, image_height_px=4000,
         source='Gemini_Specs.pdf, с. 3; физическое фокусное расстояние Sony SEL20F28',
         source_url='https://download.geoscan.ru/site-files/gemini/Gemini_Specs_EN_RUS.pdf?v2'),
    dict(id='geoscan_pollux_v1', name='Geoscan Pollux · 8 мм', compatible_profiles=['gemini', 'geoscan_201'], sensor_type='multispectral',
         # Sensor size from pixel pitch x resolution, not the nominal "1/2.9 inch" label: consistent with PF1B/RX1RM2
         # above and cross-checked against the sheet's own 6.3 mm diagonal and 43 deg diagonal field of view.
         sensor_width_mm=1440*3.45e-3, sensor_height_mm=1080*3.45e-3, focal_length_mm=8., image_width_px=1440, image_height_px=1080,
         source='Gemini_Specs.pdf, с. 3-4 (Geoscan Pollux Multispectral Camera: разрешение, пиксель 3,45 мкм, фокусное расстояние, диагональ 6,3 мм); Geoscan_201_Manual.pdf, с. 117 (объектив 8 мм); Geoscan_Gemini_Manual.pdf, с. 71 (то же разрешение/пиксель/диагональ/фокусное расстояние, второй независимый источник)',
         source_url='https://download.geoscan.ru/site-files/gemini/Gemini_Specs_EN_RUS.pdf?v2'),
    dict(id='sony_umc_r10c_v1', name='Sony UMC-R10C · 20 мм', compatible_profiles=['gemini'], sensor_type='rgb',
         sensor_width_mm=23.2, sensor_height_mm=15.4, focal_length_mm=20., image_width_px=5456, image_height_px=3632,
         source='Geoscan_Gemini_Manual.pdf, с. 71 (Фотокамера Geoscan PF1B/Sony UMC-R10C: разрешение 5456x3632, матрица APS-C 23,2x15,4 мм, фокусное расстояние 20 мм — тот же объектив, что и у PF1B)',
         source_url='https://download.geoscan.ru/site-files/gemini/Gemini_Specs_EN_RUS.pdf?v2'),
]


def camera_geometry(aircraft, profile, settings, altitude=None):
    from bas.planning.geometry import reject
    if settings.coverage_mode == 'manual':
        if settings.max_gsd_cm is not None:
            reject('GSD_NEEDS_CAMERA', 'Ограничение GSD требует режима расчета по камере.')
        return dict(mode='manual', swath_width_m=settings.swath_width_m)
    camera = next((p for p in CAMERAS if p['id'] == aircraft.camera_id), None)
    if camera is None:
        reject('CAMERA_REQUIRED', f'Выберите камеру для {aircraft.id}. Для не перечисленных сенсоров пока используйте ручную ширину захвата.')
    if camera['sensor_type'] != aircraft.payload:
        reject('CAMERA_SENSOR_MISMATCH', f'{camera["name"]} — профиль {camera["sensor_type"]}, а нагрузка {aircraft.id} задана как {aircraft.payload}.')
    if profile.origin == 'builtin' and profile.id not in camera['compatible_profiles']:
        reject('CAMERA_INCOMPATIBLE', f'{camera["name"]} не подтверждена для {profile.name}.')
    h = settings.altitude_m if altitude is None else altitude
    width = h * camera['sensor_width_mm'] / camera['focal_length_mm']
    length = h * camera['sensor_height_mm'] / camera['focal_length_mm']
    gsd = max(width / camera['image_width_px'], length / camera['image_height_px']) * 100
    if settings.max_gsd_cm is not None and gsd > settings.max_gsd_cm + 1e-9:
        reject('GSD_LIMIT', f'{aircraft.id}: GSD {gsd:.2f} см/пикс на высоте {h:g} м превышает {settings.max_gsd_cm:g}. Уменьшите высоту или измените требование.')
    return dict(mode='camera', camera=dict(camera), swath_width_m=width, footprint_length_m=length, gsd_cm=gsd,
                front_overlap_percent=settings.front_overlap_percent,
                photo_step_m=length * (1 - settings.front_overlap_percent / 100),
                trigger_interval_s=aircraft.trigger_interval_s,
                orientation='nadir_landscape_cross_track', altitude_m=h)


def crabbed_width(width, length, step, crab):
    """Width of the band every point of which lies in some frame, when frames are turned by `crab`.

    A fixed-wing aircraft in a crosswind flies with its nose turned into the wind, so a camera fixed
    to the airframe takes frames turned by the crab angle against the ground track. Frames `width`
    across and `length` along the camera axes are taken every `step` metres of track. At a lateral
    offset v the frame spans a chord along the track; the point is covered when the chord is at
    least `step`, so frames at consecutive exposures meet.
    """
    angle = abs(crab)
    if angle < 1e-12:
        return width if step <= length + 1e-9 else 0.
    cos, sin = math.cos(angle), math.sin(angle)

    def chord(v):
        low = max((-length / 2 - v * sin) / cos, (v * cos - width / 2) / sin)
        high = min((length / 2 - v * sin) / cos, (v * cos + width / 2) / sin)
        return max(0., high - low)
    if chord(0) < step:
        return 0.
    low, high = 0., (width * cos + length * sin) / 2
    for _ in range(60):
        middle = (low + high) / 2
        if chord(middle) >= step: low = middle
        else: high = middle
    return 2 * low


def effective_swath(survey, crab, payload):
    """Swath a strip can rely on at crab angle `crab`; never wider than the nominal one.

    Camera mode uses the frame geometry above. A manual LiDAR swath is a scan line across the
    airframe, so it narrows with the cosine; other manual widths are left as the operator set them.
    """
    nominal = survey['swath_width_m']
    if survey['mode'] == 'camera':
        return min(nominal, crabbed_width(nominal, survey['footprint_length_m'], survey['photo_step_m'], crab))
    return nominal * math.cos(crab) if payload == 'lidar' else nominal
