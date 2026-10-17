import { useState } from 'react';
import type { AircraftProfile, CostModel, CostPreset, Draft, PresetInput } from '../../shared/types';
import { airtimeRate, costModelError, costValue, DEFAULT_COST, presetModel, resolveCost } from '../scenarios/requirements';

const fmt=(x:number,d=0)=>x.toLocaleString('ru-RU',{maximumFractionDigits:d});
const STATUS:Record<string,string>={confirmed:'подтверждено',computed:'вычислено',assumed:'допущение',unknown:'неизвестно'};

/** The model J is computed with: the scenario's own, else the open-data preset for this fleet. */
export function effectiveCost(draft:Draft,preset:CostPreset|null):CostModel{
 return draft.cost_model||(preset?presetModel(draft.fleet,preset):DEFAULT_COST);
}
export function costSummary(draft:Draft,preset:CostPreset|null){
 const m=effectiveCost(draft,preset);
 return `α ${fmt(m.alpha_per_h)} · c ${fmt(m.airtime_default??0)}${draft.cost_model?'':' · открытые данные'}`;
}

function Source({label,input}:{label:string;input:PresetInput}){
 return <tr><td>{label}</td><td>{fmt(input.value,3)}</td><td><span className={`status-tag ${input.status}`}>{STATUS[input.status]}</span></td><td>{input.source}</td></tr>;
}

