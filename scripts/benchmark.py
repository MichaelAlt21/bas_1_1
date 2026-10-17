"""Plan fingerprints and planning time on fixed scenarios: compare versions of the planner.

    backend\\.venv\\Scripts\\python.exe scripts\\benchmark.py <output.json> [--runs 3] [--quick]

Fingerprints cover every example in contracts/ under both historical objectives and both landing
policies: a hash of the routes, landings and reserve checks plus the metrics. Timing runs the
examples and two larger generated scenarios (20 Gemini at four sites with obstacles; two 201 and
four Gemini in an 8 m/s wind). Compare two outputs with --compare a.json b.json.
"""
import argparse
import copy
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend' / 'src'))
CONTRACTS = ROOT / 'contracts'


def rect(x, y, w, h):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]}


def stress():
    """20 Gemini at four sites, a restriction zone and point obstacles inside the area, three sorties."""
    sites = [dict(id=f'S{n + 1}', name=f'Площадка {n + 1}', kind='launch_landing', coordinates=c)
             for n, c in enumerate([[37.296, 55.797], [37.364, 55.797], [37.296, 55.843], [37.364, 55.843]])]
    sites.append(dict(id='R1', name='Запасная', kind='alternate', coordinates=[37.330, 55.792]))
    obstacles = [dict(id=f'O{n}', kind='мачта', geometry={'type': 'Point', 'coordinates': [37.31 + 0.006 * (n % 4), 55.81 + 0.008 * (n // 4)]},
                      radius_m=5, height_m=160, height_reference='AGL') for n in range(8)]
    zone = dict(id='Z1', name='Зона ограничений', category='restricted', geometry=rect(37.345, 55.825, .006, .004))
    return dict(schema_version='0.1', name='Нагрузочный: 20 Gemini', survey_area=rect(37.300, 55.800, .060, .040),
                allowed_airspace=rect(37.285, 55.788, .090, .065), sites=sites, restriction_zones=[zone], obstacles=obstacles,
                fleet=[dict(id=f'G{n + 1:02d}', profile_id='gemini', battery_percent=100, payload='rgb', start_site_id=f'S{n % 4 + 1}') for n in range(20)],
                weather=dict(wind_speed_mps=4, wind_from_deg=250), survey_type='rgb', objective='min_completion_time',
                settings=dict(altitude_m=100, swath_width_m=150, side_overlap_percent=20, blocked_area_policy='exclude',
                              terrain_min_m=150, terrain_max_m=250, sorties_per_aircraft=3))


def mixed():
    data = json.loads((CONTRACTS / 'scenario.fixedwing.example.json').read_text(encoding='utf-8'))
    data['survey_area'] = rect(37.300, 55.800, .045, .025)
    data['allowed_airspace'] = rect(37.260, 55.775, .13, .08)
    data['fleet'] = [dict(id=f'201-{i}', profile_id='geoscan_201', battery_percent=100, payload='rgb', start_site_id='plane-base', airspeed_mps=22,
                          camera_id='sony_rx1rm2_v1') for i in (1, 2)] + \
                    [dict(id=f'Gemini-{i}', profile_id='gemini', battery_percent=100, payload='rgb', start_site_id='copter-base',
                          camera_id='geoscan_pf1b_v1') for i in (1, 2, 3, 4)]
    data['settings']['sorties_per_aircraft'] = 3
    data['weather'] = dict(wind_speed_mps=8, wind_from_deg=200)
    return data


def fingerprint(result):
    legs = json.dumps([[m['aircraft_id'], m['landing_site_id'], m['alternate'],
                        [(l['phase'], l['coordinates'], l['start'], l['end']) for l in m['legs']]] for m in result['missions']])
    m = result['metrics']
    return [repr(m['completion_time_s']), repr(m['total_airtime_s']), repr(m['total_distance_m']), repr(m['coverage_percent']),
            m['strip_angle_deg'], hashlib.sha1(legs.encode()).hexdigest()[:12]]


def run(arguments):
    from bas.domain.models import Scenario
    from bas.planning.geometry import PlanningError
    from bas.planning.service import plan
    out = dict(python=platform.python_version(), platform=platform.platform(), processor=platform.processor(),
               runs=arguments.runs, fingerprints={}, timing={})
    for path in sorted(CONTRACTS.glob('scenario*.json')):
        base = json.loads(path.read_text(encoding='utf-8'))
        for objective in ('min_completion_time', 'min_total_airtime'):
            for policy in ('return_to_start', 'best_main_site'):
                data = copy.deepcopy(base)
                data['objective'] = objective
                data.setdefault('settings', {})['landing_policy'] = policy
                key = f'{path.stem}|{objective}|{policy}'
                try:
                    out['fingerprints'][key] = fingerprint(plan(Scenario.model_validate(data)))
                except PlanningError as error:
                    out['fingerprints'][key] = f'ERR {error.code}'
    cases = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in sorted(CONTRACTS.glob('scenario*.json'))}
    if not arguments.quick:
        cases.update(stress_20_gemini=stress(), mixed_2x201_4gemini_wind8=mixed())
    for name, data in cases.items():
        times = []
        for _ in range(arguments.runs):
            started = time.perf_counter()
            result = plan(Scenario.model_validate(data))
            times.append(time.perf_counter() - started)
        out['timing'][name] = dict(median_s=round(statistics.median(times), 3), completion_min=round(result['metrics']['completion_time_s'] / 60, 2),
                                   airtime_min=round(result['metrics']['total_airtime_s'] / 60, 2))
        print(f"{name}: {out['timing'][name]}")
    Path(arguments.output).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')


def compare(a, b):
    a, b = (json.loads(Path(p).read_text(encoding='utf-8')) for p in (a, b))
    same = [k for k in a['fingerprints'] if a['fingerprints'][k] == b['fingerprints'].get(k)]
    print(f'Одинаковых расчетов: {len(same)} из {len(a["fingerprints"])}')
    for k in a['fingerprints']:
        if k not in same:
            print('  отличается:', k, a['fingerprints'][k][:2], '→', b['fingerprints'].get(k, ['нет'])[:2])
    for k in b['fingerprints']:
        if k not in a['fingerprints']:
            print('  новый:', k, b['fingerprints'][k][:2])
    for name in a['timing']:
        if name in b['timing']:
            print(f"  время {name}: {a['timing'][name]['median_s']} → {b['timing'][name]['median_s']} с")


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('output', nargs='?')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--quick', action='store_true', help='только примеры, без больших сценариев')
    parser.add_argument('--compare', nargs=2, metavar=('A', 'B'))
    arguments = parser.parse_args()
    if arguments.compare:
        compare(*arguments.compare)
    else:
        run(arguments)
