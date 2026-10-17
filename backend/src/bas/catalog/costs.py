"""Default coefficients of the cost criterion J = α·T + cᵀt + tᵀQt from open data (edition 2026-10).

Every number is an estimate for a demonstration, not customer data; each input carries its source
and a status: `confirmed` (manufacturer document, dealer price list), `computed` (formula over
confirmed inputs), `assumed` (no public data found — a stated assumption).

α   — cost of an hour of the operation: a ground team of two at the median civil salary of a UAV
      operator with insurance contributions, plus transport and travel (assumed).
c_i — cost of an hour of flight of aircraft i: the expected loss of the aircraft (failure rate per
      flight hour × price) and the battery wear (battery price / rated cycles per hour of flight).
q_ii — re-flight risk: if aircraft i fails after τ of its t_i hours, the remaining t_i − τ hours
      of its work have to be flown again by others and the operation lasts longer. The expected
      cost is λ·(α + c_i)·t_i²/2, so q_ii = λ·(α + c_i)/2. It is small for operations of hours:
      the quadratic term matters when an operator sets it for a reason of their own (an aircraft
      close to its maintenance, two aircraft sharing one pilot or charger).
1 у.е. of this preset ≈ 1 ₽ at 2025–2026 prices.
"""
EDITION = '2026-10'

# Failures with loss of the aircraft or the mission, per flight hour.
FAILURE_RATE = dict(value=1e-3, status='confirmed',
                    source='Petritoli E., Leccese F., Ciani L. Reliability and Maintenance Analysis of Unmanned Aerial Vehicles. '
                           'Sensors, 2018, 18(9):3171, doi:10.3390/s18093171 — для БВС «около 1 отказа на 10³ летных часов» '
                           '(для гражданской авиации 1 на 10⁵). Разброс в литературе 10⁻⁴…2,2·10⁻² 1/ч (Reimann et al., '
                           'University of Minnesota, 2013: 2,2·10⁻² для малого БВС без резервирования).')
SALARY = dict(value=120_000, status='computed',
              source='Медиана диапазонов вакансий гражданских операторов БВС и фотограмметристов, 80–160 тыс. ₽/мес '
                     '(bplainfo.ru, dronschool.ru, gorodrabot.ru по вакансиям hh.ru, 2025–2026)')
CONTRIBUTIONS = dict(value=0.30, status='confirmed', source='Тариф страховых взносов работодателя 30% (НК РФ, ст. 425)')
HOURS_PER_MONTH = dict(value=165, status='computed', source='Норма рабочего времени при 40-часовой неделе ≈ 165 ч/мес')
TEAM = dict(value=2, status='assumed', source='Допущение: внешний пилот и помощник на площадке')
TRANSPORT = dict(value=600, status='assumed', source='Допущение: автомобиль, топливо и суточные ≈ 600 ₽/ч работы бригады')
ENDURANCE_SHARE = 0.75  # one battery cycle per sortie of about 75 % of the rated flight time, as the planner's budget

PROFILES = {
    'gemini': dict(
        price=dict(value=1_600_000, status='confirmed', source='Цены дилеров 1,40–1,74 млн ₽ с НДС (ecnk.ru, rusgeocom.ru, воздух.рф, 2025–2026); середина диапазона'),
        battery_price=dict(value=43_500, status='confirmed', source='Цена АКБ Gemini у дилера (rusgeocom.ru)'),
        battery_cycles=dict(value=300, status='assumed', source='Ресурс АКБ Gemini в документации не указан; принято между ≥80 циклами АКБ 201 '
                                                              '(Geoscan_201_Manual.pdf, с. 115) и 400 циклами промышленной АКБ DJI TB65 (store.dji.com)'),
    ),
    'geoscan_201': dict(
        price=dict(value=2_500_000, status='confirmed', source='Цены дилеров 2,1–3,1 млн ₽ (geo-spektr.ru, neramsc.ru, ugt-holding.ru, 2025–2026); середина диапазона'),
        battery_price=dict(value=60_000, status='assumed', source='Цена АКБ 201 в открытых источниках не найдена; допущение'),
        battery_cycles=dict(value=80, status='confirmed', source='Geoscan_201_Manual.pdf, с. 115: не менее 80 рабочих циклов'),
    ),
    'geoscan_801': dict(
        price=dict(value=3_000_000, status='assumed', source='Цена 801 в открытых источниках не найдена; допущение: дороже 201 как более тяжелый носитель'),
        battery_price=dict(value=60_000, status='assumed', source='Цена АКБ 801 в открытых источниках не найдена; допущение'),
        battery_cycles=dict(value=300, status='assumed', source='Как для Gemini (допущение)'),
    ),
}
# Aircraft profiles of the user: Gemini-class prices with their own flight time.
GENERIC = 'gemini'


