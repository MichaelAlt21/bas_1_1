import type { AirspaceReport, Obstacle, RestrictionZone, VerticalLimit } from '../../shared/types';
export const CATEGORY_NAMES:Record<string,string>={prohibited:'Запретные',restricted:'Ограничения',temporary:'Временные',danger:'Опасные',other:'Прочие'};

export interface MapInfo {title:string;lines:string[]}
export const ZONE_COLORS:Record<string,string>={prohibited:'#D92D20',restricted:'#7C3AED',temporary:'#CA8A04',danger:'#DB2777',other:'#64748B'};

export function limitText(limit:VerticalLimit){
 if(limit.reference==='GND')return 'земля';
 if(limit.reference==='UNL')return 'без ограничения';
 if(limit.reference==='FL')return `FL${String(Math.round(limit.value)).padStart(3,'0')}`;
 return `${limit.value} м ${limit.reference}`;
}

const DAY_NAMES:Record<string,string>={MON:'пн',TUE:'вт',WED:'ср',THU:'чт',FRI:'пт',SAT:'сб',SUN:'вс'};

export function zoneInfo(zone:RestrictionZone,report?:AirspaceReport):MapInfo{
 const lines=[`${CATEGORY_NAMES[zone.category]||zone.category} · от ${limitText(zone.lower)} до ${limitText(zone.upper)}${zone.parsed?'':' (текст высот не распознан — учитывается на всех высотах)'}`];
 const v=zone.validity;
 if(v.kind==='unknown')lines.push('Время действия неизвестно: считается действующей.');
 if(v.kind==='scheduled')lines.push('Действует: '+[...v.periods.map(p=>`${new Date(p.start).toLocaleString('ru-RU')} – ${new Date(p.end).toLocaleString('ru-RU')}`),
  ...v.weekly.map(w=>`${w.days.map(d=>DAY_NAMES[d]||d).join(', ')} ${w.start.slice(0,5)}–${w.end.slice(0,5)}`)].join('; ')+` (${v.timezone})`);
 if(report){
  const decide=(list:{id:string}[])=>list.some(z=>z.id===zone.id);
  lines.push(decide(report.zones_applied)?'В этом плане: облетается.':decide(report.zones_above)?'В этом плане: выше высоты полета с запасом.':
   decide(report.zones_inactive)?'В этом плане: не действует в окне операции.':decide(report.zones_below_ground)?'В этом плане: ниже поверхности.':'В этом плане: вне района.');
 }
 if(zone.notes)lines.push(zone.notes.length>300?zone.notes.slice(0,300)+'…':zone.notes);
 return {title:zone.name||zone.id,lines};
}

export function obstacleInfo(obstacle:Obstacle,report?:AirspaceReport):MapInfo{
 const lines=[`${obstacle.kind||'Препятствие'} · высота ${obstacle.height_m} м ${obstacle.height_reference==='AMSL'?'над уровнем моря':'над землей'}`];
 if(report){
  const hit=report.obstacles_applied.find(o=>o.id===obstacle.id);
  lines.push(hit?`В этом плане: облетается (${hit.height_agl_m} м над землей).`:'В этом плане: ниже высоты полета с запасом.');
 }
 return {title:obstacle.id,lines};
}
