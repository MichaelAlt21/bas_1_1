import type { LineString, Point, Polygon } from 'geojson';
export type ProfileId = string;
export type Payload = 'rgb' | 'multispectral' | 'thermal' | 'lidar' | 'geophysical';
export type Position = [number, number];
export interface Site { id:string; name:string; kind:'launch_landing'|'alternate'; coordinates:Position; chargers?:number|null }
export interface Aircraft { id:string; profile_id:ProfileId; battery_percent:number; payload:Payload; start_site_id:string|null; airspeed_mps?:number|null; camera_id?:string|null; trigger_interval_s?:number|null; altitude_m?:number|null }
export interface FixedWingSettings {airspeed_mps:number;turn_radius_m:number;climb_rate_mps:number;parachute_sink_mps:number;launch_heading_deg?:number|null;lead_in_m?:number;parachute_release_m?:number|null}
export interface SurveySettings { altitude_m:number; airspeed_mps:number; swath_width_m:number; side_overlap_percent:number; reserve_percent:number; endurance_factor:number; boundary_clearance_m:number;coverage_mode?:'manual'|'camera';front_overlap_percent?:number;max_gsd_cm?:number|null;landing_policy?:'return_to_start'|'best_main_site';fixed_wing?:FixedWingSettings;
  terrain_min_m?:number;terrain_max_m?:number;vertical_buffer_m?:number;obstacle_clearance_m?:number;blocked_area_policy?:'reject'|'exclude';
  operation_start?:string|null;time_limit_min?:number|null;until_sunset?:boolean;max_aircraft?:number|null;sorties_per_aircraft?:number;turnaround_min?:number;
  recharge_mode?:'swap'|'charge';charge_time_min?:number;ground_handling_min?:number;
  echelons?:boolean;vertical_separation_m?:number;altitude_ceiling_m?:number }
export type LimitReference='GND'|'AGL'|'AMSL'|'FL'|'UNL';
export interface VerticalLimit {reference:LimitReference;value:number}
export interface Validity {kind:'permanent'|'scheduled'|'unknown';periods:{start:string;end:string}[];weekly:{days:string[];start:string;end:string}[];timezone:string}
export type ZoneCategory='prohibited'|'restricted'|'temporary'|'danger'|'other';
export interface RestrictionZone {id:string;name:string;category:ZoneCategory;geometry:Polygon;lower:VerticalLimit;upper:VerticalLimit;validity:Validity;notes:string;source_text:string;parsed:boolean}
export interface Obstacle {id:string;name:string;kind:string;geometry:Point|LineString|Polygon;radius_m:number;height_m:number;height_reference:'AGL'|'AMSL'}
export interface CameraProfile {id:string;name:string;compatible_profiles:string[];sensor_type:Payload;sensor_width_mm:number;sensor_height_mm:number;focal_length_mm:number;image_width_px:number;image_height_px:number;source:string;source_url:string}
export interface CameraResult {mode:'manual'|'camera';swath_width_m:number;camera?:CameraProfile;gsd_cm?:number;photo_count?:number;min_photo_interval_s?:number;crab_deg?:number;nominal_swath_width_m?:number;altitude_m?:number}
export interface Scenario {
  schema_version:'0.1'; name:string; survey_area:Polygon; additional_survey_areas?:Polygon[];
  allowed_airspace:Polygon|null; no_fly_zones:Polygon[]; restriction_zones?:RestrictionZone[]; obstacles?:Obstacle[]; sites:Site[]; fleet:Aircraft[];
  weather:{wind_speed_mps:number;wind_from_deg:number;observation?:WeatherObservation|null}; survey_type:Payload;
  aircraft_profiles?:AircraftProfile[];
  objective:'min_completion_time'|'min_total_airtime'; settings:SurveySettings;
}
export type Draft = Omit<Scenario,'survey_area'> & {survey_area:Polygon|null};
export interface AircraftProfileInput { name:string; airframe:'multirotor'|'fixed_wing'; max_flight_time_min:number; max_wind_mps:number; max_horizontal_speed_mps:number; source:string; maintenance_interval?:number|null; maintenance_unit?:'flights'|'flight_hours'|'motor_hours'|null; maintenance_source?:string; charge_time_min?:number|null; charge_source?:string }
export interface AircraftProfile extends AircraftProfileInput { id:ProfileId; origin:'builtin'|'user' }
export interface WeatherObservation {provider:'open-meteo';coordinates:Position;fetched_at:string;valid_at:string;wind_speed_mps:number;wind_from_deg:number;wind_gusts_mps:number;temperature_c:number;precipitation_mm:number;wind_height_m:10}
export interface Leg {sequence:number;phase:string;coordinates:Position[];start:number;end:number;altitude_start_m:number;altitude_end_m:number;speed_mps:number}
export interface AlternateReachability {checked:boolean;reachable:boolean|null;site_id:string|null;margin_s:number|null}
export interface Mission {aircraft_id:string;sortie?:number;ground_s?:number;charge_site_id?:string;charge_s?:number;charge_start_s?:number;charge_end_s?:number;charge_wait_s?:number;battery_start_percent?:number;battery_end_percent?:number;profile_id:ProfileId;site_id:string;landing_site_id?:string;color:string;legs:Leg[];flight_time_s:number;budget_s:number;distance_m:number;start_s:number;finish_s:number;reserve_percent:number;airframe?:string;airspeed_mps?:number;altitude_m?:number;survey?:CameraResult;fixed_wing_assumptions?:(FixedWingSettings&{bank_deg?:number;spiral_climb?:boolean;release_altitude_m?:number;descent_turns?:number})|null;alternate:AlternateReachability}
export interface Maintenance {aircraft_id:string;profile_id:string;sorties:number;flight_time_s:number;interval:number|null;unit:'flights'|'flight_hours'|'motor_hours'|null;interval_share:number|null;source:string}
export interface ZoneDecision {id:string;name:string;category:ZoneCategory;lower_agl_m:number|null;upper_agl_m:number|null;reason?:string}
export interface AirspaceReport {terrain_min_m:number;terrain_max_m:number;vertical_buffer_m:number;flight_altitude_m:number;window_start:string|null;window_end:string|null;
  zones_applied:ZoneDecision[];zones_above:ZoneDecision[];zones_below_ground:ZoneDecision[];zones_inactive:ZoneDecision[];zones_time_assumed:ZoneDecision[];
  obstacles_applied:{id:string;kind:string;height_agl_m:number}[];obstacles_below:number;tallest_below_agl_m:number|null;
  excluded_area_m2:number;excluded_by:{kind:string;id:string;label:string}[];excluded_outside_allowed:boolean;blockers_considered:number;free_space_vertices:number}
