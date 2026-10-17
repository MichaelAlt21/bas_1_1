"""Scenario of version 1.1 or earlier → survey requirements model of version 1.2.

The single survey type over all areas becomes one requirement over every area with the swath,
overlaps and GSD limit of the old settings; each aircraft's camera (or, without one, a payload of
that type without a profile) becomes its list of sensors. Planning the converted scenario gives
the same plan as the original: checked for every example in tests/test_survey_requirements.py.
The frontend (features/scenarios/requirements.ts) performs the same conversion when opening.
"""
import copy

LEGACY_REQUIREMENT = 'survey'


def upgrade(data: dict) -> dict:
    """A converted copy of scenario JSON; scenarios that already have requirements are returned as they are."""
    if data.get('requirements') is not None:
        return copy.deepcopy(data)
    out = copy.deepcopy(data)
    settings = out.setdefault('settings', {})
    count = 1 + len(out.get('additional_survey_areas') or [])
    area_ids = out.get('area_ids') or [f'zone-{n}' for n in range(1, count + 1)]
    camera = settings.get('coverage_mode', 'manual') == 'camera'
    requirement = dict(id=LEGACY_REQUIREMENT, name='Съемка', area_ids=area_ids, survey_type=out.get('survey_type', 'rgb'),
                       side_overlap_percent=settings.get('side_overlap_percent', 30), front_overlap_percent=settings.get('front_overlap_percent', 70))
    if camera:
        if settings.get('max_gsd_cm') is not None:
            requirement['max_gsd_cm'] = settings['max_gsd_cm']
    else:
        requirement['swath_width_m'] = settings.get('swath_width_m', 100)
    out['area_ids'] = area_ids
    out['requirements'] = [requirement]
    for aircraft in out.get('fleet', []):
        if aircraft.get('sensors') is None:
            aircraft['sensors'] = [aircraft['camera_id']] if aircraft.get('camera_id') else [f"generic_{aircraft.get('payload', 'rgb')}"]
    return out