def alpha():
    return TEAM['value'] * SALARY['value'] * (1 + CONTRIBUTIONS['value']) / HOURS_PER_MONTH['value'] + TRANSPORT['value']


def airtime_cost(profile_id, flight_min):
    """c for an aircraft of `profile_id` flying `flight_min` minutes per battery, у.е./ч."""
    p = PROFILES.get(profile_id, PROFILES[GENERIC])
    hours_per_cycle = flight_min * ENDURANCE_SHARE / 60
    return FAILURE_RATE['value'] * p['price']['value'] + p['battery_price']['value'] / p['battery_cycles']['value'] / hours_per_cycle


def reflight_cost(c):
    """q_ii = λ·(α + c)/2, у.е./ч²."""
    return FAILURE_RATE['value'] * (alpha() + c) / 2


def model_for(fleet, profiles):
    """The default cost model for the fleet: rounded values per aircraft by its type."""
    from bas.domain.models import CostModel
    a = round(alpha())
    airtime, diagonal = {}, {}
    for aircraft in fleet:
        profile = profiles.get(aircraft.profile_id)
        c = airtime_cost(aircraft.profile_id, profile.max_flight_time_min if profile else 40)
        airtime[aircraft.id] = round(c)
        diagonal[aircraft.id] = round(reflight_cost(c), 2)
    generic = airtime_cost(GENERIC, 40)
    return CostModel(alpha_per_h=a, airtime_default=round(generic), airtime=airtime,
                     default_diagonal=round(reflight_cost(generic), 2), diagonal=diagonal, pairs=[])


def effective(scenario, profiles):
    """The cost model a plan is computed with: the scenario's own, type coefficients written out; else the preset."""
    from bas.planning.cost import resolve
    return resolve(scenario.cost_model, scenario.fleet) if scenario.cost_model is not None else model_for(scenario.fleet, profiles)


def document(profiles):
    """The preset with every input and its source, for the interface and the docs."""
    by_profile = {}
    for pid in sorted(set(PROFILES) | {p.id for p in profiles}):
        profile = next((p for p in profiles if p.id == pid), None)
        flight = profile.max_flight_time_min if profile else 40
        c = airtime_cost(pid, flight)
        base = PROFILES.get(pid, PROFILES[GENERIC])
        by_profile[pid] = dict(airtime=round(c), diagonal=round(reflight_cost(c), 2), generic=pid not in PROFILES,
                               loss=round(FAILURE_RATE['value'] * base['price']['value']), battery=round(c - FAILURE_RATE['value'] * base['price']['value']),
                               inputs=base)
    return dict(edition=EDITION, units='у.е. (≈ ₽ в ценах 2025–2026)', alpha_per_h=round(alpha()),
                alpha_inputs=dict(salary=SALARY, contributions=CONTRIBUTIONS, hours_per_month=HOURS_PER_MONTH, team=TEAM, transport=TRANSPORT),
                failure_rate=FAILURE_RATE, endurance_share=ENDURANCE_SHARE, by_profile=by_profile,
                formulas=dict(alpha='α = бригада × зарплата × (1 + взносы) / часов в месяце + транспорт',
                              airtime='c = λ × цена борта + цена АКБ / циклов / (75% паспортного полета, ч)',
                              diagonal='q = λ × (α + c) / 2 — ожидаемая цена повторного облета работы борта при его отказе'))
