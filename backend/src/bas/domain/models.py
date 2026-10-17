import math
from datetime import date, time
from typing import Annotated, Literal, Union
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from shapely.geometry import shape
from shapely.validation import explain_validity


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


Lon = Annotated[float, Field(ge=-180, le=180)]
Lat = Annotated[float, Field(ge=-90, le=90)]
Position = tuple[Lon, Lat]


class Polygon(Model):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[Position]] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_geometry(self):
        if sum(map(len, self.coordinates)) > 10000:
            raise ValueError("Максимум 10000 вершин в полигоне")
        for ring in self.coordinates:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Кольцо должно быть замкнуто и содержать минимум 4 точки")
        polygon = shape(self.model_dump())
        if not polygon.is_valid or polygon.area == 0:
            raise ValueError(f"Некорректный полигон: {explain_validity(polygon)}")
        return self


class Site(Model):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["launch_landing", "alternate"]
    coordinates: Position
    # Battery chargers on the site. The Gemini and 801 chargers take batteries one at a time,
    # so each charger is one charging place. None: two at a main site, none at a reserve site.
    chargers: int | None = Field(default=None, ge=0, le=50)

    def charger_count(self) -> int:
        if self.chargers is not None:
            return self.chargers
        return 2 if self.kind == "launch_landing" else 0


class PointGeometry(Model):
    type: Literal["Point"] = "Point"
    coordinates: Position


class LineGeometry(Model):
    type: Literal["LineString"] = "LineString"
    coordinates: list[Position] = Field(min_length=2, max_length=10000)


FOOT_M = 0.3048


class VerticalLimit(Model):
    """GND/UNL carry no value; AGL/AMSL are metres; FL is a flight level (hundreds of feet)."""
    reference: Literal["GND", "AGL", "AMSL", "FL", "UNL"]
    value: float = Field(default=0, ge=-500, le=100000)

    def above_ground_m(self, terrain_m: float) -> float:
        if self.reference == "GND": return 0.0
        if self.reference == "UNL": return math.inf
        if self.reference == "AGL": return self.value
        if self.reference == "AMSL": return self.value - terrain_m
        # Pressure altitude of the standard atmosphere, taken as AMSL: no QNH is available.
        return self.value * 100 * FOOT_M - terrain_m


Weekday = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class TimeWindow(Model):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Окончание периода должно быть позже начала")
        return self


class WeeklyWindow(Model):
    days: list[Weekday] = Field(min_length=1, max_length=7)
    start: time
    end: time


class Validity(Model):
    """permanent — always; scheduled — only within periods and/or weekly windows; unknown — treated as always."""
    kind: Literal["permanent", "scheduled", "unknown"] = "permanent"
    periods: list[TimeWindow] = Field(default_factory=list, max_length=100)
    weekly: list[WeeklyWindow] = Field(default_factory=list, max_length=20)
    timezone: str = Field(default="Europe/Moscow", max_length=64)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"Неизвестный часовой пояс: {value}") from None
        return value

    @model_validator(mode="after")
    def schedule_present(self):
        if self.kind == "scheduled" and not (self.periods or self.weekly):
            raise ValueError("Для расписания укажите периоды или недельные окна")
        return self


class RestrictionZone(Model):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="", max_length=200)
    category: Literal["prohibited", "restricted", "temporary", "danger", "other"] = "restricted"
    geometry: Polygon
    lower: VerticalLimit = Field(default_factory=lambda: VerticalLimit(reference="GND"))
    upper: VerticalLimit = Field(default_factory=lambda: VerticalLimit(reference="UNL"))
    validity: Validity = Field(default_factory=Validity)
    notes: str = Field(default="", max_length=4000)
    source_text: str = Field(default="", max_length=4000)
    parsed: bool = True


