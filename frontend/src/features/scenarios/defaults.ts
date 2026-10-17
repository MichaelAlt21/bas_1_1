import type { Draft, SurveySettings } from '../../shared/types';
import type { Polygon } from 'geojson';
// New scenarios separate aircraft by height too; saved ones keep their own setting.
export const DEFAULT_SETTINGS:SurveySettings={altitude_m:100,airspeed_mps:10,swath_width_m:100,side_overlap_percent:30,reserve_percent:25,endurance_factor:.75,boundary_clearance_m:20,echelons:true};
export function emptyDraft():Draft {return {schema_version:'0.1',name:'Новый район съемки',survey_area:null,allowed_airspace:null,no_fly_zones:[],sites:[],fleet:[],weather:{wind_speed_mps:3,wind_from_deg:270},survey_type:'rgb',objective:'min_completion_time',settings:{...DEFAULT_SETTINGS}};}
export function fixedWingDemo():Draft {return {...emptyDraft(),name:'Демо · Геоскан 201 и Gemini с камерами',
 survey_area:rect(37.300,55.800,.012,.009),allowed_airspace:rect(37.260,55.775,.09,.06),
 sites:[{id:'plane-base',name:'Площадка 201',kind:'launch_landing',coordinates:[37.288,55.795]},{id:'copter-base',name:'Площадка Gemini',kind:'launch_landing',coordinates:[37.314,55.804]}],
 fleet:[{id:'201-01',profile_id:'geoscan_201',battery_percent:100,payload:'rgb',start_site_id:'plane-base',airspeed_mps:22,camera_id:'sony_rx1rm2_v1'},{id:'Gemini-01',profile_id:'gemini',battery_percent:100,payload:'rgb',start_site_id:'copter-base',airspeed_mps:10,camera_id:'geoscan_pf1b_v1'}],
 settings:{...DEFAULT_SETTINGS,coverage_mode:'camera',front_overlap_percent:70}};}
/** Pads on two corners, areas on the other two: four aircraft per pad share a long corridor; echelons split them. */
export function echelonDemo():Draft {return {...emptyDraft(),name:'Демо · Эшелоны: 8 Gemini, общие коридоры',
 survey_area:rect(37.300,55.830,.012,.006),additional_survey_areas:[rect(37.360,55.790,.012,.006)],allowed_airspace:rect(37.290,55.780,.095,.060),
 sites:[{id:'south-west',name:'Юго-западная площадка',kind:'launch_landing',coordinates:[37.302,55.792]},{id:'north-east',name:'Северо-восточная площадка',kind:'launch_landing',coordinates:[37.370,55.834]}],
 fleet:Array.from({length:8},(_,n)=>({id:`Gemini-${String(n+1).padStart(2,'0')}`,profile_id:'gemini',battery_percent:100,payload:'rgb' as const,start_site_id:n%2?'north-east':'south-west',camera_id:'geoscan_pf1b_v1'})),
 settings:{...DEFAULT_SETTINGS,coverage_mode:'camera',front_overlap_percent:70,echelons:true,vertical_separation_m:20,altitude_ceiling_m:150}};}
const rect=(x:number,y:number,w:number,h:number):Polygon=>({type:'Polygon',coordinates:[[[x,y],[x+w,y],[x+w,y+h],[x,y+h],[x,y]]]});
export function demoDraft():Draft {return {...emptyDraft(),name:'Демо · RGB-съемка двумя коптерами',survey_area:rect(37.30,55.80,.008,.005),allowed_airspace:rect(37.294,55.796,.02,.014),
 no_fly_zones:[rect(37.300,55.797,.003,.001)],
 sites:[{id:'west',name:'Западная площадка',kind:'launch_landing',coordinates:[37.297,55.801]},{id:'east',name:'Восточная площадка',kind:'launch_landing',coordinates:[37.311,55.804]},{id:'alternate',name:'Запасная площадка',kind:'alternate',coordinates:[37.309,55.808]}],
 fleet:[{id:'Gemini-01',profile_id:'gemini',battery_percent:100,payload:'rgb',start_site_id:'west'},{id:'801-01',profile_id:'geoscan_801',battery_percent:90,payload:'rgb',start_site_id:'east'}]};}
/** Same as contracts/scenario.multisurvey.example.json: orthophoto and vegetation of a field, thermal survey of a plant. */
export function multiSurveyDemo():Draft {return {...emptyDraft(),name:'Демо · Несколько видов съемки: ортофото, вегетация, тепловизор',
 survey_area:rect(37.300,55.800,.014,.008),additional_survey_areas:[rect(37.322,55.806,.010,.006)],area_ids:['field','plant'],allowed_airspace:rect(37.285,55.790,.060,.035),
 sites:[{id:'base',name:'База',kind:'launch_landing',coordinates:[37.296,55.797]},{id:'east',name:'Восточная',kind:'launch_landing',coordinates:[37.336,55.804]}],
 fleet:[{id:'Gemini-01',profile_id:'gemini',battery_percent:100,payload:'rgb',start_site_id:'base',sensors:['geoscan_pf1b_v1','geoscan_pollux_v1']},
  {id:'Gemini-02',profile_id:'gemini',battery_percent:100,payload:'rgb',start_site_id:'base',sensors:['geoscan_pf1b_v1']},
  {id:'801-01',profile_id:'geoscan_801',battery_percent:100,payload:'thermal',start_site_id:'east',sensors:['geoscan_801_thermal_v1']},
  {id:'Gemini-03',profile_id:'gemini',battery_percent:100,payload:'multispectral',start_site_id:'east',sensors:['geoscan_pollux_v1']}],
 objective:'min_cost',
 requirements:[{id:'field-rgb',name:'Поле · ортофото',area_ids:['field'],survey_type:'rgb',max_gsd_cm:2.5,side_overlap_percent:30,front_overlap_percent:70},
  {id:'field-ms',name:'Поле · вегетация',area_ids:['field'],survey_type:'multispectral',side_overlap_percent:30,front_overlap_percent:70},
  {id:'plant-ir',name:'Завод · тепловизор',area_ids:['plant'],survey_type:'thermal',swath_width_m:60,side_overlap_percent:20,front_overlap_percent:70}],
 settings:{...DEFAULT_SETTINGS,sorties_per_aircraft:3,echelons:false}};}
