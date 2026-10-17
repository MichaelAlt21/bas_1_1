import { useRef, useState } from 'react';
import { api, ApiError } from '../../shared/api';
import type { AircraftProfile, Draft, Payload, SensorProfile, SensorProfileInput } from '../../shared/types';
import { SURVEY_TYPES, TYPE_NAMES } from '../scenarios/requirements';

const STATUS:Record<string,string>={confirmed:'подтверждено',computed:'вычислено',assumed:'допущение',unknown:'неизвестно'};
const GEOMETRY:Record<SensorProfile['geometry'],string>={frame:'кадровая камера',scanner:'сканер (угол обзора)',line:'съемка по линиям',unknown:'геометрия не подтверждена'};
const PARAM_NAMES:Record<string,string>={sensor_width_mm:'ширина матрицы',sensor_height_mm:'высота матрицы',focal_length_mm:'фокусное расстояние',image_width_px:'ширина кадра',image_height_px:'высота кадра',field_of_view_deg:'угол обзора',compatible_profiles:'совместимость',swath:'полоса',gsd:'GSD'};
const empty=():SensorProfileInput=>({name:'',sensor_type:'thermal',geometry:'unknown',compatible_profiles:[],source:'',source_url:'',checked_at:null,demonstration:false});
const inputOf=(p:SensorProfile):SensorProfileInput=>{const {id:_i,version:_v,origin:_o,...rest}=p;return rest;};

/** The checks the server makes, worded for the operator; the server stays the authority. */
export function profileProblem(v:SensorProfileInput):string|null{
 if(!v.name.trim())return 'Задайте название профиля.';
 if(!v.source.trim())return 'Укажите источник данных: документ производителя и страницу или «допущение оператора».';
 const positive=(x:number|null|undefined)=>typeof x==='number'&&Number.isFinite(x)&&x>0;
 if(v.geometry==='frame'){
  const missing=([['sensor_width_mm','ширина матрицы, мм'],['sensor_height_mm','высота матрицы, мм'],['focal_length_mm','фокусное расстояние, мм'],['image_width_px','ширина кадра, пикс'],['image_height_px','высота кадра, пикс']] as const).filter(([k])=>!positive(v[k])).map(([,n])=>n);
  if(missing.length)return `Для кадровой камеры нужны: ${missing.join(', ')}.`;
 }
 if(v.geometry==='scanner'&&!(positive(v.field_of_view_deg)&&v.field_of_view_deg!<180))return 'Для сканера нужен угол обзора от 0 до 180°.';
 return null;
}

type Props={sensors:SensorProfile[];catalog:AircraftProfile[];draft:Draft;onChange:(draft:Draft)=>void;onCatalog:(sensors:SensorProfile[])=>void};