class Obstacle(Model):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="", max_length=200)
    kind: str = Field(default="", max_length=100)
    geometry: Annotated[Union[PointGeometry, LineGeometry, Polygon], Field(discriminator="type")]
    # Footprint radius of a point obstacle; lines and polygons carry their own extent.
    radius_m: float = Field(default=0, ge=0, le=5000)
    # Top of the obstacle: above its ground (AGL) or above sea level (AMSL), as the source states.
    height_m: float = Field(ge=0, le=9000)
    height_reference: Literal["AGL", "AMSL"] = "AGL"


SurveyType = Literal["rgb", "multispectral", "thermal", "lidar", "geophysical"]
ID_PATTERN = r'^[a-zA-Z0-9_.:-]+$'


class ParameterSource(Model):
    """Where one parameter of a sensor profile comes from."""
    status: Literal['confirmed', 'computed', 'assumed', 'unknown']
    source: str = Field(default='', max_length=500)
    formula: str = Field(default='', max_length=300)


class SensorProfileInput(Model):
    """A camera or other survey sensor.

    `geometry` says how the swath follows from the parameters: `frame` — a nadir frame camera
    (swath = H · sensor width / focal length, GSD from the pixel size); `scanner` — a line scanner
    (swath = 2 H tan(FOV/2)); `line` — a point sensor flown on lines (magnetometer): the line spacing
    is set by the survey requirement; `unknown` — geometry not confirmed: a requirement must give
    the swath width by hand. Compatibility lists aircraft profiles the sensor is confirmed for.
    """
    name: str = Field(min_length=1, max_length=120)
    sensor_type: SurveyType
    geometry: Literal['frame', 'scanner', 'line', 'unknown']
    sensor_width_mm: float | None = Field(default=None, gt=0, le=100)
    sensor_height_mm: float | None = Field(default=None, gt=0, le=100)
    focal_length_mm: float | None = Field(default=None, gt=0, le=1000)
    image_width_px: int | None = Field(default=None, gt=0, le=100000)
    image_height_px: int | None = Field(default=None, gt=0, le=100000)
    field_of_view_deg: float | None = Field(default=None, gt=0, lt=180)
    compatible_profiles: list[str] = Field(default_factory=list, max_length=50)
    # Demonstration profiles stand in for equipment that is not confirmed for these aircraft.
    demonstration: bool = False
    parameters: dict[str, ParameterSource] = Field(default_factory=dict)
    source: str = Field(min_length=1, max_length=1000)
    source_url: str = Field(default='', max_length=500)
    checked_at: date | None = None

    @model_validator(mode='after')
    def geometry_complete(self):
        if self.geometry == 'frame':
            missing = [name for name in ('sensor_width_mm', 'sensor_height_mm', 'focal_length_mm', 'image_width_px', 'image_height_px')
                       if getattr(self, name) is None]
            if missing:
                raise ValueError('Для кадровой камеры нужны размеры матрицы (мм), фокусное расстояние (мм) и разрешение (пикс): не заданы ' + ', '.join(missing))
        if self.geometry == 'scanner' and self.field_of_view_deg is None:
            raise ValueError('Для сканера нужен угол обзора field_of_view_deg (градусы)')
        return self


class SensorProfile(SensorProfileInput):
    id: str = Field(min_length=1, max_length=100, pattern=ID_PATTERN)
    # A changed profile is a new version; a saved scenario keeps the version it was planned with.
    version: int = Field(default=1, ge=1, le=100000)
    origin: Literal['builtin', 'user'] = 'user'


class SurveyRequirement(Model):
    """One obligation of the operation: zones + survey type + quality parameters.

    The swath comes from the sensor geometry, or from `swath_width_m` when it is given (for
    sensors whose geometry is not confirmed). `altitude_m` fixes the survey height for this
    requirement; without it the aircraft flies at its own level (survey altitude, echelon or
    its own height). `line_spacing_m` is the line spacing of `line` sensors (magnetometry).
    """
    id: str = Field(min_length=1, max_length=100, pattern=ID_PATTERN)
    name: str = Field(default='', max_length=120)
    area_ids: list[str] = Field(min_length=1, max_length=200)
    survey_type: SurveyType
    side_overlap_percent: float = Field(default=30, ge=0, le=80)
    front_overlap_percent: float = Field(default=70, ge=0, le=90)
    max_gsd_cm: float | None = Field(default=None, ge=0.1, le=100)
    altitude_m: float | None = Field(default=None, ge=20, le=300)
    swath_width_m: float | None = Field(default=None, ge=10, le=500)
    line_spacing_m: float | None = Field(default=None, ge=5, le=1000)
    max_speed_mps: float | None = Field(default=None, ge=2, le=100)


