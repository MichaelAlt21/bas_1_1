import type { Aircraft, AircraftProfile, Payload, Site } from '../../shared/types';

export const MAX_FLEET = 20;

export function groupFleet(fleet:Aircraft[],sites:Site[]){
 const launchSites=sites.filter(s=>s.kind==='launch_landing');
 const validIds=new Set(launchSites.map(s=>s.id));
 return {
  assigned:launchSites.map(site=>({site,aircraft:fleet.filter(a=>a.start_site_id===site.id)})),
  unassigned:fleet.filter(a=>!a.start_site_id||!validIds.has(a.start_site_id)),
 };
}

const defaultCameraId=(profileId:string,payload:Payload)=>{
 if(payload==='rgb') return profileId==='geoscan_201'?'sony_rx1rm2_v1':profileId==='gemini'?'geoscan_pf1b_v1':null;
 if(payload==='multispectral') return profileId==='gemini'||profileId==='geoscan_201'?'geoscan_pollux_v1':null;
 return null;
};

export function addAircraft(fleet:Aircraft[],profile:AircraftProfile,sites:Site[],siteId:string|null,count:number,payload:Payload='rgb'):Aircraft[]{
 if(!Number.isInteger(count)||count<1||fleet.length+count>MAX_FLEET)throw new Error(`В демонстрационном расчете доступно до ${MAX_FLEET} БПЛА.`);
 if(siteId&&!sites.some(s=>s.id===siteId&&s.kind==='launch_landing'))throw new Error('Выберите существующую площадку старта и посадки.');
 const next=[...fleet];
 for(let i=0;i<count;i++){
  const prefix=profile.name.slice(0,70).trim()||'БПЛА';
  let serial=1;
  let id='';
  do{id=`${prefix}-${String(serial++).padStart(2,'0')}`;}while(next.some(a=>a.id===id));
  next.push({id,profile_id:profile.id,battery_percent:100,payload,start_site_id:siteId,
   camera_id:defaultCameraId(profile.id,payload)});
 }
 return next;
}
