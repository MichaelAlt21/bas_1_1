"""Self-contained printable report from saved inputs; no live services or recalculation."""
from html import escape

from bas.domain.models import StoredScenario


def mission_report(plan: dict, stored: StoredScenario) -> str:
    def text(value):
        return escape(str(value), quote=True)

    def minutes(value):
        return f'{value / 60:.1f} мин'

    def table(headers, rows):
        head = ''.join(f'<th scope="col">{text(h)}</th>' for h in headers)
        body = ''.join('<tr>'+''.join(f'<td>{text(c)}</td>' for c in row)+'</tr>' for row in rows)
        return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    scenario = stored.scenario
    metrics = plan['metrics']
    objective = 'Быстрее завершить операцию' if plan['objective']=='min_completion_time' else 'Меньше суммарный налет'
    profiles = {p.id:p for p in scenario.aircraft_profiles}
    sites = {s.id:s for s in scenario.sites}
    fleet = {a.id:a for a in scenario.fleet}
    timestamp = plan.get('created_at')
    date_label = 'Расчет сохранен (UTC)' if timestamp else 'Дата исходного снимка (UTC); время расчета не записано'
    timestamp = timestamp or stored.created_at
    def alternate_label(mission):
        alt = mission.get('alternate') or {}
        if not alt.get('checked'):
            return 'Не задано'
        if not alt.get('reachable'):
            return 'Недостижим'
        return sites[alt['site_id']].name if alt.get('site_id') in sites else str(alt.get('site_id'))
    def ground_label(mission):
        # Plans before 0.9 carry no ground time; charging shows its wait for a free charger.
        if mission.get('charge_s') is not None:
            wait = mission.get('charge_wait_s') or 0
            return f"зарядка {minutes(mission['charge_s'])}" + (f", ожидание ЗУ {minutes(wait)}" if wait > 1 else '')
        if mission.get('ground_s') is not None:
            return f"замена АКБ {minutes(mission['ground_s'])}"
        return '—'
    def site_name(site_id):
        return sites[site_id].name if site_id in sites else str(site_id)
    mission_rows = []
    for mission in plan['missions']:
        aircraft = fleet.get(mission['aircraft_id'])
        profile = profiles.get(mission['profile_id'])
        # Plans before 0.7 have no landing_site_id: they always returned to the launch site.
        landing_id = mission.get('landing_site_id', mission['site_id'])
        # Plans before 0.8 fly one sortie per aircraft; repeat sorties take a fresh battery.
        sortie = mission.get('sortie', 1)
        mission_rows.append([
            mission['aircraft_id'] + (f' · рейс {sortie}' if sortie > 1 else ''), profile.name if profile else mission['profile_id'],
            site_name(mission['site_id']), 'та же' if landing_id == mission['site_id'] else site_name(landing_id),
            # Plans before 1.1 fly every aircraft at the survey altitude.
            f"{mission.get('altitude_m', scenario.settings.altitude_m):g} м",
            '100% (новая АКБ)' if sortie > 1 else f'{aircraft.battery_percent:g}%' if aircraft else '—',
            ground_label(mission), minutes(mission['start_s']), minutes(mission['finish_s']), minutes(mission['flight_time_s']),
            minutes(mission['budget_s']), f"{mission['distance_m']/1000:.2f} км", alternate_label(mission)])
    missions = table(['Борт','Модель','Старт','Посадка','Высота','Заряд','Перед вылетом','Старт +','Посадка +','Налет','Бюджет времени','Путь','Резерв'],mission_rows)
    separation = plan.get('separation') or {}
    if separation.get('vertical_m'):
        missions += (f"<p>Разведение: не ближе {separation['horizontal_m']:g} м по горизонтали или {separation['vertical_m']:g} м по высоте; "
                     'на наборе и снижении высота меняется линейно.</p>')
    zones = ''
    if metrics.get('coverage_by_area'):
        excluded = any(a.get('excluded_m2') for a in metrics['coverage_by_area'])
        zones = '<h2>Зоны съемки</h2>'+table(['Зона','Площадь','Геометрическое покрытие']+(['Исключено ограничениями'] if excluded else []),[
            (f"Зона {a['index']}",f"{a['area_m2']/10000:.1f} га",f"{a['coverage_percent']:.1f}%")+((f"{a.get('excluded_m2',0)/10000:.2f} га",) if excluded else ())
            for a in metrics['coverage_by_area']])+'<p>В общей площади пересечения зон учтены один раз.</p>'
    constraints = plan.get('constraints') or {}
    limits = ''
    if constraints.get('time_limit_s') is not None or constraints.get('max_aircraft') is not None:
        verdict = {True: 'укладывается', False: 'не укладывается', None: '—'}[constraints.get('meets_time_limit')]
        limits = '<h2>Ограничения задания</h2>'+table(['Параметр','Значение'],[
            ('Срок', minutes(constraints['time_limit_s'])+(' (до заката)' if constraints.get('time_limit_source')=='sunset' else '') if constraints.get('time_limit_s') is not None else 'не задан'),
            ('Завершение', minutes(constraints['completion_s'])+f' — {verdict}'),
            ('Начало / окончание работ', f"{constraints.get('operation_start') or '—'} / {constraints.get('operation_end') or '—'}"),
            ('Ограничение числа бортов', constraints['max_aircraft'] if constraints.get('max_aircraft') is not None else 'нет'),
            ('Задействовано бортов / вылетов', f"{constraints.get('aircraft_used')} / {constraints.get('sorties')}")])
    upkeep = ''
    if metrics.get('maintenance'):
        unit = {'flights': 'полетов', 'flight_hours': 'ч налета', 'motor_hours': 'моточасов'}
        upkeep = '<h2>Расход ресурса до ТО</h2>'+table(['Борт','Вылетов','Налет','Норматив ТО','Доля интервала','Источник норматива'],[
            (m['aircraft_id'], m['sorties'], minutes(m['flight_time_s']),
             f"{m['interval']:g} {unit.get(m['unit'], m['unit'])}" if m['interval'] else 'нет данных',
             f"{m['interval_share']*100:.2f}%" if m['interval_share'] is not None else '—', m['source'] or '—')
            for m in metrics['maintenance']])
    space = plan.get('airspace') or {}
    airspace = ''
    if scenario.restriction_zones or scenario.obstacles:
        applied = ', '.join(z['name'] or z['id'] for z in space.get('zones_applied', [])[:20]) or 'нет'
        airspace = '<h2>Воздушное пространство</h2>'+table(['Показатель','Значение'],[
            ('Зоны ограничений: учтены', f"{len(space.get('zones_applied', []))} ({applied})"),
            ('Зоны выше полета / неактивны в окне', f"{len(space.get('zones_above', []))} / {len(space.get('zones_inactive', []))}"),
            ('Препятствия: облетаются / ниже полета', f"{len(space.get('obstacles_applied', []))} / {space.get('obstacles_below', 0)}"),
            ('Рельеф района (AMSL)', f"{space.get('terrain_min_m', '—')}–{space.get('terrain_max_m', '—')} м"),
            ('Исключено из съемки', f"{space.get('excluded_area_m2', 0)/10000:.2f} га")])
    settings = scenario.settings
    parameters = table(['Параметр','Значение'],[
        ('Вид съемки',scenario.survey_type.upper()),('Высота съемки над землей (AGL)',f'{settings.altitude_m:g} м'),
        ('Эшелоны','Автоматически, шаг '+f'{settings.vertical_separation_m:g} м, потолок {settings.altitude_ceiling_m:g} м' if settings.echelons
         else 'Заданы вручную' if any(a.altitude_m is not None for a in scenario.fleet) else 'Нет, одна высота'),
        ('Скорость коптера по умолчанию',f'{settings.airspeed_mps:g} м/с'),
        ('Расчет покрытия','По камерам' if settings.coverage_mode=='camera' else f'Ручной захват {settings.swath_width_m:g} м'),
        ('Боковое перекрытие',f'{settings.side_overlap_percent:g}%'),('Резерв',f'{settings.reserve_percent:g}%'),
        ('Коэффициент паспортной длительности',settings.endurance_factor),('Отступ от границ',f'{settings.boundary_clearance_m:g} м'),
        ('Запретные зоны',len(scenario.no_fly_zones)),
        ('Зоны ограничений / препятствия',f'{len(scenario.restriction_zones)} / {len(scenario.obstacles)}'),
        ('Вертикальный запас / отступ от препятствий',f'{settings.vertical_buffer_m:g} м / {settings.obstacle_clearance_m:g} м'),
        ('Заблокированные участки','Исключаются' if settings.blocked_area_policy=='exclude' else 'Запрещают расчет'),
        ('Вылетов на борт',settings.sorties_per_aircraft),
        ('Между вылетами',f'Зарядка на площадке (полная {settings.charge_time_min:g} мин, если не указано в профиле; обслуживание {settings.ground_handling_min:g} мин)'
         if settings.recharge_mode=='charge' else f'Замена АКБ, {settings.turnaround_min:g} мин'),
        ('Посадка','На любую основную площадку' if settings.landing_policy=='best_main_site' else 'На площадку старта')])
    camera_rows=[]
    plane_rows=[]
    for mission in plan['missions']:
        survey=mission.get('survey',{})
        if survey.get('mode')=='camera':
            camera_rows.append((mission['aircraft_id'],survey['camera']['name'],f"{mission['airspeed_mps']:g} м/с",
                                f"{survey['gsd_cm']:.2f} см/пикс",f"{survey['swath_width_m']:.1f} м"+(f" (снос {survey['crab_deg']:.0f}°)" if survey.get('crab_deg',0)>=.5 else ''),
                                survey['photo_count'],f"{survey['min_photo_interval_s']:.2f} с",survey['camera']['source']))
        assumptions=mission.get('fixed_wing_assumptions')
        if assumptions:
            # Plans before 1.1 carry no bank, lead-in, spiral or release height.
            plane_rows.append((mission['aircraft_id'],f"{mission['airspeed_mps']:g} м/с",
                               f"{assumptions['turn_radius_m']:g} м"+(f" (крен ≈ {assumptions['bank_deg']:.0f}°)" if 'bank_deg' in assumptions else ''),
                               f"{assumptions.get('lead_in_m', 0):g} м",
                               f"{assumptions['climb_rate_mps']:g} м/с"+(' · по спирали' if assumptions.get('spiral_climb') else ''),
                               f"{assumptions['parachute_sink_mps']:g} м/с с {assumptions.get('release_altitude_m', mission.get('altitude_m', settings.altitude_m)):g} м",
                               f"{assumptions['launch_heading_deg']:g}°"))
    cameras = ('<h2>Камеры и требуемые параметры съемки</h2>'+table(['Борт','Камера','Скорость','GSD','Захват','Снимки','Мин. интервал','Источник'],camera_rows)
               +f'<p>Продольное перекрытие: {settings.front_overlap_percent:g}%. Ограничение GSD: {text(settings.max_gsd_cm if settings.max_gsd_cm is not None else "не задано")} см/пикс. Номинальная геометрия надира; интервал съемки требует проверки по конкретной комплектации.</p>') if camera_rows else ''
    planes = ('<h2>Допущения самолетной модели</h2>'+table(['Борт','Скорость','Радиус в штиль','Заход на галс','Набор','Парашют','Курс запуска'],plane_rows)
              +'<p>Развороты с постоянным креном; в ветре траектория разворота вытягивается по ветру. Радиус, набор, заход и спуск — настраиваемые параметры демонстрации, '
               'не подтвержденные изготовителем. Запуск условно занимает 50 м, завершение посадки — 20 с.</p>') if plane_rows else ''
    places = table(['Название','Назначение','Зарядных мест','Широта','Долгота'],[
        (s.name,'Запасная' if s.kind=='alternate' else 'Старт и посадка',s.charger_count(),f'{s.coordinates[1]:.6f}',f'{s.coordinates[0]:.6f}')
        for s in scenario.sites])
    observation = scenario.weather.observation
    weather = f'Ветер {scenario.weather.wind_speed_mps:g} м/с, откуда дует: {scenario.weather.wind_from_deg:g}°. '
    if observation:
        weather += (f'Источник: Open-Meteo, снимок {observation.valid_at}; ветер на высоте 10 м. '
                    f'Порывы {observation.wind_gusts_mps:g} м/с, температура {observation.temperature_c:g} °C, '
                    f'осадки {observation.precipitation_mm:g} мм. Это сохраненные условия расчета, не текущая погода.')
    else:
        weather += 'Источник: ручной ввод в исходном сценарии.'
    warnings = ''.join(f'<li>{text(w)}</li>' for w in plan['warnings'])
    sources = table(['Модель','Происхождение','Источник характеристик'],[
        (p.name,'Пользовательская' if p.origin=='user' else 'Каталог',p.source) for p in profiles.values()])
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Отчет · {text(scenario.name)} · KUMOsystems</title>
<style>
*{{box-sizing:border-box}}body{{font:14px/1.55 Arial,sans-serif;margin:0;color:#1F2328;background:#EEF0F2}}
main{{max-width:1120px;margin:32px auto;padding:40px;background:white;border-top:6px solid #F26B1D}}
.brand{{font-weight:800;letter-spacing:1px;color:#22262B}}.brand b{{color:#F26B1D}}h1{{font-size:30px;line-height:1.2;overflow-wrap:anywhere}}h2{{font-size:18px;margin-top:30px}}
.meta{{font-size:12px;color:#5B636E;overflow-wrap:anywhere}}.notice{{padding:14px 18px;background:#fff6de;border-left:4px solid #b78b25}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}.metrics div{{padding:14px;background:#F6F7F8;border:1px solid #DDE1E5;border-radius:6px}}.metrics strong{{display:block;font-size:22px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{text-align:left;padding:9px 7px;border-bottom:1px solid #DDE1E5;vertical-align:top;overflow-wrap:anywhere}}th{{background:#F6F7F8}}.table-wrap{{overflow-x:auto}}li{{margin:8px 0}}
footer{{margin-top:30px;border-top:1px solid #DDE1E5;padding-top:12px;font-size:12px;color:#5B636E}}
@media(max-width:700px){{main{{margin:0;padding:20px}}.metrics{{grid-template-columns:repeat(2,1fr)}}}}
@page{{size:A4 landscape;margin:14mm}}@media print{{body{{background:white;font-size:11px}}main{{max-width:none;margin:0;padding:0}}.screen-only{{display:none}}h2{{break-after:avoid}}tr,.metrics,.notice{{break-inside:avoid}}thead{{display:table-header-group}}.table-wrap{{overflow:visible}}table{{font-size:10px}}}}
</style></head><body><main>
<div class="brand"><b>■</b> KUMOsystems / ОТЧЕТ ПО ОПЕРАЦИИ</div>
<p class="screen-only">Для печати или сохранения в PDF используйте меню браузера «Печать» (Ctrl+P).</p>
<h1>{text(scenario.name)}</h1><p>{text(objective)}</p>
<p class="meta">{text(date_label)}: {text(timestamp)}<br>План: {text(plan['id'])}<br>Исходный снимок: {text(stored.id)}<br>Алгоритм: {text(plan.get('algorithm_version','не записан'))}</p>
<p class="notice"><strong>Демонстрационный расчет.</strong> Документ предназначен для предварительного планирования. Это не разрешение на полет и не задание для автопилота.</p>
<div class="metrics"><div>Завершение<strong>{text(minutes(metrics['completion_time_s']))}</strong></div>
<div>Суммарный налет<strong>{text(minutes(metrics['total_airtime_s']))}</strong></div>
<div>Площадь<strong>{metrics['area_m2']/10000:.1f} га</strong></div>
<div>Геометрическое покрытие<strong>{metrics['coverage_percent']:.1f}%</strong></div></div>
{zones}{limits}<h2>Миссии и расписание</h2><p>Время старта и посадки отсчитывается от начала операции. Бюджет времени уже учитывает расчетный резерв.</p>{missions}
{upkeep}<h2>Параметры съемки</h2>{parameters}{airspace}{cameras}{planes}<h2>Условия расчета</h2><p>{text(weather)}</p>
<h2>Площадки · WGS84</h2>{places}<h2>Характеристики парка</h2>{sources}
<h2>Допущения и ограничения</h2><ul>{warnings}</ul>
<p>Эвристика выбирает из двух направлений галсов (для самолета в ветре — еще вдоль и поперек ветра). Глобальная оптимальность не доказана.</p>
<footer>Отчет сформирован из сохраненного плана и его исходного сценария без повторного расчета.
Карта и геометрия маршрутов в этот документ не включены; для них используйте экспорт GeoJSON/KML.</footer>
</main></body></html>'''