class ProfileCost(Model):
    """Coefficients for every aircraft of one aircraft type; an aircraft's own entry wins over them."""
    airtime: float | None = Field(default=None, ge=0, le=1e7)
    diagonal: float | None = Field(default=None, ge=0, le=1e6)


class CostPair(Model):
    """Off-diagonal entry q_ij = q_ji of the symmetric matrix Q, у.е./ч². The pair adds 2·q_ij·t_i·t_j."""
    a: str = Field(min_length=1, max_length=100)
    b: str = Field(min_length=1, max_length=100)
    q: float = Field(ge=-1e6, le=1e6)


class CostModel(Model):
    """J = α·T + cᵀt + tᵀQt in conventional cost units (у.е.); T and t_i in hours.

    T — completion time of the operation, t_i — flight time of aircraft i over all its sorties.
    α in у.е./ч; c_i — cost of an hour of flight of aircraft i, у.е./ч; Q in у.е./ч². c and the
    diagonal of Q are keyed by aircraft id; aircraft without their own entry take the defaults.
    Q must be symmetric positive semidefinite. A model without c (saved before 17.10.2026 evening)
    has c = 0. A scenario without a model uses the open-data preset (catalog/costs.py).
    """
    alpha_per_h: float = Field(default=60, ge=0, le=1e6)
    airtime_default: float = Field(default=0, ge=0, le=1e7)
    airtime: dict[str, float] = Field(default_factory=dict)
    default_diagonal: float = Field(default=30, ge=0, le=1e6)
    # By aircraft type (profile id): c and q for every aircraft of the type without its own entry.
    by_profile: dict[str, ProfileCost] = Field(default_factory=dict)
    diagonal: dict[str, float] = Field(default_factory=dict)
    pairs: list[CostPair] = Field(default_factory=list, max_length=400)

    @field_validator('diagonal')
    @classmethod
    def nonnegative_diagonal(cls, value):
        for key, q in value.items():
            if not math.isfinite(q) or q < 0 or q > 1e6:
                raise ValueError(f'Индивидуальный коэффициент {key}: нужно число от 0 до 10⁶ у.е./ч²')
        return value

    @field_validator('airtime')
    @classmethod
    def nonnegative_airtime(cls, value):
        for key, c in value.items():
            if not math.isfinite(c) or c < 0 or c > 1e7:
                raise ValueError(f'Стоимость часа налета {key}: нужно число от 0 до 10⁷ у.е./ч')
        return value


class AircraftProfileInput(Model):
    name: str = Field(min_length=1, max_length=100)
    airframe: Literal['multirotor', 'fixed_wing'] = 'multirotor'
    max_flight_time_min: float = Field(gt=0, le=600)
    max_wind_mps: float = Field(ge=0, le=40)
    max_horizontal_speed_mps: float = Field(gt=0, le=100)
    source: str = Field(min_length=1, max_length=300)
    # Scheduled maintenance: every N flights, flight hours or motor hours; None when unknown.
    maintenance_interval: float | None = Field(default=None, gt=0, le=100000)
    maintenance_unit: Literal['flights', 'flight_hours', 'motor_hours'] | None = None
    maintenance_source: str = Field(default='', max_length=300)
    # Full battery charge on the standard charger; None falls back to the scenario setting.
    charge_time_min: float | None = Field(default=None, gt=0, le=1440)
    charge_source: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def maintenance_pair(self):
        if (self.maintenance_interval is None) != (self.maintenance_unit is None):
            raise ValueError('Для ТО укажите и интервал, и единицу измерения')
        return self


