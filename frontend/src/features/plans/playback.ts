import type { Mission, Position } from '../../shared/types';

const names:Record<string,string>={takeoff:'Взлет',climb:'Набор высоты',transit:'Перелет',survey:'Съемка',turn:'Разворот',hold:'Удержание',descent:'Снижение',parachute:'Спуск на парашюте',landing:'Посадка'};
export function sampleMission(mission:Mission,time:number){
 const first=mission.legs[0],last=mission.legs[mission.legs.length-1];
 if(time<mission.start_s)return {position:first.coordinates[0],altitude:0,phase:'Ожидание старта'};
 if(time>=mission.finish_s)return {position:last.coordinates[last.coordinates.length-1],altitude:0,phase:'На земле'};
 const leg=mission.legs.find(l=>time>=l.start&&time<l.end)||last;
 const fraction=Math.max(0,Math.min(1,(time-leg.start)/(leg.end-leg.start)));
 const a=leg.coordinates[0],b=leg.coordinates[leg.coordinates.length-1];
 return {position:[a[0]+(b[0]-a[0])*fraction,a[1]+(b[1]-a[1])*fraction] as Position,altitude:leg.altitude_start_m+(leg.altitude_end_m-leg.altitude_start_m)*fraction,phase:names[leg.phase]||leg.phase};
}

/** One mission per aircraft at `time`: the sortie in progress, else the next one, else the last one flown. */
export function currentSorties(missions:Mission[],time:number):Mission[]{
 const byAircraft=new Map<string,Mission[]>();
 missions.forEach(m=>byAircraft.set(m.aircraft_id,[...(byAircraft.get(m.aircraft_id)||[]),m]));
 return [...byAircraft.values()].map(list=>{
  const ordered=[...list].sort((a,b)=>a.start_s-b.start_s);
  return ordered.find(m=>time<m.finish_s)||ordered[ordered.length-1];
 });
}

/** Phase label with the sortie number; before a repeat sortie the aircraft is serviced, queued or charging. */
export function sortieState(mission:Mission,time:number){
 const sample=sampleMission(mission,time),sortie=mission.sortie??1;
 if(sortie===1)return sample;
 let phase=sample.phase;
 if(phase==='Ожидание старта'){
  const {charge_start_s:begin,charge_end_s:end,charge_wait_s:wait=0}=mission;
  if(begin===undefined||end===undefined)phase='Подготовка';
  else if(time>=end)phase='Заряжен, ждет вылета';
  else if(time>=begin)phase=`Зарядка, осталось ${Math.ceil((end-time)/60)} мин`;
  else phase=time>=begin-wait?'Ожидание свободного ЗУ':'Обслуживание';
 }
 return {...sample,phase:`${phase} · рейс ${sortie}`};
}
