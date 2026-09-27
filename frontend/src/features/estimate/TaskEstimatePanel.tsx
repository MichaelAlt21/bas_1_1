import { useState } from 'react';
import type { Polygon } from 'geojson';
import { api } from '../../shared/api';
import { plots } from '../../shared/plural';
import type { AircraftProfile, Draft, TaskEstimate, WorkPackage } from '../../shared/types';

interface Props {areas:Polygon[];draft:Draft;catalog:AircraftProfile[];estimate:TaskEstimate|null;selected:string|null;disabled:boolean;
 onEstimate:(estimate:TaskEstimate|null)=>void;onSelect:(id:string)=>void;onTake:(pack:WorkPackage)=>void;onClose:()=>void}

const hours=(value:number)=>value>=10?`${value.toFixed(0)} ч`:`${value.toFixed(1)} ч`;

export default function TaskEstimatePanel({areas,draft,catalog,estimate,selected,disabled,onEstimate,onSelect,onTake,onClose}:Props){
 const fleetProfile=draft.fleet[0]?.profile_id;
 const [profileId,setProfileId]=useState(fleetProfile&&catalog.some(p=>p.id===fleetProfile)?fleetProfile:'gemini');
 const [fleetSize,setFleetSize]=useState(Math.max(draft.fleet.length,5));
 const [workday,setWorkday]=useState(10);
 const [cell,setCell]=useState<number|''>('');
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const run=async()=>{
  setBusy(true);setError('');
  try{onEstimate(await api.taskEstimate({areas,profile_id:profileId,fleet_size:fleetSize,workday_h:workday,cell_km:cell===''?null:cell,settings:draft.settings,
   aircraft_profiles:catalog.filter(p=>p.origin==='user')}));}
  catch(e){setError(e instanceof Error?e.message:'Оценка не выполнена');}
  finally{setBusy(false);}
 };
 const current=estimate?.packages.find(p=>p.id===selected)||null;
 const totals=estimate?.totals;
 return <section className="estimate-panel" aria-label="Оценка задания">
  <p className="muted">Задание больше, чем помещается в один детальный расчет. Оценка делит его на пакеты работ с площадкой в центре каждого; выберите пакет на карте или в таблице и возьмите его в детальный расчет.</p>
  <fieldset disabled={disabled||busy} className="editor compact">
   <label>Модель БПЛА<select value={profileId} onChange={e=>setProfileId(e.target.value)}>{catalog.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
   <div className="row"><label>Бортов в парке<input type="number" min={1} max={200} value={fleetSize} onChange={e=>setFleetSize(Math.max(1,Math.min(200,e.target.valueAsNumber||1)))}/></label>
    <label>Рабочий день, ч<input type="number" min={1} max={24} step={.5} value={workday} onChange={e=>setWorkday(Math.max(1,Math.min(24,e.target.valueAsNumber||1)))}/></label></div>
   <label>Размер пакета, км<input type="number" min={.5} max={30} step={.5} placeholder="авто — по дальности вылета" value={cell} onChange={e=>setCell(e.target.value===''?'':Math.max(.5,Math.min(30,e.target.valueAsNumber)))}/></label>
   <div className="row actions"><button className="primary" onClick={run}>{busy?'Оценка…':'Оценить задание'}</button><button onClick={onClose}>Закрыть задание</button></div>
  </fieldset>
  {error&&<p role="alert" className="error">{error}</p>}
  {estimate&&totals&&<>
   <div className="estimate-totals">
    <div><small>Площадь</small><strong>{totals.area_ha.toFixed(0)} га</strong></div>
    <div><small>Галсы</small><strong>{totals.lines_km.toFixed(0)} км</strong></div>
    <div><small>Вылеты</small><strong>{totals.sorties}</strong></div>
    <div><small>Налет</small><strong>{hours(totals.airtime_h)}</strong></div>
   </div>
   <p className="muted">Пакет {estimate.cell_km} км · шаг линий {estimate.line_spacing_m.toFixed(0)} м · вылет до {estimate.sortie_budget_min.toFixed(0)} мин, в среднем {totals.mean_sortie_min.toFixed(0)} мин · {totals.sorties_per_aircraft_day} вылетов на борт за день
    {totals.maintenance_intervals!==null?` · ресурс до ТО: ${(totals.maintenance_intervals*100).toFixed(0)}% интервала одного борта`:''}.</p>
   <div className="table-scroll"><table aria-label="Дни работы в зависимости от парка"><thead><tr><th>Бортов</th>{estimate.days_by_fleet.map(d=><th key={d.aircraft}>{d.aircraft}</th>)}</tr></thead>
    <tbody><tr><td>Рабочих дней</td>{estimate.days_by_fleet.map(d=><td key={d.aircraft}>{d.days}</td>)}</tr></tbody></table></div>
   {estimate.unreachable_packages.length>0&&<p className="error">Недостижимы для этой модели из центра пакета: {estimate.unreachable_packages.join(', ')}. Уменьшите размер пакета.</p>}
   {current&&<div className="package-card"><strong>{current.id}</strong> · {current.area_ha.toFixed(0)} га · {plots(current.parts)} · {current.lines_km.toFixed(1)} км галсов
    <br/>{current.reachable?`${current.sorties} вылетов · налет ${hours(current.airtime_h||0)}`:'недостижим из центра пакета'} · до дальней точки {current.farthest_km.toFixed(1)} км
    <div className="row actions"><button className="primary" disabled={disabled} onClick={()=>onTake(current)}>Взять пакет в детальный расчет</button></div>
    {current.parts>200&&<p className="muted">В детальный расчет попадут 200 крупнейших участков из {current.parts}.</p>}</div>}
   <div className="table-scroll package-table"><table aria-label="Пакеты работ"><thead><tr><th>Пакет</th><th>Площадь</th><th>Участков</th><th>Вылетов</th><th>Налет</th></tr></thead>
    <tbody>{estimate.packages.map(p=><tr key={p.id} className={p.id===selected?'selected':''} onClick={()=>onSelect(p.id)}><td><button className="link" onClick={()=>onSelect(p.id)}>{p.id}</button></td><td>{p.area_ha.toFixed(0)} га</td><td>{p.parts}</td>
     <td>{p.sorties??'—'}</td><td>{p.airtime_h===null?'—':hours(p.airtime_h)}</td></tr>)}</tbody></table></div>
   <details className="limitations"><summary>Допущения оценки</summary><ul>{estimate.assumptions.map(a=><li key={a}>{a}</li>)}</ul></details>
  </>}
 </section>;
}