class AircraftProfile(AircraftProfileInput):
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    origin: Literal['builtin', 'user'] = 'user'


class AircraftInstance(Model):
    id: str = Field(min_length=1, max_length=100)
    profile_id: str = Field(min_length=1, max_length=100)
    battery_percent: float = Field(gt=0, le=100)
    payload: SurveyType
    start_site_id: str | None = None
    airspeed_mps: float | None = Field(default=None, ge=2, le=100)
    camera_id: Literal['sony_rx1rm2_v1', 'geoscan_pf1b_v1', 'geoscan_pollux_v1', 'sony_umc_r10c_v1'] | None = None
    # Optional operator-declared sustained trigger interval, not a factory rating.
    trigger_interval_s: float | None = Field(default=None, ge=0.1, le=60)
    # Flight level of this aircraft (AGL): survey and transit. None: the survey altitude, or an
    # automatically assigned echelon when the scenario asks for echelons.
    altitude_m: float | None = Field(default=None, ge=20, le=300)
    # Survey equipment available to this aircraft (sensor profile ids). Used with survey
    # requirements; a sortie carries one of them. None: the single payload/camera of version 1.
    sensors: list[str] | None = Field(default=None, max_length=20)


class FixedWingSettings(Model):
    airspeed_mps: float = Field(default=22, ge=18, le=36)
    turn_radius_m: float = Field(default=150, ge=50, le=1000)
    climb_rate_mps: float = Field(default=2.5, ge=0.5, le=8)
    parachute_sink_mps: float = Field(default=5, ge=2, le=10)
    launch_heading_deg: float | None = Field(default=None, ge=0, lt=360)
    # Straight flight before each strip so the aircraft is level when the camera starts.
    lead_in_m: float = Field(default=50, ge=0, le=500)
    # Parachute release height; None releases at the flight level. Lower: circle down first.
    parachute_release_m: float | None = Field(default=None, ge=20, le=300)


class SurveySettings(Model):
    altitude_m: float = Field(default=100, ge=20, le=300)
    airspeed_mps: float = Field(default=10, ge=2, le=15)
    swath_width_m: float = Field(default=100, ge=10, le=500)
    side_overlap_percent: float = Field(default=30, ge=0, le=80)
    reserve_percent: float = Field(default=25, ge=15, le=60)
    endurance_factor: float = Field(default=0.75, ge=0.3, le=0.9)
    boundary_clearance_m: float = Field(default=20, ge=5, le=100)
    coverage_mode: Literal['manual', 'camera'] = 'manual'
    front_overlap_percent: float = Field(default=70, ge=0, le=90)
    max_gsd_cm: float | None = Field(default=None, ge=0.1, le=100)
    # Default keeps saved scenarios reproducible: every aircraft lands where it launched.
    landing_policy: Literal['return_to_start', 'best_main_site'] = 'return_to_start'
    fixed_wing: FixedWingSettings = Field(default_factory=FixedWingSettings)
    # Terrain range of the area, AMSL (no DEM). AMSL/FL limits and AMSL obstacle tops are compared
    # on the unsafe side: a zone floor over the highest ground, obstacles over the lowest.
    terrain_min_m: float = Field(default=150, ge=-450, le=6000)
    terrain_max_m: float = Field(default=150, ge=-450, le=6000)
    vertical_buffer_m: float = Field(default=50, ge=0, le=500)
    obstacle_clearance_m: float = Field(default=50, ge=5, le=1000)
    # 'reject' keeps the historical rule; 'exclude' cuts blocked parts out and reports them.
    blocked_area_policy: Literal['reject', 'exclude'] = 'reject'
    operation_start: AwareDatetime | None = None
    time_limit_min: float | None = Field(default=None, gt=0, le=10080)
    # The work must also end by sunset at the survey area (needs operation_start).
    until_sunset: bool = False
    max_aircraft: int | None = Field(default=None, ge=1, le=20)
    # Sorties per aircraft. Between sorties the battery is either swapped for a charged spare
    # ('swap': the turnaround, same site) or charged on a site charger ('charge': the aircraft
    # waits for its own battery; it may land on any site with chargers, reserve ones included).
    sorties_per_aircraft: int = Field(default=1, ge=1, le=20)
    turnaround_min: float = Field(default=15, ge=0, le=240)
    recharge_mode: Literal['swap', 'charge'] = 'swap'
    charge_time_min: float = Field(default=105, gt=0, le=1440)
    ground_handling_min: float = Field(default=5, ge=0, le=120)
    # Echelons: aircraft whose routes come close fly at different levels from the survey altitude up,
    # in steps of the vertical separation, below the ceiling. Aircraft closer than the vertical
    # separation must keep the horizontal one. Russian rules (FP IVP) allow UAV flights without an
    # airspace permit below 150 m above the surface, among other conditions: the default ceiling.
    echelons: bool = False
    vertical_separation_m: float = Field(default=20, ge=5, le=100)
    altitude_ceiling_m: float = Field(default=150, ge=20, le=1000)
    # Ground time to change the payload when the next sortie needs another sensor. No customer data:
    # a conservative assumption. One sortie carries one sensor; no simultaneous survey types.
    payload_change_min: float = Field(default=15, ge=0, le=240)

    @model_validator(mode="after")
    def terrain_range(self):
        if self.terrain_max_m < self.terrain_min_m:
            raise ValueError("Максимальная высота рельефа меньше минимальной")
        if self.until_sunset and self.operation_start is None:
            raise ValueError("Для срока до заката задайте время начала работ")
        return self


