# Контракты

Входной контракт описан Pydantic-моделями Scenario и OpenAPI. Пример рядом можно
импортировать в интерфейс или передать на POST /api/scenarios.
Координаты — [долгота, широта] WGS84; ветер — м/с, направление «откуда дует»,
градусы по часовой стрелке от севера.

schema_version=0.1 допускает сохранение неполного черновика. Перед расчетом версия 0.2
отдельно требует разрешенное пространство, коптеры и назначенные основные площадки.
Новые settings и start_site_id имеют значения по умолчанию для старых снимков.

Результат пока возвращается как JSON без полной Pydantic-модели ответа.
Актуальная структура — planning/service.py и frontend/src/shared/types.ts.
FlightLeg в domain/models.py — старый эскиз будущего типизированного контракта,
он не используется для валидации текущего ответа. Перед генерацией типов результатов
необходимо согласовать и подключить полный response_model.

GeoJSON остается двумерным. Высоты AGL, время, скорости и предупреждения находятся
в свойствах; это демонстрационный экспорт для анализа, не задание автопилота.

## Расширение 21.09.2026

`scenario.fixedwing.example.json` — воспроизводимый пример 201 + Gemini с камерами.
Необязательные поля борта: `airspeed_mps`, `camera_id`, `trigger_interval_s`.
Настройки: `coverage_mode`, `front_overlap_percent`, `max_gsd_cm`, `fixed_wing`.
Старые сценарии без этих полей используют ручную ширину захвата.
Подробности контракта и ограничений: [CAMERAS_AND_FIXED_WING.md](../docs/CAMERAS_AND_FIXED_WING.md).

`scenario.example.json` и `scenario.fixedwing.example.json` теперь содержат по
одной площадке `"kind": "alternate"`, достижимой в примере расчета. Это опциональный
элемент `sites`; для нее план добавляет проверку достижимости с оставшимся ресурсом.
Подробности: [RESERVE_SITES.md](../docs/RESERVE_SITES.md).

## Расширение версии 0.6

`scenario.multispectral.example.json` — Gemini с камерой Geoscan Pollux,
`survey_type: "multispectral"`, `coverage_mode: "camera"`. Каждый борт в `fleet`
должен иметь `payload`, совпадающий с `survey_type` сценария. Для ИК/LiDAR/геофизической
съемки подтвержденного профиля камеры нет — используйте `coverage_mode: "manual"`
с ручной `swath_width_m`. Подробности: [SURVEY_TYPES.md](../docs/SURVEY_TYPES.md).

## Расширение версии 0.7

Новое необязательное поле `settings.landing_policy`: `"return_to_start"` (по
умолчанию, прежнее поведение) или `"best_main_site"` — борт садится на ближайшую
по времени допустимую основную площадку. Старые сценарии без поля считаются как
раньше. В каждой миссии результата появилось `landing_site_id` рядом с `site_id`
(площадкой старта); в планах до 0.7 его нет, и они всегда возвращались на площадку
старта. Подробности: [LANDING_SITES.md](../docs/LANDING_SITES.md).

## Расширение версии 0.8

Все новые поля необязательны; сценарии 0.7 считаются как раньше.

- `restriction_zones` — зоны ограничений: `id`, `name`, `category`
  (`prohibited|restricted|temporary|danger|other`), `geometry` (Polygon), `lower`/`upper`
  (`{"reference": "GND|AGL|AMSL|FL|UNL", "value": число}`), `validity`
  (`kind: permanent|scheduled|unknown`, `periods`, `weekly`, `timezone`), `notes`,
  `source_text`, `parsed`. Обмен в KML — [zones.example.kml](zones.example.kml).
- `obstacles` — препятствия: `id`, `kind`, `geometry` (Point/LineString/Polygon),
  `radius_m`, `height_m`, `height_reference` (`AGL|AMSL`).
- `additional_survey_areas` — до 199 (всего 200 зон съемки).
- `settings`: `terrain_min_m`, `terrain_max_m`, `vertical_buffer_m`,
  `obstacle_clearance_m`, `blocked_area_policy` (`reject|exclude`), `operation_start`
  (ISO с часовым поясом), `time_limit_min`, `until_sunset`, `max_aircraft`,
  `sorties_per_aircraft`, `turnaround_min`.

В результате: `airspace` (какие зоны и препятствия учтены и почему, исключенная
площадь), `constraints` (срок, его источник, укладывается ли план, лимит бортов,
окончание работ), в миссиях — `sortie`, в метриках — `excluded_area_m2`,
`sortie_count`, `maintenance`, `maintenance_share_total`, у зон покрытия —
`excluded_m2`. `aircraft_used` теперь считает борта, а не вылеты.
Подробности: [AIRSPACE_AND_CUSTOMER_DATA.md](../docs/AIRSPACE_AND_CUSTOMER_DATA.md).

## Расширение версии 0.9

Все поля необязательны, сценарии 0.8 считаются как раньше.

- `sites[].chargers` — число зарядных устройств на площадке (каждое заряжает одну АКБ за
  раз). Без поля: 2 на основной площадке, 0 на запасной.
- `settings.recharge_mode` — `"swap"` (по умолчанию: замена АКБ за `turnaround_min`) или
  `"charge"` (зарядка своей АКБ на ЗУ площадки), `settings.charge_time_min` (полная
  зарядка, если в профиле нет своего значения, по умолчанию 105), `settings.ground_handling_min`
  (обслуживание перед зарядкой, 5). `sorties_per_aircraft` — до 20.
- Профили БПЛА: `charge_time_min`, `charge_source`.

В миссиях результата: `ground_s` (время на земле перед повторным вылетом),
`charge_site_id`, `charge_s`, `charge_start_s`, `charge_end_s`, `charge_wait_s`,
`battery_start_percent`, `battery_end_percent`; `site_id` повторного вылета — площадка, где
борт заряжался. В метриках — `charging_s` и `charger_wait_s`.

## Расширение версии 1.1

Все новые поля необязательны; сценарии 1.0 считаются как раньше.

- Борт: `altitude_m` — своя высота полета (AGL, 20–300 м, съемка и перелеты). Без нее борт
  летит на `settings.altitude_m`.
- Настройки: `echelons` (автоматические эшелоны и разведение по высоте, по умолчанию `false`),
  `vertical_separation_m` (20), `altitude_ceiling_m` (150).
- `settings.fixed_wing`: `lead_in_m` — прямой заход перед галсом (50 м), `parachute_release_m` —
  высота выпуска парашюта (`null` — с высоты полета).
- Результат: у миссии `altitude_m`, у съемки самолета `crab_deg` и `nominal_swath_width_m`; у
  плана `echelons` (уровни, общие участки маршрутов на одной высоте до и после) и
  `separation` (`horizontal_m`, `vertical_m`; `null` — только по горизонтали).

`scenario.echelons.example.json` — 8 Gemini с двух площадок: по четыре летят к дальнему району общим коридором.
Подробности: [ECHELONS_AND_FIXED_WING.md](../docs/ECHELONS_AND_FIXED_WING.md).
