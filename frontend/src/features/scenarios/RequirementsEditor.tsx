import { useState } from 'react';
import type { AircraftProfile, Draft, Payload, SensorProfile, SurveyRequirement } from '../../shared/types';
import { areaIds, isMulti, newRequirement, requirementIssues, sensorsOf, sensorUsable, SURVEY_TYPES, TYPE_NAMES, upgradeDraft } from './requirements';

type Props={draft:Draft;onChange:(draft:Draft)=>void;sensors:SensorProfile[];catalog:AircraftProfile[];onOpenFleet:()=>void};

function Optional({label,value,min,max,step=1,nav,placeholder,onChange}:{label:string;value:number|null|undefined;min:number;max:number;step?:number;nav:string;placeholder:string;onChange:(v:number|null)=>void}){
 return <label data-nav={nav}>{label}<input type="number" min={min} max={max} step={step} placeholder={placeholder} value={value??''}
  onChange={e=>onChange(e.target.value===''?null:Math.max(min,Math.min(max,e.target.valueAsNumber||min)))}/></label>;
}

/** Who can fly a requirement: aircraft carrying a sensor of its type that is confirmed for the aircraft model. */
export function capableAircraft(draft:Draft,requirement:SurveyRequirement,sensors:SensorProfile[],catalog:AircraftProfile[]){
 const byId=new Map(sensors.map(s=>[s.id,s]));
 return draft.fleet.flatMap(a=>{
  const profile=catalog.find(p=>p.id===a.profile_id);
  const own=sensorsOf(a).map(id=>byId.get(id)).filter((s):s is SensorProfile=>Boolean(s)&&s!.sensor_type===requirement.survey_type);
  const usable=own.filter(s=>sensorUsable(s,a.profile_id,profile?.origin==='user'));
  return own.length?[{aircraft:a.id,sensors:usable.map(s=>s.name),incompatible:own.filter(s=>!usable.includes(s)).map(s=>s.name)}]:[];
 });
}