class WeatherObservation(Model):
    provider: Literal['open-meteo'] = 'open-meteo'
    coordinates: Position
    fetched_at: AwareDatetime
    valid_at: AwareDatetime
    wind_speed_mps: float = Field(ge=0, le=100)
    wind_from_deg: float = Field(ge=0, lt=360)
    wind_gusts_mps: float = Field(ge=0, le=150)
    temperature_c: float = Field(ge=-100, le=70)
    precipitation_mm: float = Field(ge=0, le=500)
    wind_height_m: Literal[10] = 10


class Weather(Model):
    wind_speed_mps: float = Field(ge=0, le=100)
    wind_from_deg: float = Field(ge=0, lt=360)
    observation: WeatherObservation | None = None

    @model_validator(mode='after')
    def matching_observation(self):
        if self.observation and (self.wind_speed_mps != self.observation.wind_speed_mps or self.wind_from_deg != self.observation.wind_from_deg):
            raise ValueError('При ручном изменении ветра удалите погодный снимок источника.')
        return self


class Scenario(Model):
    schema_version: Literal["0.1"] = "0.1"
    name: str = Field(min_length=1, max_length=120)
    survey_area: Polygon
    # Up to 200 areas in total: an imported work package often consists of many small fields.
    additional_survey_areas: list[Polygon] = Field(default_factory=list, max_length=199)
    allowed_airspace: Polygon | None = None
    no_fly_zones: list[Polygon] = Field(default_factory=list, max_length=100)
    restriction_zones: list[RestrictionZone] = Field(default_factory=list, max_length=2000)
    obstacles: list[Obstacle] = Field(default_factory=list, max_length=20000)
    sites: list[Site] = Field(default_factory=list, max_length=100)
    fleet: list[AircraftInstance] = Field(default_factory=list, max_length=50)
    weather: Weather
    survey_type: SurveyType = "rgb"
    objective: Literal["min_completion_time", "min_total_airtime", "min_cost"]
    settings: SurveySettings = Field(default_factory=SurveySettings)
    aircraft_profiles: list[AircraftProfile] = Field(default_factory=list, max_length=50)
    # Version 1.2. Stable ids of the survey areas, in the order survey_area, additional_survey_areas.
    area_ids: list[str] | None = Field(default=None, max_length=200)
    # Survey requirements; None — the single survey_type over all areas (scenarios before 1.2).
    requirements: list[SurveyRequirement] | None = Field(default=None, max_length=100)
    # Snapshot of the sensor profiles the fleet refers to, as the aircraft profiles above.
    sensor_profiles: list[SensorProfile] = Field(default_factory=list, max_length=200)
    cost_model: CostModel | None = None

    def area_id_list(self) -> list[str]:
        count = 1 + len(self.additional_survey_areas)
        return list(self.area_ids) if self.area_ids else [f'zone-{n}' for n in range(1, count + 1)]

    @model_validator(mode="after")
    def survey_model(self):
        count = 1 + len(self.additional_survey_areas)
        if self.area_ids is not None:
            if len(self.area_ids) != count:
                raise ValueError(f'area_ids: {len(self.area_ids)} идентификаторов на {count} зон съемки')
            if len(set(self.area_ids)) != count:
                raise ValueError('area_ids: идентификаторы зон повторяются')
        if self.requirements is not None:
            known = set(self.area_id_list())
            ids = [r.id for r in self.requirements]
            if len(ids) != len(set(ids)):
                raise ValueError('Повторяющиеся идентификаторы требований съемки')
            for r in self.requirements:
                unknown = [a for a in r.area_ids if a not in known]
                if unknown:
                    raise ValueError(f'Требование {r.id}: нет зон {", ".join(unknown)}')
        sensors = [(s.id, s.version) for s in self.sensor_profiles]
        if len({s for s, _ in sensors}) != len(sensors):
            raise ValueError('В снимке профилей сенсоров идентификатор повторяется')
        return self

    @field_validator('cost_model')
    @classmethod
    def cost_model_fits_fleet(cls, value, info):
        # A field validator, so an error points at cost_model; the fleet is validated before it.
        if value is not None and 'fleet' in info.data:
            from bas.planning.cost import resolve, validate_cost_model
            validate_cost_model(resolve(value, info.data['fleet']), [a.id for a in info.data['fleet']])
        return value

    @model_validator(mode="after")
    def unique_ids(self):
        for name, entries in (("sites", self.sites), ("fleet", self.fleet), ('aircraft_profiles', self.aircraft_profiles),
                              ('restriction_zones', self.restriction_zones), ('obstacles', self.obstacles)):
            ids = [entry.id for entry in entries]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Повторяющиеся идентификаторы: {name}")
        return self