/** Sensor catalog: built-in profiles with the provenance of each parameter, user profiles as numbered versions, import and export. */
export default function SensorProfiles({sensors,catalog,draft,onChange,onCatalog}:Props){
 const [form,setForm]=useState<{id:string|null;value:SensorProfileInput}|null>(null);
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [pending,setPending]=useState<{document:unknown;conflicts:ApiError['conflicts']}|null>(null);
 const [versions,setVersions]=useState<{id:string;list:SensorProfile[]}|null>(null);
 const file=useRef<HTMLInputElement>(null);
 const refresh=async()=>onCatalog(await api.sensors());
 const run=async(action:()=>Promise<void>)=>{setError('');setNotice('');try{await action();}catch(e){setError(e instanceof Error?e.message:'Не удалось выполнить действие');}};
 const submit=()=>run(async()=>{
  if(!form)return;
  const problem=profileProblem(form.value);
  if(problem)throw new Error(problem);
  const saved=form.id?await api.updateSensor(form.id,form.value):await api.createSensor(form.value);
  await refresh();setForm(null);
  setNotice(form.id?`Сохранено как версия ${saved.version} профиля ${saved.id}. Прежние версии и сохраненные расчеты не меняются.`:`Создан профиль ${saved.id}.`);
 });
 const importFile=(document:unknown,onConflict:'reject'|'new_version'|'rename')=>run(async()=>{
  try{
   const report=await api.importSensors(document,onConflict);
   setPending(null);await refresh();
   setNotice([report.added.length&&`добавлено: ${report.added.join(', ')}`,report.new_versions.length&&`новые версии: ${report.new_versions.map(v=>`${v.id} v${v.version}`).join(', ')}`,
    report.renamed.length&&`переименовано: ${report.renamed.map(r=>`${r.id} → ${r.new_id}`).join(', ')}`,report.unchanged.length&&`без изменений: ${report.unchanged.join(', ')}`].filter(Boolean).join('; ')||'Нечего импортировать.');
  }catch(e){
   if(e instanceof ApiError&&e.status===409&&e.conflicts.length){setPending({document,conflicts:e.conflicts});setError(e.message);return;}
   throw e;
  }
 });
 // A scenario keeps the versions it was saved with; the operator decides when to take a newer one.
 const latest=new Map(sensors.map(s=>[s.id,s]));
 const outdated=(draft.sensor_profiles||[]).filter(s=>latest.has(s.id)&&latest.get(s.id)!.version!==s.version);
 const v=form?.value;
 const set=(patch:Partial<SensorProfileInput>)=>form&&setForm({...form,value:{...form.value,...patch}});
 const num=(label:string,key:keyof SensorProfileInput,step=.01)=><label>{label}<input type="number" step={step} min={0} value={(v?.[key] as number|null|undefined)??''} onChange={e=>set({[key]:e.target.value===''?null:e.target.valueAsNumber} as Partial<SensorProfileInput>)}/></label>;
 return <div className="sensor-profiles">
  <p className="hint">Профиль описывает геометрию сенсора и источник каждого параметра. Сохраненный сценарий и план хранят копию профиля той версии, с которой считались: правка профиля создает новую версию и не меняет прошлые расчеты.</p>
  {outdated.length>0&&<div className="note" data-nav="sensors:outdated">В сценарии — прежние версии: {outdated.map(s=>`${s.name} v${s.version} (в каталоге v${latest.get(s.id)!.version})`).join(', ')}.
   <button className="link-button" onClick={()=>onChange({...draft,sensor_profiles:(draft.sensor_profiles||[]).filter(s=>!outdated.includes(s))})}>Считать по версиям из каталога</button></div>}
  <ul className="sensor-list">{sensors.map(s=><li key={s.id} className="sensor-item">
   <details><summary><span className={`type-dot type-${s.sensor_type}`} aria-hidden="true"/><b>{s.name}</b>
    <span className="badges"><span className="badge">{TYPE_NAMES[s.sensor_type]}</span><span className="badge">{s.origin==='builtin'?'встроенный':`пользовательский · v${s.version}`}</span>{s.demonstration&&<span className="badge warn">демонстрационный</span>}</span></summary>
    <div className="sensor-detail">
     <p className="muted">{s.id} · {GEOMETRY[s.geometry]}{s.focal_length_mm?` · ${s.sensor_width_mm}×${s.sensor_height_mm} мм, f ${s.focal_length_mm} мм, ${s.image_width_px}×${s.image_height_px} пикс`:''}{s.field_of_view_deg?` · угол ${s.field_of_view_deg}°`:''}</p>
     <p className="muted">Совместим: {s.compatible_profiles.length?s.compatible_profiles.map(id=>catalog.find(p=>p.id===id)?.name||id).join(', '):'не подтвержден ни для одной встроенной модели'}{' '}(пользовательские модели БПЛА допускают любой профиль).</p>
     {Object.keys(s.parameters||{}).length>0&&<table className="data-table compact"><thead><tr><th>Параметр</th><th>Статус</th><th>Источник</th></tr></thead><tbody>
      {Object.entries(s.parameters!).map(([k,p])=><tr key={k}><td>{PARAM_NAMES[k]||k}</td><td><span className={`status-tag ${p.status}`}>{STATUS[p.status]}</span></td><td>{p.source}{p.formula?` · ${p.formula}`:''}</td></tr>)}</tbody></table>}
     <p className="muted">Источник: {s.source}{s.source_url&&<> · <a href={s.source_url} target="_blank" rel="noopener noreferrer">ссылка</a></>}{s.checked_at?` · проверено ${s.checked_at}`:''}</p>
     <div className="row actions">
      {s.origin==='user'?<button onClick={()=>{setForm({id:s.id,value:inputOf(s)});setError('');}}>Изменить (новая версия)</button>:<button onClick={()=>setForm({id:null,value:{...inputOf(s),name:`${s.name} · копия`,demonstration:s.demonstration}})}>Создать копию</button>}
      {s.origin==='user'&&<button onClick={()=>run(async()=>setVersions({id:s.id,list:await api.sensorVersions(s.id)}))}>Версии</button>}
      <a className="button" href={api.sensorExportUrl([s.id])}>Экспорт</a>
     </div>
     {versions?.id===s.id&&<ul className="muted">{versions.list.map(x=><li key={x.version}>v{x.version}: {x.name} · {x.source}</li>)}</ul>}
    </div>
   </details></li>)}</ul>
  <div className="row actions"><button onClick={()=>{setForm({id:null,value:empty()});setError('');}}>+ Новый профиль</button><button onClick={()=>file.current?.click()}>Импорт JSON</button><a className="button" href={api.sensorExportUrl()}>Экспорт всех</a></div>
  <input ref={file} hidden type="file" accept=".json,application/json" onChange={e=>{const f=e.target.files?.[0];e.target.value='';if(!f)return;void run(async()=>{if(f.size>2_000_000)throw new Error('Файл профилей больше 2 МБ.');let document:unknown;try{document=JSON.parse(await f.text());}catch{throw new Error('Файл не является JSON.');}await importFile(document,'reject');});}}/>
  <div aria-live="polite">{notice&&<p className="fleet-notice">{notice}</p>}{error&&<p className="error" role="alert">{error}</p>}</div>
  {pending&&<div className="note" role="group" aria-label="Конфликт идентификаторов при импорте">
   <p>Уже есть и отличаются: {pending.conflicts.map(c=>`${c.name} (${c.id}${c.builtin?', встроенный':`, v${c.existing_version}`})`).join('; ')}. Ничего не импортировано.</p>
   <div className="row actions">{!pending.conflicts.some(c=>c.builtin)&&<button onClick={()=>void importFile(pending.document,'new_version')}>Импортировать как новые версии</button>}
    <button onClick={()=>void importFile(pending.document,'rename')}>Импортировать под новыми ID</button><button onClick={()=>{setPending(null);setError('');}}>Отмена</button></div></div>}
  {form&&v&&<div className="profile-form" role="region" aria-label={form.id?`Новая версия профиля ${form.id}`:'Новый профиль сенсора'}>
   <strong>{form.id?`Новая версия профиля ${form.id}`:'Новый профиль сенсора'}</strong>
   <label>Название<input maxLength={120} value={v.name} onChange={e=>set({name:e.target.value})}/></label>
   <div className="row"><label>Вид съемки<select value={v.sensor_type} onChange={e=>set({sensor_type:e.target.value as Payload})}>{SURVEY_TYPES.map(t=><option key={t} value={t}>{TYPE_NAMES[t]}</option>)}</select></label>
    <label>Геометрия<select value={v.geometry} onChange={e=>set({geometry:e.target.value as SensorProfile['geometry']})}>{Object.entries(GEOMETRY).map(([k,n])=><option key={k} value={k}>{n}</option>)}</select></label></div>
   {v.geometry==='frame'&&<><div className="row">{num('Ширина матрицы, мм','sensor_width_mm')}{num('Высота матрицы, мм','sensor_height_mm')}</div>
    <div className="row">{num('Фокусное, мм','focal_length_mm')}{num('Ширина кадра, пикс','image_width_px',1)}{num('Высота кадра, пикс','image_height_px',1)}</div></>}
   {v.geometry==='scanner'&&num('Угол обзора, °','field_of_view_deg',.1)}
   {(v.geometry==='unknown'||v.geometry==='line')&&<p className="hint">Ширину полосы или шаг линий задает требование съемки.</p>}
   <fieldset className="zone-picks"><legend>Подтвержден для моделей</legend>{catalog.filter(p=>p.origin==='builtin').map(p=><label className="check-row" key={p.id}><input type="checkbox" checked={v.compatible_profiles.includes(p.id)} onChange={e=>set({compatible_profiles:e.target.checked?[...v.compatible_profiles,p.id]:v.compatible_profiles.filter(x=>x!==p.id)})}/> {p.name}</label>)}</fieldset>
   <label>Источник данных (обязательно)<textarea maxLength={1000} rows={2} value={v.source} onChange={e=>set({source:e.target.value})} placeholder="Документ производителя, страница, или «допущение оператора»"/></label>
   <div className="row"><label>Ссылка<input maxLength={500} value={v.source_url||''} onChange={e=>set({source_url:e.target.value})}/></label><label>Дата проверки<input type="date" value={v.checked_at||''} onChange={e=>set({checked_at:e.target.value||null})}/></label></div>
   <label className="check-row"><input type="checkbox" checked={Boolean(v.demonstration)} onChange={e=>set({demonstration:e.target.checked})}/> Демонстрационный — данные не подтверждены</label>
   <div className="row actions"><button className="primary" onClick={()=>void submit()}>{form.id?'Сохранить новую версию':'Создать'}</button><button onClick={()=>setForm(null)}>Отмена</button></div>
  </div>}
 </div>;
}
