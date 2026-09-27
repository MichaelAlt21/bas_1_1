import type { Scenario, AircraftProfile, AircraftProfileInput, Plan, StoredScenario, ScenarioSummary, WeatherObservation } from './types';
async function request<T>(path:string, options?:RequestInit):Promise<T> {
  const response=await fetch(`/api${path}`,options);
  if(!response.ok){
    const body=await response.json().catch(()=>null);
    const detail=body?.detail;
    const message=typeof detail==='string'?detail:detail?.message ?? (Array.isArray(detail)?detail.map((d:{loc:string[];msg:string})=>`${d.loc.slice(1).join('.')}: ${d.msg}`).join('; '):`HTTP ${response.status}`);
    throw new Error(message);
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