export interface PlanConstraints {time_limit_s:number|null;time_limit_source:'manual'|'sunset'|null;sunset:string|null;completion_s:number;meets_time_limit:boolean|null;max_aircraft:number|null;aircraft_used:number;sorties:number;operation_start:string|null;operation_end:string|null}
export interface EchelonReport {levels:number[];vertical_separation_m:number;ceiling_m:number;applied:boolean;note?:string;shared_same_level_before_m:number;shared_same_level_after_m:number;single_level:{completion_s:number;total_airtime_s:number}}
export interface Plan {id:string;scenario_id:string;status:'demonstration';objective:Scenario['objective'];missions:Mission[];warnings:string[];airspace?:AirspaceReport;constraints?:PlanConstraints;
  echelons?:EchelonReport|null;separation?:{horizontal_m:number;vertical_m:number|null};
  metrics:{survey_area_count?:number;coverage_by_area?:{index:number;area_m2:number;coverage_percent:number;excluded_m2?:number}[];completion_time_s:number;total_airtime_s:number;total_distance_m:number;area_m2:number;excluded_area_m2?:number;coverage_percent:number;aircraft_used:number;sortie_count?:number;charging_s?:number;charger_wait_s?:number;maintenance?:Maintenance[];maintenance_share_total?:number;calculation_time_s:number;strip_angle_deg:number}}
export type ImportStats=Record<string,unknown>;
export interface SurveyImport {kind:'survey';areas:{geometry:Polygon;area_m2:number}[];stats:ImportStats}
export interface ZonesImport {kind:'zones';zones:RestrictionZone[];warnings:string[];stats:ImportStats}
export interface ObstaclesImport {kind:'obstacles';obstacles:Obstacle[];warnings:string[];stats:ImportStats}
export interface AnalysisRow {aircraft:number;feasible:boolean;code?:string;message?:string;aircraft_used?:number;sorties?:number;completion_s?:number;total_airtime_s?:number;maintenance_share?:number;coverage_percent?:number;meets_deadline?:boolean|null;best_completion_s:number|null;best_aircraft:number|null}
export interface FleetAnalysis {fleet_size:number;deadline_s:number|null;deadline_source:'manual'|'sunset'|null;sunset:string|null;rows:AnalysisRow[];aircraft_needed:number|null;balanced:AnalysisRow|null;with_requested:AnalysisRow|null;saturated_at:number|null;truncated:boolean;verdict:string;calculation_time_s:number}
export interface WorkPackage {id:string;cell:[number,number];area_ha:number;parts:number;lines_km:number;survey_h:number;sorties:number|null;airtime_h:number|null;reachable:boolean;farthest_km:number;site:Position;areas:Polygon[]}
export interface TaskEstimate {profile_id:string;cell_km:number;line_spacing_m:number;sortie_budget_min:number;packages:WorkPackage[];unreachable_packages:string[];
  totals:{area_ha:number;lines_km:number;sorties:number;airtime_h:number;mean_sortie_min:number;sorties_per_aircraft_day:number;maintenance_intervals:number|null};days_by_fleet:{aircraft:number;days:number}[];assumptions:string[];cycle_min?:number}
export interface SunTimes {date:string;sunrise:string|null;sunset:string|null;solar_noon:string|null;civil_dawn:string|null;civil_dusk:string|null;polar:'day'|'night'|null}
export interface ScenarioSummary { id:string; name:string; created_at:string }
export interface StoredScenario { id:string; scenario:Scenario; created_at:string }
export interface PlanSummary { id:string; scenario_id:string; name:string; objective:Scenario['objective']; created_at:string; calculated_at:string|null; metrics:Plan['metrics'] }
export interface PlanPage { items:PlanSummary[]; total:number; limit:number; offset:number }
