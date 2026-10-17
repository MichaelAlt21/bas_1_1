# API KUMOsystems 1.2

REST API на FastAPI. Интерактивная документация и схема — на работающем сервере:
`/docs` (Swagger UI) и `/openapi.json`. Все пути ниже — относительно `http://127.0.0.1:8000`.
Сообщения об ошибках — на русском. Сервис рассчитан на локальный запуск: авторизации нет,
кроме административного токена для настроек БД (см. «Ограничения»).

## Типичный сценарий

```text
POST /api/scenarios            сценарий (JSON)          → {id, created_at, scenario}
POST /api/planning-runs        {"scenario_id": id}      → план (JSON)
GET  /api/plans/{plan_id}/export?format=kml&aircraft_id=Gemini-01   → полетное задание борта
GET  /api/plans/{plan_id}/report                         → печатный HTML-отчет
```

Пример на PowerShell:

```powershell
$scenario = Get-Content contracts\scenario.echelons.example.json -Raw -Encoding utf8
$stored = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/scenarios -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($scenario))
$plan = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/planning-runs -ContentType 'application/json' -Body (@{scenario_id=$stored.id} | ConvertTo-Json)
$plan.metrics.completion_time_s / 60
```

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/health` | Состояние сервера и БД, версия |
| GET | `/api/catalog/aircraft` | Профили БПЛА: встроенные (Gemini, 801, 201) и пользовательские |
| POST | `/api/catalog/aircraft` | Создать пользовательский профиль → 201 |
| GET | `/api/catalog/cameras` | Профили камер с источниками параметров (формат 1.1) |
| GET | `/api/catalog/cost-preset` | Коэффициенты J по открытым данным: α, c и q по типам бортов, каждое исходное число со статусом и источником |
| GET | `/api/catalog/sensors` | Профили сенсоров: встроенные и последние версии пользовательских, с пометками источников |
| GET | `/api/catalog/sensors/{id}/versions` | Все версии пользовательского профиля |
| POST | `/api/catalog/sensors` | Создать пользовательский профиль → 201; необязательный `id`, иначе из названия |
| PUT | `/api/catalog/sensors/{id}` | Изменить пользовательский профиль: сохраняется новая версия; встроенный → 409 |
| GET | `/api/catalog/sensors/export` | Файл обмена `kumo-sensor-profiles` v1; необязательный `ids=a,b` |
| POST | `/api/catalog/sensors/import` | Импорт файла обмена; `on_conflict=reject\|new_version\|rename`; при `reject` конфликты → 409 `{code, message, conflicts}`, ничего не импортируется |
| POST | `/api/scenarios` | Проверить и сохранить снимок сценария → 201; ошибки модели → 422 |
| GET | `/api/scenarios` | Последние 50 сохраненных сценариев |
| GET | `/api/scenarios/{id}` | Снимок сценария |
| POST | `/api/planning-runs` | Расчет плана по `scenario_id` и сохранение в архив; план не построен → 422 `{code, message, diagnostics}` |
| POST | `/api/scenarios/{id}/fleet-analysis` | Анализ парка: срок и налет для 1…N бортов, сколько нужно к сроку |
| POST | `/api/task-estimate` | Оценка всего задания по пакетам: вылеты и рабочие дни |
| GET | `/api/plans` | Архив расчетов, `limit` (≤ 50) и `offset` |
| GET | `/api/plans/{id}` | Сохраненный план |
| GET | `/api/plans/{id}/export` | `format=geojson\|kml`, необязательный `aircraft_id` — задание одного борта |
| GET | `/api/plans/{id}/report` | HTML-отчет для печати из сохраненного плана без пересчета |
| POST | `/api/imports/kml` | Импорт KML заказчика: тело — файл KML (до 25 МБ), `kind=survey\|zones\|obstacles`, необязательная рамка `west,south,east,north` |
| POST | `/api/exports/zones-kml` | Зоны ограничений в KML формата KUMO |
| GET | `/api/weather/current` | Ветер и порывы Open-Meteo для `latitude`, `longitude` |
| GET | `/api/sun` | Восход, закат, гражданские сумерки для `latitude`, `longitude`, `date` |
| GET | `/api/settings/database` | Настройки PostgreSQL; заголовок `X-Kumo-Admin-Token` |
| POST | `/api/settings/database/check`, `/prepare`, `/save`, `/local` | Проверка, подготовка схемы, сохранение, возврат к SQLite; тот же заголовок |

## Сценарий (вход)

Полный контракт — модели Pydantic в `backend/src/bas/domain/models.py`, примеры — в
[contracts/](../contracts/), описание полей по версиям — [contracts/README.md](../contracts/README.md).

| Поле | Содержание |
|---|---|
| `survey_area`, `additional_survey_areas` | Районы съемки, GeoJSON Polygon WGS84, до 200 |
| `allowed_airspace` | Разрешенное пространство (обязательно для расчета) |
| `no_fly_zones`, `restriction_zones`, `obstacles` | Запреты; зоны с высотами (GND/AGL/AMSL/FL/UNL) и временем действия; препятствия с высотой |
| `sites` | Площадки: `launch_landing` или `alternate`, число ЗУ |
| `fleet` | Борта: профиль, заряд, нагрузка, площадка, скорость, камера, интервал снимков, своя высота; с 1.2 — `sensors` (список профилей сенсоров борта) |
| `weather` | Ветер: скорость и откуда дует; необязательный снимок Open-Meteo |
| `survey_type` | `rgb`, `multispectral`, `thermal`, `lidar`, `geophysical` (сценарии без `requirements`) |
| `area_ids` | 1.2: идентификаторы зон в порядке `survey_area`, `additional_survey_areas` |
| `requirements` | 1.2: требования съемки — зоны, вид, перекрытия, предел GSD, высота, полоса, шаг линий, скорость; `null` — один вид на все зоны, как в 1.1 ([подробно](MULTI_SURVEY_AND_SENSORS.md)) |
| `sensor_profiles` | 1.2: снимок профилей сенсоров бортов; заполняется сервером при сохранении |
| `cost_model` | 1.2: `alpha_per_h`, `airtime_default` и `airtime` {борт: c} (цена часа налета; без них c = 0), `default_diagonal`, `diagonal` {борт: q}, `by_profile` {тип: {airtime, diagonal}} (для всех бортов типа без своего значения), `pairs` [{a, b, q}]; `null` — коэффициенты по открытым данным (`GET /api/catalog/cost-preset`) ([критерий J](COST_CRITERION.md)) |
| `objective` | `min_completion_time`, `min_total_airtime` или `min_cost` |
| `settings` | Высота съемки, скорости, захват или камеры, перекрытия, резерв, вылеты и зарядка, сроки, эшелоны, параметры самолета, рельеф и запасы; с 1.2 — `payload_change_min` (смена сенсора, мин) |

## План (выход)

| Поле | Содержание |
|---|---|
| `missions[]` | Вылеты: `aircraft_id`, `sortie`, с 1.2 — `sensor_id`, `sensor_name`, `requirement_ids`, `surveys` по требованиям, `change_s` (смена нагрузки перед вылетом); `site_id` → `landing_site_id`, `altitude_m`, `airspeed_mps`, `start_s`/`finish_s` от начала операции, `flight_time_s`, `budget_s`, заряд до и после, время на земле и зарядку, `survey` (захват, GSD, снимки, снос у самолета), `alternate` (резервная площадка), `legs[]` |
| `missions[].legs[]` | Этапы полета по порядку: `phase` (`takeoff`, `climb`, `transit`, `turn`, `survey`, `hold`, `descent`, `parachute`, `landing`), `coordinates` WGS84, `start`/`end`, высоты начала и конца (AGL), `speed_mps` и `speed_reference` (`air`/`ground`), `vertical_speed_mps`; у съемки — число снимков, шаг и интервал |
| `metrics` | Срок, суммарный налет и путь, площадь и покрытие (в том числе по зонам), бортов и вылетов, зарядка, расход ресурса до ТО, направление галсов, время расчета; с 1.2 — `coverage_by_requirement` |
| `cost` | 1.2: J и разложение: `linear` (α·T), `airtime_total` (cᵀt), `quadratic` (tᵀQt), `individual[]` (c·t и q·t² по бортам), `pairs[]` (2·q·t·t), `completion_h`, `airtime_h`, `aircraft_used`, `model`, `default_model`, `search`; `tradeoff` — варианты плана (T, налет, цена налета, J), выбор и диапазон α на нижней оболочке |
| `diagnostics` | 1.2: замечания к плану в формате диагностики (см. ниже) |
| `constraints` | Срок и его источник (вручную или закат), укладывается ли план, начало и окончание работ, лимит бортов |
| `echelons` | Уровни, применены ли, общие участки маршрутов на одной высоте до и после, срок и налет на одной высоте |
| `separation` | Интервалы разведения: `horizontal_m`, `vertical_m` (`null` — только по горизонтали) |
| `airspace` | Какие зоны и препятствия учтены, выше/ниже полета, неактивны; исключенная площадь |
| `warnings` | Допущения и предупреждения модели для этого плана |
| `status`, `algorithm_version` | `demonstration`, версия алгоритма (`1.2`) |

GeoJSON и KML экспорта содержат те же этапы: у каждого участка — фаза, время, высоты AGL,
скорости; в KML высоты передаются как `relativeToGround`. GeoJSON остается двумерным, высоты — в
свойствах. Экспорт — для анализа и переноса в ГИС, не для загрузки в автопилот.

## Коды ошибок расчета

`422` с `{code, message, diagnostics}`: например `SITE_REQUIRED`, `FLEET_REQUIRED`, `WIND_LIMIT`,
`AREA_BLOCKED`, `AREA_UNREACHABLE`, `CAPACITY`, `GSD_LIMIT`, `EQUIPMENT_MISSING`, `FIXED_WING_SITE`,
`NO_PLAN_FOUND`. Сообщение объясняет причину и что изменить. `diagnostics[]` — структурированно:
`code`, `severity`, `category` (`input`, `equipment`, `constraint`, `search`, `budget`, `internal`),
`title`, `message`, `subjects` (зоны, борта, площадки, требования), `actual` и `required` с
единицами, `actions` (`navigate` — путь к полю `{tab, section, field, map}`; `apply` — изменение
с описанием, применяется только по действию оператора) — [подробно](DIAGNOSTICS_AND_SEARCH.md).
Ошибки проверки полей (422 `{detail: [...]}`) тоже содержат `diagnostics` с путем к полю.
Внутренняя ошибка — 500 `{detail: {code: "INTERNAL", message}}` с кодом для журнала сервера.

## Ограничения

- Авторизации и разграничения доступа нет: сервер слушает `127.0.0.1`. Для общего сервера нужны
  авторизация, ограничение частоты запросов и размера входа.
- Расчет синхронный: большой анализ парка может занять десятки секунд. Очереди задач нет.
- Административный токен для настроек БД создается в файле `.runtime/admin-token` на сервере
  (или задается переменной `KUMO_ADMIN_TOKEN`); пароль PostgreSQL задается переменной окружения и
  не сохраняется.
