import math
from datetime import time
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
    payload: Literal["rgb", "multispectral", "thermal", "lidar", "geophysical"]
    start_site_id: str | None = None
    airspeed_mps: float | None = Field(default=None, ge=2, le=100)
    camera_id: Literal['sony_rx1rm2_v1', 'geoscan_pf1b_v1', 'geoscan_pollux_v1', 'sony_umc_r10c_v1'] | None = None
    # Optional operator-declared sustained trigger interval, not a factory rating.
    trigger_interval_s: float | None = Field(default=None, ge=0.1, le=60)
    # Flight level of this aircraft (AGL): survey and transit. None: the survey altitude, or an
    # automatically assigned echelon when the scenario asks for echelons.
    altitude_m: float | None = Field(default=None, ge=20, le=300)


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
    survey_type: Literal["rgb", "multispectral", "thermal", "lidar", "geophysical"] = "rgb"
    objective: Literal["min_completion_time", "min_total_airtime"]
    settings: SurveySettings = Field(default_factory=SurveySettings)
    aircraft_profiles: list[AircraftProfile] = Field(default_factory=list, max_length=50)

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
