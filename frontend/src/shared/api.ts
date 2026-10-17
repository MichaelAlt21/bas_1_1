import type { Scenario, AircraftProfile, AircraftProfileInput, Diagnostic, Plan, SensorProfile, SensorProfileInput, StoredScenario, ScenarioSummary, WeatherObservation } from './types';
/** A refused request: the message for the operator plus, when the server gives them, diagnostics and conflicts. */
export class ApiError extends Error {
  constructor(message:string,public status:number,public code?:string,public diagnostics:Diagnostic[]=[],public conflicts:{id:string;existing_version:number;builtin:boolean;name:string}[]=[]){super(message);}
}
async function request<T>(path:string, options?:RequestInit):Promise<T> {
  const response=await fetch(`/api${path}`,options);
  if(!response.ok){
    const body=await response.json().catch(()=>null);
    const detail=body?.detail;
    const message=typeof detail==='string'?detail:detail?.message ?? (Array.isArray(detail)?detail.map((d:{loc:string[];msg:string})=>`${d.loc.slice(1).join('.')}: ${d.msg.replace(/^Value error, /,'')}`).join('; '):`HTTP ${response.status}`);
    throw new ApiError(message,response.status,detail?.code,detail?.diagnostics??body?.diagnostics??[],detail?.conflicts??[]);
  }
  return response.json() as Promise<T>;
}
const json=(body:unknown)=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const api={
  databaseStatus:(token:string)=>request<import('./databaseTypes').DatabaseStatus>('/settings/database',{headers:{'X-Kumo-Admin-Token':token}}),
  databaseAction:(action:'check'|'prepare'|'save'|'local',token:string,body?:unknown)=>request<import('./databaseTypes').DatabaseReport>('/settings/database/'+action,{...json(body||{}),headers:{'Content-Type':'application/json','X-Kumo-Admin-Token':token}}),
  health:()=>request<{version:string;planner_ready:boolean}>('/health'),
  aircraft:()=>request<AircraftProfile[]>('/catalog/aircraft'),
  cameras:()=>request<import('./types').CameraProfile[]>('/catalog/cameras'),
  createProfile:(profile:AircraftProfileInput)=>request<AircraftProfile>('/catalog/aircraft',json(profile)),
  sensors:()=>request<SensorProfile[]>('/catalog/sensors'),
  costPreset:()=>request<import('./types').CostPreset>('/catalog/cost-preset'),
  sensorVersions:(id:string)=>request<SensorProfile[]>(`/catalog/sensors/${encodeURIComponent(id)}/versions`),
  createSensor:(profile:SensorProfileInput)=>request<SensorProfile>('/catalog/sensors',json(profile)),
  updateSensor:(id:string,profile:SensorProfileInput)=>request<SensorProfile>(`/catalog/sensors/${encodeURIComponent(id)}`,{...json(profile),method:'PUT'}),
  importSensors:(document:unknown,onConflict:'reject'|'new_version'|'rename')=>request<{added:string[];new_versions:{id:string;version:number}[];renamed:{id:string;new_id:string}[];unchanged:string[]}>(`/catalog/sensors/import?on_conflict=${onConflict}`,json(document)),
  sensorExportUrl:(ids?:string[])=>`/api/catalog/sensors/export${ids?.length?`?ids=${encodeURIComponent(ids.join(','))}`:''}`,
  weather:(latitude:number,longitude:number,signal?:AbortSignal)=>request<WeatherObservation>(`/weather/current?latitude=${latitude}&longitude=${longitude}`,{signal}),
  save:(scenario:Scenario)=>request<StoredScenario>('/scenarios',json(scenario)),
  list:()=>request<ScenarioSummary[]>('/scenarios'),
  load:(id:string)=>request<StoredScenario>(`/scenarios/${encodeURIComponent(id)}`),
  plan:(scenarioId:string)=>request<Plan>('/planning-runs',json({scenario_id:scenarioId})),
  plans:(offset=0,signal?:AbortSignal)=>request<import('./types').PlanPage>(`/plans?limit=12&offset=${offset}`,{signal}),
  loadPlan:(id:string)=>request<Plan>(`/plans/${encodeURIComponent(id)}`),
  fleetAnalysis:(scenarioId:string)=>request<import('./types').FleetAnalysis>(`/scenarios/${encodeURIComponent(scenarioId)}/fleet-analysis`,{method:'POST'}),
  importKml:<T,>(kind:'survey'|'zones'|'obstacles',file:File,bbox?:[number,number,number,number]|null)=>request<T>(`/imports/kml?kind=${kind}${bbox?`&west=${bbox[0]}&south=${bbox[1]}&east=${bbox[2]}&north=${bbox[3]}`:''}`,{method:'POST',headers:{'Content-Type':'application/vnd.google-earth.kml+xml'},body:file}),
  exportZones:async(zones:import('./types').RestrictionZone[])=>{const response=await fetch('/api/exports/zones-kml',json(zones));if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.blob();},
  taskEstimate:(body:unknown)=>request<import('./types').TaskEstimate>('/task-estimate',json(body)),
  sun:(latitude:number,longitude:number,date:string)=>request<import('./types').SunTimes>(`/sun?latitude=${latitude}&longitude=${longitude}&date=${date}`),
  reportUrl:(id:string)=>`/api/plans/${encodeURIComponent(id)}/report`,
  exportUrl:(id:string,format:'geojson'|'kml',aircraft?:string)=>`/api/plans/${encodeURIComponent(id)}/export?format=${format}${aircraft?`&aircraft_id=${encodeURIComponent(aircraft)}`:''}`,
};
