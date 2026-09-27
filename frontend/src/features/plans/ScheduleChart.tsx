import type { Plan } from '../../shared/types';
import { SCHEDULE_COLORS } from '../../app/theme';
import AircraftTag from './AircraftTag';
import { clock, scheduleRows, tickStep, type Segment } from './schedule';

const LABELS:Record<Segment['kind'],string>={flight:'Полет',handling:'Обслуживание',waiting:'Ожидание ЗУ',charging:'Зарядка',swap:'Замена АКБ'};

export default function ScheduleChart({plan,time,onTime,siteName}:{plan:Plan;time:number;onTime:(value:number)=>void;siteName:(id:string)=>string}){
 const rows=scheduleRows(plan.missions);
 const end=Math.max(plan.metrics.completion_time_s,...rows.flatMap(r=>r.segments.map(s=>s.end)),1);
 const step=tickStep(end);
 const ticks=Array.from({length:Math.floor(end/step)+1},(_,i)=>i*step);
 const x=(seconds:number)=>`${Math.min(100,seconds/end*100)}%`;
 const kinds=new Set(rows.flatMap(r=>r.segments.map(s=>s.kind)));
 const seek=(event:React.MouseEvent<HTMLDivElement>)=>{const box=event.currentTarget.getBoundingClientRect();onTime(Math.max(0,Math.min(end,(event.clientX-box.left)/box.width*end)));};
 const title=(s:Segment)=>{
  const m=s.mission,span=`${clock(s.start)} – ${clock(s.end)}`;
  if(s.kind==='flight')return `${m.aircraft_id}, вылет ${m.sortie??1}: ${span} · ${siteName(m.site_id)} → ${siteName(m.landing_site_id||m.site_id)}`;
  if(s.kind==='charging')return `Зарядка АКБ ${clock(s.end-s.start)} на площадке ${siteName(m.charge_site_id||'')}: ${span}`;
  if(s.kind==='waiting')return `Ожидание свободного ЗУ ${clock(s.end-s.start)}: ${span}`;
  return `${LABELS[s.kind]} ${clock(s.end-s.start)}: ${span}`;
 };
 return <section className="schedule" aria-label="Расписание бортов">
  <div className="schedule-head"><h3>Расписание бортов</h3>
   <div className="schedule-legend">{(['flight','handling','waiting','charging','swap'] as const).filter(k=>kinds.has(k)).map(k=><span key={k}><i className={`seg-${k}`} style={k==='charging'?{background:SCHEDULE_COLORS.charging}:undefined}/>{LABELS[k]}</span>)}</div></div>
  <div className="schedule-grid">
   <div className="schedule-axis"><div/><div className="schedule-track">{ticks.map(t=><span key={t} style={{left:x(t)}}>{clock(t)}</span>)}</div></div>
   {rows.map(row=><div className="schedule-row" key={row.aircraftId}>
    <div className="schedule-label"><AircraftTag mission={{aircraft_id:row.aircraftId,profile_id:row.profileId,color:row.color}}/></div>
    <div className="schedule-track" onClick={seek} role="presentation">
     {ticks.map(t=><i key={t} className="schedule-tick" style={{left:x(t)}}/>)}
     {row.segments.map((s,index)=><div key={index} className={`seg seg-${s.kind}`} title={title(s)}
      style={{left:x(s.start),width:`max(2px, ${(s.end-s.start)/end*100}%)`,...(s.kind==='flight'?{background:row.color}:s.kind==='charging'?{background:SCHEDULE_COLORS.charging}:{})}}>
      {s.kind==='flight'&&(s.end-s.start)/end>.04?<b>{s.mission.sortie??1}</b>:null}</div>)}
     <i className="schedule-playhead" style={{left:x(time)}}/>
    </div>
   </div>)}
  </div>
  <p className="muted">Щелчок по дорожке переводит симуляцию на это время. Цифра на полете — номер вылета.</p>
 </section>;
}