class TaskEstimateRequest(Model):
    """Whole-task estimate for the imported task area: packages, sorties and days for one aircraft type."""
    areas: list[Polygon] = Field(min_length=1, max_length=5000)
    profile_id: str = Field(min_length=1, max_length=100)
    fleet_size: int = Field(default=10, ge=1, le=200)
    workday_h: float = Field(default=10, gt=0, le=24)
    cell_km: float | None = Field(default=None, ge=0.5, le=30)
    settings: SurveySettings = Field(default_factory=SurveySettings)
    aircraft_profiles: list[AircraftProfile] = Field(default_factory=list, max_length=50)


class StoredScenario(Model):
    id: UUID
    created_at: str
    scenario: Scenario


class PlanningRequest(Model):
    scenario_id: UUID


class FlightLeg(Model):
    """Future output contract. AGL stays in properties, never implicit GeoJSON Z."""
    sequence: int = Field(ge=0)
    phase: Literal["takeoff", "climb", "transit", "survey", "turn", "hold", "descent", "parachute", "landing"]
    coordinates: list[Position] = Field(min_length=2)
    altitude_m: float
    altitude_reference: Literal["AGL", "AMSL", "ELLIPSOID", "RELATIVE_START"]
    speed_mps: float = Field(gt=0)
    speed_reference: Literal["air", "ground"]
    duration_s: float = Field(gt=0)
