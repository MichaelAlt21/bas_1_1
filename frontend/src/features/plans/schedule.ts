import type { Mission } from '../../shared/types';

export type Segment={kind:'flight'|'handling'|'waiting'|'charging'|'swap';start:number;end:number;mission:Mission};
export interface ScheduleRow {aircraftId:string;profileId:string;color:string;segments:Segment[]}

/** Per aircraft, in time order: sorties and the ground time between them (handling, charger queue, charging or battery swap). */
export function scheduleRows(missions:Mission[]):ScheduleRow[]{
 const rows=new Map<string,Mission[]>();
 missions.forEach(m=>rows.set(m.aircraft_id,[...(rows.get(m.aircraft_id)||[]),m]));
 return [...rows.entries()].sort(([a],[b])=>a.localeCompare(b,'ru',{numeric:true})).map(([aircraftId,list])=>{
  const ordered=[...list].sort((a,b)=>a.start_s-b.start_s);
  const segments:Segment[]=[];
  ordered.forEach((m,index)=>{
   const before=ordered[index-1];
   if(before){
    if(m.charge_start_s!==undefined&&m.charge_end_s!==undefined){
     const queued=m.charge_start_s-(m.charge_wait_s||0);
     if(queued>before.finish_s)segments.push({kind:'handling',start:before.finish_s,end:queued,mission:m});
     if(m.charge_start_s>queued+1)segments.push({kind:'waiting',start:queued,end:m.charge_start_s,mission:m});
     segments.push({kind:'charging',start:m.charge_start_s,end:m.charge_end_s,mission:m});
    }else if(m.ground_s){
     segments.push({kind:'swap',start:before.finish_s,end:before.finish_s+m.ground_s,mission:m});
    }
   }
   segments.push({kind:'flight',start:m.start_s,end:m.finish_s,mission:m});
  });
  return {aircraftId,profileId:ordered[0].profile_id,color:ordered[0].color,segments};
 });
}

const STEPS=[60,120,300,600,900,1800,3600,7200,10800,14400,21600,43200];

/** Round tick spacing giving at most `count` intervals over `span` seconds. */
export function tickStep(span:number,count=8){
 return STEPS.find(step=>span/step<=count)||Math.ceil(span/count/3600)*3600;
}

export function clock(seconds:number){
 const minutes=Math.round(seconds/60);
 return minutes<60?`${minutes} мин`:`${Math.floor(minutes/60)} ч${minutes%60?` ${String(minutes%60).padStart(2,'0')}`:''}`;
}

/** Clock time of an offset from the operation start: local HH:MM when the start is known, else +h:mm. */
export function timeOfDay(seconds:number,start?:string|null){
 if(!start){const minutes=Math.round(seconds/60);return `+${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,'0')}`;}
 const origin=new Date(start),moment=new Date(origin.getTime()+seconds*1000);
 const days=Math.round((new Date(moment).setHours(0,0,0,0)-new Date(origin).setHours(0,0,0,0))/86400000);
 return moment.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})+(days?` +${days} д`:'');
}