export default function RequirementsEditor({draft,onChange,sensors,catalog,onOpenFleet}:Props){
 const [type,setType]=useState<Payload>('thermal');
 if(!isMulti(draft))return <div data-nav="requirements:convert">
  <p className="note">Сценарий в формате 1.1: один вид съемки — <b>{TYPE_NAMES[draft.survey_type]}</b> — на все зоны, параметры в разделе «Съемка». Расчет идет как раньше.</p>
  <p className="hint">Чтобы задать несколько видов съемки (например, ортофото поля и тепловизор объекта), преобразуйте сценарий: появится одно требование с текущими параметрами, у бортов — список сенсоров из выбранных камер. План после преобразования не меняется. Отменить — «Отменить» над картой.</p>
  <button onClick={()=>onChange(upgradeDraft(draft))}>Преобразовать в требования съемки</button>
 </div>;
 const ids=areaIds(draft),issues=requirementIssues(draft);
 const requirements=draft.requirements||[];
 const set=(id:string,patch:Partial<SurveyRequirement>)=>onChange({...draft,requirements:requirements.map(r=>r.id===id?{...r,...patch}:r)});
 return <>
  <p className="hint">Требование — зоны + вид съемки + параметры качества. Каждое покрывается и проверяется отдельно, площади разных видов не суммируются. За вылет борт несет один сенсор; смена нагрузки между вылетами — {draft.settings.payload_change_min??15} мин (раздел «Вылеты и зарядка»).</p>
  {issues.filter(i=>!i.id).map(i=><p className="fleet-warning" key={i.text}>{i.text}</p>)}
  {requirements.map(r=>{
   const capable=capableAircraft(draft,r,sensors,catalog),usable=capable.filter(c=>c.sensors.length);
   const frame=r.survey_type==='rgb'||r.survey_type==='multispectral';
   const own=issues.filter(i=>i.id===r.id);
   const nav=(field:string)=>`requirement:${r.id}:${field}`;
   return <article className="requirement-card" key={r.id} aria-label={`Требование ${r.name||r.id}`}>
    <div className="site-head"><span className={`type-dot type-${r.survey_type}`} aria-hidden="true"/><input aria-label={`Название требования ${r.id}`} data-nav={nav('name')} maxLength={120} value={r.name} onChange={e=>set(r.id,{name:e.target.value})}/>
     <button className="icon-button" aria-label={`Удалить требование ${r.name||r.id}`} onClick={()=>onChange({...draft,requirements:requirements.filter(x=>x.id!==r.id)})}>×</button></div>
    <small className="muted">ID {r.id}</small>
    <label data-nav={nav('survey_type')}>Вид съемки<select value={r.survey_type} onChange={e=>set(r.id,{survey_type:e.target.value as Payload})}>{SURVEY_TYPES.map(t=><option key={t} value={t}>{TYPE_NAMES[t]}</option>)}</select></label>
    <fieldset className="zone-picks" data-nav={nav('area_ids')}><legend>Зоны</legend>
     {ids.map((id,n)=><label className="check-row" key={id}><input type="checkbox" checked={r.area_ids.includes(id)} onChange={e=>set(r.id,{area_ids:e.target.checked?ids.filter(x=>x===id||r.area_ids.includes(x)):r.area_ids.filter(x=>x!==id)})}/> Зона {n+1} <small className="muted">{id}</small></label>)}
     {!ids.length&&<p className="muted">Зон пока нет: нарисуйте их на карте.</p>}
    </fieldset>
    <div className="row"><Optional label="Боковое перекрытие, %" nav={nav('side_overlap_percent')} value={r.side_overlap_percent} min={0} max={80} placeholder="30" onChange={v=>set(r.id,{side_overlap_percent:v??30})}/>
     {frame&&<Optional label="Продольное перекрытие, %" nav={nav('front_overlap_percent')} value={r.front_overlap_percent} min={0} max={90} placeholder="70" onChange={v=>set(r.id,{front_overlap_percent:v??70})}/>}</div>
    <div className="row">{frame&&<Optional label="Макс. GSD, см/пикс" nav={nav('max_gsd_cm')} value={r.max_gsd_cm} min={.1} max={100} step={.1} placeholder="без предела" onChange={v=>set(r.id,{max_gsd_cm:v})}/>}
     <Optional label="Высота, м" nav={nav('altitude_m')} value={r.altitude_m} min={20} max={300} placeholder="высота борта" onChange={v=>set(r.id,{altitude_m:v})}/></div>
    <div className="row">{r.survey_type==='geophysical'?<Optional label="Шаг линий, м" nav={nav('line_spacing_m')} value={r.line_spacing_m} min={5} max={1000} placeholder="нужен" onChange={v=>set(r.id,{line_spacing_m:v})}/>:
      <Optional label="Ширина полосы, м" nav={nav('swath_width_m')} value={r.swath_width_m} min={10} max={500} placeholder={frame?'по камере':'если нет геометрии'} onChange={v=>set(r.id,{swath_width_m:v})}/>}
     <Optional label="Макс. скорость, м/с" nav={nav('max_speed_mps')} value={r.max_speed_mps} min={2} max={100} step={.5} placeholder="без предела" onChange={v=>set(r.id,{max_speed_mps:v})}/></div>
    {own.map(i=><p className="fleet-warning" key={i.text}>{i.text}</p>)}
    {usable.length?<p className="hint">Могут выполнить: {usable.map(c=>`${c.aircraft} (${c.sensors.join(', ')})`).join('; ')}.</p>:
     <p className="fleet-warning">Нет борта с совместимым сенсором вида «{TYPE_NAMES[r.survey_type]}». <button className="link-button" onClick={onOpenFleet}>Открыть парк</button></p>}
    {capable.some(c=>c.incompatible.length)&&<p className="hint warn">Не подтверждены для своей модели: {capable.filter(c=>c.incompatible.length).map(c=>`${c.aircraft} — ${c.incompatible.join(', ')}`).join('; ')}.</p>}
   </article>;
  })}
  <div className="row actions"><label>Новое требование<select value={type} onChange={e=>setType(e.target.value as Payload)}>{SURVEY_TYPES.map(t=><option key={t} value={t}>{TYPE_NAMES[t]}</option>)}</select></label>
   <button onClick={()=>onChange({...draft,requirements:[...requirements,newRequirement(draft,type)]})}>+ Добавить</button></div>
  <p className="hint">Высота требования фиксирует высоту съемки для всех бортов этого требования; пусто — каждый борт летит на своей высоте или эшелоне. Для сенсоров без подтвержденной геометрии нужна ширина полосы.</p>
 </>;
}
