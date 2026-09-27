import { useEffect, useState } from 'react';
import type { Polygon } from 'geojson';
import { api } from '../../shared/api';
import type { AircraftProfile, Draft, SunTimes, SurveySettings } from '../../shared/types';
import { bboxOf } from './bbox';

const pad=(n:number)=>String(n).padStart(2,'0');
/** ISO instant → value for <input type="datetime-local"> in the browser's zone. */
export function toLocalInput(iso:string|null|undefined){
 if(!iso)return '';
 const d=new Date(iso);
 return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const time=(iso:string|null)=>iso?new Date(iso).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):'—';

function Numeric({label,value,min,max,step=1,empty,onChange}:{label:string;value:number|null|undefined;min:number;max:number;step?:number;empty?:string;onChange:(value:number|null)=>void}){
 return <label>{label}<input type="number" min={min} max={max} step={step} placeholder={empty} value={value??''}
  onChange={e=>onChange(e.target.value===''?(empty?null:min):Math.max(min,Math.min(max,e.target.valueAsNumber)))}/></label>;
}

type Props={draft:Draft;onChange:(draft:Draft)=>void};
const patcher=({draft,onChange}:Props)=>(patch:Partial<SurveySettings>)=>onChange({...draft,settings:{...draft.settings,...patch}});

export function airspaceSummary(draft:Draft){
 const zones=draft.restriction_zones?.length||0,obstacles=draft.obstacles?.length||0;
 return `${zones} зон · ${obstacles} преп.`;
}

export function AirspaceFields(props:Props){
 const s=props.draft.settings,set=patcher(props);
 return <>
  <div className="row"><Numeric label="Рельеф, мин. м над ур. моря" value={s.terrain_min_m??150} min={-450} max={6000} onChange={v=>set({terrain_min_m:v??150,terrain_max_m:Math.max(v??150,s.terrain_max_m??150)})}/>
   <Numeric label="Рельеф, макс. м" value={s.terrain_max_m??150} min={-450} max={6000} onChange={v=>set({terrain_max_m:Math.max(v??150,s.terrain_min_m??150)})}/></div>
  <div className="row"><Numeric label="Вертикальный запас, м" value={s.vertical_buffer_m??50} min={0} max={500} onChange={v=>set({vertical_buffer_m:v??50})}/>
   <Numeric label="Отступ от препятствий, м" value={s.obstacle_clearance_m??50} min={5} max={1000} onChange={v=>set({obstacle_clearance_m:v??50})}/></div>
  <label>Участки под ограничениями<select value={s.blocked_area_policy??'reject'} onChange={e=>set({blocked_area_policy:e.target.value as SurveySettings['blocked_area_policy']})}>
   <option value="reject">Запрещают расчет — нужно изменить район</option><option value="exclude">Исключаются из съемки с отчетом о площади</option></select></label>
  <p className="hint">Границы зон по уровню моря (AMSL) и в эшелонах FL пересчитываются по диапазону рельефа в худшую сторону. Зона учитывается, если достает до самой высокой высоты полета плюс запас, препятствие — до самой низкой.</p>
 </>;
}

export function deadlineSummary(draft:Draft){
 const s=draft.settings;
 const parts=[s.time_limit_min?`срок ${s.time_limit_min} мин`:'',s.until_sunset?'до заката':'',s.max_aircraft?`≤ ${s.max_aircraft} бортов`:''].filter(Boolean);
 return parts.join(' · ')||'не заданы';
}