/** J = α·T + cᵀt + tᵀQt: the main weights up front, per-aircraft coefficients and pairs on demand (never a 20×20 grid). */
export default function CostModelEditor({draft,onChange,preset,catalog=[]}:{draft:Draft;onChange:(draft:Draft)=>void;preset:CostPreset|null;catalog?:AircraftProfile[]}){
 const model=effectiveCost(draft,preset),ids=draft.fleet.map(a=>a.id);
 const [pairA,setPairA]=useState('');const [pairB,setPairB]=useState('');
 // The first edit turns the preset into the scenario's own model, so what is shown is what is saved.
 const set=(patch:Partial<CostModel>)=>onChange({...draft,cost_model:{...model,airtime:model.airtime||{},...patch}});
 const setEntry=(key:'airtime'|'diagonal',id:string,value:string,asNumber:number)=>{const map={...(model[key]||{})};if(value==='')delete map[id];else map[id]=asNumber;set({[key]:map});};
 // Per aircraft: own entry, else its type's, else the default — the same order as on the server.
 const resolved=resolveCost(model,draft.fleet);
 const types=[...new Set(draft.fleet.map(a=>a.profile_id))];
 const typeOf=(id:string)=>draft.fleet.find(a=>a.id===id)?.profile_id||'';
 const typeName=(pid:string)=>catalog.find(p=>p.id===pid)?.name||pid;
 const setType=(pid:string,key:'airtime'|'diagonal',value:string,asNumber:number)=>{
  const byProfile={...(model.by_profile||{})};const entry={...(byProfile[pid]||{})};
  if(value==='')delete entry[key];else entry[key]=asNumber;
  if(entry.airtime==null&&entry.diagonal==null)delete byProfile[pid];else byProfile[pid]=entry;
  set({by_profile:byProfile});
 };
 const inherited=(id:string,key:'airtime'|'diagonal')=>{const t=model.by_profile?.[typeOf(id)]?.[key];return t!=null?`${fmt(t,2)} (тип)`:`${fmt(key==='airtime'?model.airtime_default??0:model.default_diagonal,2)} (по умолч.)`;};
 const error=costModelError(resolved,ids);
 const a=ids.includes(pairA)?pairA:ids[0]||'',b=ids.includes(pairB)&&pairB!==a?pairB:ids.find(id=>id!==a)||'';
 const exists=model.pairs.some(p=>(p.a===a&&p.b===b)||(p.a===b&&p.b===a));
 const example=ids.length>=2?costValue(resolved,ids.slice(0,2),2,{[ids[0]]:1.5,[ids[1]]:1}):null;
 const number=(value:number,onValue:(v:number)=>void,label:string,min=0)=><input type="number" step="any" min={min} max={1e7} aria-label={label} value={Number.isFinite(value)?value:''} onChange={e=>onValue(e.target.value===''?0:e.target.valueAsNumber)}/>;
 const own=Boolean(draft.cost_model),fromPreset=!own&&Boolean(preset);
 return <div data-nav="cost">
  <p className="hint"><b>J = α·T + cᵀt + tᵀQt</b> — условная стоимость операции в у.е. T — время до посадки последнего борта, ч; t<sub>i</sub> — налет борта i за все вылеты, ч (ожидание и зарядка не входят).
   α·T — цена срока операции (бригада, транспорт); c<sub>i</sub>·t<sub>i</sub> — цена налета борта (износ и риск потери, ресурс АКБ); q<sub>ii</sub>·t<sub>i</sub>² — риск повторного облета работы борта при его отказе, растущий с объемом работы на одном борту; 2·q<sub>ij</sub>·t<sub>i</sub>·t<sub>j</sub> — взаимодействие пары (общий пилот, общая зарядка). J — компромисс между сроком и налетом: чем больше c относительно α, тем выгоднее меньше летать, а не быстрее закончить.</p>
  <p className={fromPreset?'note':'hint'}>{fromPreset?<>Коэффициенты по умолчанию — <b>оценка по открытым данным</b> (ред. {preset!.edition}, 1 у.е. ≈ 1 ₽), не данные заказчика. Любое изменение сохранит модель в сценарии.</>:
   own?'Модель задана в сценарии.':'Пресет открытых данных не загружен: показаны значения без стоимости налета.'}</p>
  {own&&!(model.airtime_default??0)&&!Object.values(model.airtime||{}).some(v=>v>0)&&<p className="fleet-warning">В модели нет стоимости налета (c = 0): J учитывает только срок и квадратичную часть и обычно совпадает с планом «Быстрее». Так считались сценарии, сохраненные до 17.10.2026; «Заполнить по открытым данным» добавит c.</p>}
  <div className="row">
   <label data-nav="cost:alpha">α, у.е./ч — час срока{number(model.alpha_per_h,v=>set({alpha_per_h:v}),'α, у.е. за час срока операции')}</label>
   <label data-nav="cost:airtime">c по умолчанию, у.е./ч налета{number(model.airtime_default??0,v=>set({airtime_default:v}),'Стоимость часа налета по умолчанию')}</label>
   <label data-nav="cost:default">q по умолчанию, у.е./ч²{number(model.default_diagonal,v=>set({default_diagonal:v}),'Индивидуальный коэффициент по умолчанию')}</label>
  </div>
  {example&&<p className="hint">Пример: T = 2 ч, {ids[0]} налетал 1,5 ч, {ids[1]} — 1 ч: α·T = {fmt(example.linear)}, cᵀt = {fmt(example.flying)}, tᵀQt = {fmt(example.quadratic,1)}, J = {fmt(example.total)} у.е.</p>}
  <div className="row actions">
   {preset&&<button onClick={()=>onChange({...draft,cost_model:presetModel(draft.fleet,preset)})}>Заполнить по открытым данным</button>}
   {own&&<button onClick={()=>onChange({...draft,cost_model:null})}>Сбросить к умолчанию</button>}
   <button onClick={()=>set({airtime_default:0,airtime:{}})} title="J = α·T + tᵀQt — исходная формула без стоимости налета">Без стоимости налета (c = 0)</button>
  </div>
  <details className="subtle" data-nav="cost:types" open={Boolean(Object.keys(model.by_profile||{}).length)}><summary>По типам бортов · {Object.keys(model.by_profile||{}).length} из {types.length}</summary>
   {types.length>0?<table className="data-table compact" aria-label="Коэффициенты по типам бортов"><thead><tr><th>Тип</th><th>Бортов</th><th>c, у.е./ч</th><th>q, у.е./ч²</th></tr></thead><tbody>
    {types.map(pid=><tr key={pid}><td>{typeName(pid)}</td><td>{draft.fleet.filter(a=>a.profile_id===pid).length}</td>
     <td><input type="number" step="any" min={0} aria-label={`Стоимость часа налета для типа ${typeName(pid)}`} placeholder={`${fmt(model.airtime_default??0)} (по умолч.)`} value={model.by_profile?.[pid]?.airtime??''} onChange={e=>setType(pid,'airtime',e.target.value,e.target.valueAsNumber)}/></td>
     <td><input type="number" step="any" min={0} aria-label={`Коэффициент q для типа ${typeName(pid)}`} placeholder={`${fmt(model.default_diagonal,2)} (по умолч.)`} value={model.by_profile?.[pid]?.diagonal??''} onChange={e=>setType(pid,'diagonal',e.target.value,e.target.valueAsNumber)}/></td></tr>)}
   </tbody></table>:<p className="muted">Добавьте борта в парк.</p>}
   <p className="hint">Значение типа действует на все борта этого типа, у которых нет своего значения в таблице ниже. Порядок: свое значение борта → значение типа → значение по умолчанию.</p>
  </details>
  <details className="subtle" data-nav="cost:matrix"><summary>Коэффициенты отдельных бортов и пары · {Object.keys(model.airtime||{}).length} c · {Object.keys(model.diagonal).length} q · {model.pairs.length} пар</summary>
   {!ids.length&&<p className="muted">Добавьте борта в парк.</p>}
   {ids.length>0&&<table className="data-table compact" aria-label="Коэффициенты бортов"><thead><tr><th>Борт</th><th>c<sub>i</sub>, у.е./ч</th><th>q<sub>ii</sub>, у.е./ч²</th></tr></thead><tbody>
    {ids.map(id=><tr key={id}><td>{id}</td>
     <td><input type="number" step="any" min={0} aria-label={`Стоимость часа налета ${id}`} placeholder={inherited(id,'airtime')} value={model.airtime?.[id]??''} onChange={e=>setEntry('airtime',id,e.target.value,e.target.valueAsNumber)}/></td>
     <td><input type="number" step="any" min={0} aria-label={`Коэффициент ${id}`} placeholder={inherited(id,'diagonal')} value={model.diagonal[id]??''} onChange={e=>setEntry('diagonal',id,e.target.value,e.target.valueAsNumber)}/></td></tr>)}</tbody></table>}
   {model.pairs.length>0&&<table className="data-table compact" aria-label="Парные коэффициенты"><thead><tr><th>Пара</th><th>q<sub>ij</sub>, у.е./ч²</th><th/></tr></thead><tbody>
    {model.pairs.map((p,n)=><tr key={`${p.a}|${p.b}`}><td>{p.a} — {p.b}</td><td>{number(p.q,v=>set({pairs:model.pairs.map((x,i)=>i===n?{...x,q:v}:x)}),`Коэффициент пары ${p.a} и ${p.b}`,-1e6)}</td>
     <td><button className="small-button" onClick={()=>set({pairs:model.pairs.filter((_,i)=>i!==n)})} aria-label={`Удалить пару ${p.a} и ${p.b}`}>Убрать</button></td></tr>)}</tbody></table>}
   {ids.length>=2&&<div className="row actions"><select aria-label="Первый борт пары" value={a} onChange={e=>setPairA(e.target.value)}>{ids.map(id=><option key={id}>{id}</option>)}</select>
    <select aria-label="Второй борт пары" value={b} onChange={e=>setPairB(e.target.value)}>{ids.filter(id=>id!==a).map(id=><option key={id}>{id}</option>)}</select>
    <button disabled={!a||!b||exists} onClick={()=>set({pairs:[...model.pairs,{a,b,q:0}]})}>+ Пара</button></div>}
   <p className="hint">Q симметрична: пара задается один раз и входит в J дважды (q<sub>ij</sub> = q<sub>ji</sub>). Q должна быть положительно полуопределенной; отрицательный парный коэффициент допустим, пока мал: для пары нужно q<sub>ij</sub>² ≤ q<sub>ii</sub>·q<sub>jj</sub>, всю матрицу проверяет программа. Коэффициенты привязаны к бортовым номерам и переносятся при переименовании. Повод поднять q борта — например, он близок к ТО и его налет нужно ограничить.</p>
  </details>
  {preset&&<details className="subtle"><summary>Откуда коэффициенты по умолчанию · открытые данные {preset.edition}</summary>
   <p className="hint">{preset.formulas.alpha}<br/>{preset.formulas.airtime}<br/>{preset.formulas.diagonal}</p>
   <div className="table-scroll"><table className="data-table compact" aria-label="Источники коэффициентов"><thead><tr><th>Величина</th><th>Значение</th><th>Статус</th><th>Источник</th></tr></thead><tbody>
    <Source label="Зарплата оператора, ₽/мес" input={preset.alpha_inputs.salary}/><Source label="Страховые взносы, доля" input={preset.alpha_inputs.contributions}/>
    <Source label="Рабочих часов в месяце" input={preset.alpha_inputs.hours_per_month}/><Source label="Человек в бригаде" input={preset.alpha_inputs.team}/>
    <Source label="Транспорт и суточные, ₽/ч" input={preset.alpha_inputs.transport}/><Source label="λ — отказов на летный час" input={preset.failure_rate}/>
    {Object.entries(preset.by_profile).filter(([,p])=>!p.generic).flatMap(([id,p])=>[
     <Source key={`${id}p`} label={`${id}: цена борта, ₽`} input={p.inputs.price}/>,<Source key={`${id}b`} label={`${id}: цена АКБ, ₽`} input={p.inputs.battery_price}/>,<Source key={`${id}c`} label={`${id}: циклов АКБ`} input={p.inputs.battery_cycles}/>])}
   </tbody></table></div>
   <p className="hint">Итог: α = {fmt(preset.alpha_per_h)} у.е./ч; {Object.entries(preset.by_profile).filter(([,p])=>!p.generic).map(([id,p])=>`${id}: c = ${fmt(p.airtime)} (риск потери ${fmt(p.loss)} + АКБ ${fmt(p.battery)}), q = ${fmt(p.diagonal,2)}`).join('; ')}. Пользовательские модели БПЛА — как Gemini. Квадратичная часть по открытым данным мала: она заметна для долгих операций или когда оператор задает q сам.</p>
  </details>}
  {error&&<p className="error" role="alert">{error}</p>}
  <p className="hint">J считается для любого плана; оптимизирует J цель «Стоимость». У каждого борта: {ids.slice(0,4).map(id=>`${id} c = ${fmt(airtimeRate(resolved,id))}`).join(', ')}{ids.length>4?' …':''}.</p>
 </div>;
}