export function DeadlineFields({draft,onChange,areas}:Props&{areas:Polygon[]}){
 const s=draft.settings,set=patcher({draft,onChange});
 const [sun,setSun]=useState<SunTimes|null>(null);
 const box=bboxOf(areas);
 const lon=box?(box[0]+box[2])/2:null,lat=box?(box[1]+box[3])/2:null;
 const day=s.operation_start?toLocalInput(s.operation_start).slice(0,10):'';
 useEffect(()=>{
  if(lat===null||lon===null||!day){setSun(null);return;}
  let active=true;
  api.sun(lat,lon,day).then(v=>{if(active)setSun(v);}).catch(()=>{if(active)setSun(null);});
  return()=>{active=false;};
 },[lat?.toFixed(3),lon?.toFixed(3),day]);
 return <>
  <label>Начало работ<input type="datetime-local" value={toLocalInput(s.operation_start)} onChange={e=>set({operation_start:e.target.value?new Date(e.target.value).toISOString():null,until_sunset:e.target.value?s.until_sunset:false})}/></label>
  <div className="row"><Numeric label="Срок, мин" value={s.time_limit_min} min={1} max={10080} empty="не задан" onChange={v=>set({time_limit_min:v})}/>
   <Numeric label="Не более бортов" value={s.max_aircraft} min={1} max={20} empty="все" onChange={v=>set({max_aircraft:v})}/></div>
  <label className="check-row"><input type="checkbox" disabled={!s.operation_start} checked={Boolean(s.until_sunset)} onChange={e=>set({until_sunset:e.target.checked})}/> Закончить до заката{!s.operation_start?' (задайте начало работ)':''}</label>
  {sun&&<p className="hint">{sun.polar?`Полярный ${sun.polar==='day'?'день':'ночь'}.`:`Восход ${time(sun.sunrise)}, закат ${time(sun.sunset)}, конец гражданских сумерек ${time(sun.civil_dusk)} (время браузера).`}
   {s.until_sunset&&sun.sunset&&s.operation_start?` До заката ${Math.max(0,(Date.parse(sun.sunset)-Date.parse(s.operation_start))/60000).toFixed(0)} мин.`:''}</p>}
  <p className="hint">«Анализ парка» под кнопкой расчета подбирает число бортов под срок.</p>
 </>;
}

export function sortieSummary(draft:Draft){
 const s=draft.settings,count=s.sorties_per_aircraft??1;
 if(count<=1)return 'один вылет';
 return `до ${count} · ${s.recharge_mode==='charge'?'зарядка':'замена АКБ'}`;
}

export function SortieFields({draft,onChange,catalog}:Props&{catalog:AircraftProfile[]}){
 const s=draft.settings,set=patcher({draft,onChange});
 const mode=s.recharge_mode??'swap',count=s.sorties_per_aircraft??1;
 const used=catalog.filter(p=>draft.fleet.some(a=>a.profile_id===p.id));
 const chargers=draft.sites.reduce((sum,site)=>sum+(site.chargers??(site.kind==='launch_landing'?2:0)),0);
 return <>
  <Numeric label="Вылетов на борт" value={count} min={1} max={20} onChange={v=>set({sorties_per_aircraft:v??1})}/>
  <div className="segmented" role="radiogroup" aria-label="Между вылетами">
   <button role="radio" aria-checked={mode==='swap'} className={mode==='swap'?'active':''} onClick={()=>set({recharge_mode:'swap'})}>Замена АКБ</button>
   <button role="radio" aria-checked={mode==='charge'} className={mode==='charge'?'active':''} onClick={()=>set({recharge_mode:'charge'})}>Зарядка на площадке</button>
  </div>
  {mode==='swap'?<>
   <Numeric label="Замена АКБ и подготовка, мин" value={s.turnaround_min??15} min={0} max={240} onChange={v=>set({turnaround_min:v??15})}/>
   <p className="hint">Заряженная запасная АКБ ставится сразу; повторный вылет — с той же площадки. Наличие запасных АКБ не проверяется.</p>
  </>:<>
   <div className="row"><Numeric label="Полная зарядка, мин" value={s.charge_time_min??105} min={1} max={1440} onChange={v=>set({charge_time_min:v??105})}/>
    <Numeric label="Обслуживание, мин" value={s.ground_handling_min??5} min={0} max={120} onChange={v=>set({ground_handling_min:v??5})}/></div>
   <p className="hint">Запасных АКБ нет: борт ждет, пока его АКБ зарядится на ЗУ площадки. Каждое ЗУ заряжает одну АКБ за раз, остальные ждут в очереди.
    Борт может сесть на любую площадку с зарядными местами, в том числе запасную, и взлететь оттуда. Время зарядки пропорционально израсходованной емкости.</p>
   {used.map(p=><p className="hint" key={p.id}>{p.name}: {p.charge_time_min?`${p.charge_time_min} мин — ${p.charge_source?.split(';')[0]}`:`${s.charge_time_min??105} мин (значение выше, в профиле нет данных)`}</p>)}
   <p className={chargers?'hint':'hint warn'}>Зарядных мест на площадках: {chargers}. Число ЗУ задается в карточке площадки на вкладке «Район».</p>
  </>}
  {count<=1&&<p className="hint">При одном вылете на борт режим между вылетами не используется.</p>}
 </>;
}
