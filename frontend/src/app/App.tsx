import { useEffect, useReducer, useRef, useState } from 'react';
import MapWorkspace from '../features/map/MapWorkspace';
import FleetPanel from '../features/fleet/FleetPanel';
import WeatherPanel from '../features/weather/WeatherPanel';
import Playback from '../features/plans/PlaybackControls';
import AircraftTag from '../features/plans/AircraftTag';
import PlanArchive from '../features/plans/PlanArchive';
import ScheduleChart from '../features/plans/ScheduleChart';
import { timeOfDay } from '../features/plans/schedule';
import DatabaseSettings from '../features/settings/DatabaseSettings';
import { emptyDraft, demoDraft, echelonDemo, fixedWingDemo } from '../features/scenarios/defaults';
import CameraSettings from '../features/scenarios/CameraSettings';
import { EchelonFields, echelonSummary } from '../features/scenarios/EchelonSettings';
import { surveyAreas, withSurveyAreas, MAX_SURVEY_AREAS } from '../features/scenarios/areas';
import { createHistory, historyReducer } from '../features/scenarios/history';
import ImportPanel from '../features/airspace/ImportPanel';
import { AirspaceFields, airspaceSummary, DeadlineFields, deadlineSummary, SortieFields, sortieSummary } from '../features/airspace/AirspaceSettings';
import TaskEstimatePanel from '../features/estimate/TaskEstimatePanel';
import FleetAnalysisView from '../features/analysis/FleetAnalysisView';
import Section from '../features/ui/Section';
import { bboxOf, bboxPolygon, contains, extentKm } from '../features/airspace/bbox';
import { api } from '../shared/api';
import { applyTheme, saveTheme, type ThemeMode } from './themeMode';
import { plots } from '../shared/plural';
import type { Polygon } from 'geojson';
import type { AircraftProfile, CameraProfile, Draft, FleetAnalysis, Mission, Plan, Scenario, ScenarioSummary, Site, SurveyImport, SurveySettings, TaskEstimate, WorkPackage } from '../shared/types';

const VERSION='1.1';
const minute=(seconds:number)=>`${(seconds/60).toFixed(1)} мин`;
const duration=(seconds:number)=>seconds>=5400?`${(seconds/3600).toFixed(1)} ч`:`${(seconds/60).toFixed(0)} мин`;
const objectiveName=(value:string)=>value==='min_completion_time'?'Быстрее завершить':'Меньше суммарный налет';
const missionKey=(m:Mission)=>`${m.aircraft_id}·${m.sortie??1}`;
// Analysis stays valid while only the aircraft cap changes: that is what its table is for.
const analysisKey=(draft:Draft)=>JSON.stringify({...draft,settings:{...draft.settings,max_aircraft:null}});
const MAX_DETAILED_AREAS=200,MAX_DETAILED_KM=30;
const chargersOf=(site:Site)=>site.chargers??(site.kind==='launch_landing'?2:0);
const alternateLabel=(mission:Mission,sites:Site[])=>{
 const alt=mission.alternate;
 if(!alt.checked) return {text:'не задан',cls:'muted'};
 if(!alt.reachable) return {text:'недостижим',cls:'error'};
 return {text:sites.find(s=>s.id===alt.site_id)?.name||alt.site_id||'',cls:'ok'};
};
/** [level, aircraft count] ascending; plans before 1.1 have no level per mission. */
function levelCounts(plan:Plan):[number,number][]{
 const levels=new Map<number,Set<string>>();
 for(const m of plan.missions){if(m.altitude_m===undefined)continue;levels.set(m.altitude_m,(levels.get(m.altitude_m)||new Set()).add(m.aircraft_id));}
 return [...levels].sort((a,b)=>a[0]-b[0]).map(([h,ids])=>[h,ids.size]);
}
function NumberField({label,value,min,max,step=1,onChange}:{label:string;value:number;min:number;max:number;step?:number;onChange:(value:number)=>void}){
 return <label>{label}<input type="number" value={value} min={min} max={max} step={step} onChange={e=>onChange(e.target.valueAsNumber||0)}/></label>;
}
const bboxArea=(area:Polygon)=>{const box=bboxOf([area])!;return (box[2]-box[0])*(box[3]-box[1]);};
type Tab='scenario'|'fleet'|'settings';

export default function App(){
 const [history,dispatchHistory]=useReducer(historyReducer<Draft>,undefined,()=>createHistory(emptyDraft()));
 const draft=history.present;
 const [profiles,setProfiles]=useState<AircraftProfile[]>([]);
 const [cameras,setCameras]=useState<CameraProfile[]>([]);
 const [saved,setSaved]=useState<ScenarioSummary[]>([]);
 const [status,setStatus]=useState<{text:string;ok:boolean|null}>({text:'Подключение к API…',ok:null});
 const [tab,setTab]=useState<Tab>('scenario');
 const [fleetSite,setFleetSite]=useState<string|null>(null);
 const [focusSite,setFocusSite]=useState<{id:string;revision:number}|null>(null);
 const [busy,setBusy]=useState(false);
 const [showDatabase,setShowDatabase]=useState(false);
 const [showArchive,setShowArchive]=useState(false);
 const [message,setMessage]=useState('');
 const [error,setError]=useState('');
 const [results,setResults]=useState<Plan[]>([]);
 const [selected,setSelected]=useState(0);
 const [fitVersion,setFitVersion]=useState(0);
 const [playhead,setPlayhead]=useState(0);
 const [task,setTask]=useState<Polygon[]|null>(null);
 const [estimate,setEstimate]=useState<TaskEstimate|null>(null);
 const [selectedPackage,setSelectedPackage]=useState<string|null>(null);
 const [analysis,setAnalysis]=useState<{result:FleetAnalysis;key:string}|null>(null);
 const [theme,setTheme]=useState<ThemeMode>(()=>document.documentElement.dataset.theme==='dark'?'dark':'light');
 const toggleTheme=()=>{const next:ThemeMode=theme==='dark'?'light':'dark';applyTheme(next);saveTheme(next);setTheme(next);};
 const upload=useRef<HTMLInputElement>(null);
 const plan=results[selected]||null;
 const areas=surveyAreas(draft);
 const catalog=Array.from(new Map([...profiles,...(draft.aircraft_profiles||[])].map(p=>[p.id,p])).values());
 const siteName=(id:string)=>draft.sites.find(s=>s.id===id)?.name||id;
 useEffect(()=>setPlayhead(0),[plan?.id]);
 useEffect(()=>{let active=true;Promise.all([api.health(),api.aircraft(),api.list(),api.cameras()]).then(([health,catalog,list,cameraCatalog])=>{if(active){setProfiles(catalog);setCameras(cameraCatalog);setSaved(list);setStatus({text:`API ${health.version}`,ok:true});}}).catch(()=>{if(active)setStatus({text:'API недоступен',ok:false});});return()=>{active=false;};},[]);
 const change=(next:Draft)=>{if(next.weather===draft.weather&&JSON.stringify(surveyAreas(next))!==JSON.stringify(surveyAreas(draft)))next={...next,weather:{...next.weather,observation:null}};if(JSON.stringify(next)===JSON.stringify(draft))return;dispatchHistory({type:'change',value:next});setResults([]);setSelected(0);setMessage('');setError('');};
 const navigateHistory=(type:'undo'|'redo')=>{if(busy||!(type==='undo'?history.past.length:history.future.length))return;dispatchHistory({type});setResults([]);setSelected(0);setPlayhead(0);setError('');setMessage(type==='undo'?'Изменение отменено.':'Изменение повторено.');};
 const settings=(key:keyof SurveySettings,value:number)=>change({...draft,settings:{...draft.settings,[key]:value}});
 const asScenario=():Scenario=>{if(!draft.survey_area)throw new Error('Нарисуйте область съемки на карте.');return {...draft,survey_area:draft.survey_area,aircraft_profiles:catalog.filter(p=>draft.fleet.some(a=>a.profile_id===p.id))};};
 const act=async(action:()=>Promise<void>)=>{setBusy(true);setMessage('');setError('');try{await action();}catch(e){setError(e instanceof Error?e.message:'Не удалось выполнить действие');}finally{setBusy(false);}};
 const save=()=>act(async()=>{const result=await api.save(asScenario());setSaved(await api.list());setMessage(`Сохранено: ${result.scenario.name}`);});
 const calculate=(compare=false,override:Partial<SurveySettings>={})=>act(async()=>{
  setResults([]);setSelected(0);
  const base=asScenario();const input={...base,settings:{...base.settings,...override}};const next:Plan[]=[];
  const objectives:Scenario['objective'][]=compare?['min_completion_time','min_total_airtime']:[input.objective];
  for(const objective of objectives){
   setMessage(`Расчет: ${objectiveName(objective)}…`);
   const snapshot=await api.save({...input,objective});
   next.push(await api.plan(snapshot.id));setResults([...next]);
  }
  setSaved(await api.list());setMessage('Расчет завершен. Ознакомьтесь с допущениями модели.');
 });
 const analyse=()=>act(async()=>{
  setMessage('Анализ парка: расчет планов для разного числа бортов…');
  const snapshot=await api.save(asScenario());
  setAnalysis({result:await api.fleetAnalysis(snapshot.id),key:analysisKey(draft)});
  setSaved(await api.list());setMessage('Анализ парка завершен.');
 });
 const planWith=(aircraft:number)=>{change({...draft,settings:{...draft.settings,max_aircraft:aircraft}});void calculate(false,{max_aircraft:aircraft});};
 const importSurvey=(result:SurveyImport)=>{
  const imported=result.areas.map(a=>a.geometry);
  const box=bboxOf(imported);
  const [width,height]=box?extentKm(box):[0,0];
  if(imported.length<=MAX_DETAILED_AREAS&&Math.max(width,height)<=MAX_DETAILED_KM){
   const next=withSurveyAreas(draft,imported);
   change(draft.allowed_airspace?next:{...next,allowed_airspace:bboxPolygon(bboxOf(imported,draft.sites.map(s=>s.coordinates),2)!)});
   setTask(null);setEstimate(null);setFitVersion(v=>v+1);
   setMessage(`Задание импортировано как ${imported.length} зон съемки${draft.allowed_airspace?'':'; разрешенное пространство — рамка района +2 км'}.`);
  }else{
   setTask(imported);setEstimate(null);setSelectedPackage(null);setFitVersion(v=>v+1);
   setMessage(`Задание крупнее одного детального расчета (${plots(imported.length)}, ${width.toFixed(0)}×${height.toFixed(0)} км): оцените его и выберите пакет работ.`);
  }
 };
 const takePackage=(pack:WorkPackage)=>{
  const parts=[...pack.areas].sort((a,b)=>bboxArea(b)-bboxArea(a)).slice(0,MAX_DETAILED_AREAS);
  const box=bboxOf(parts,[pack.site],2)!;
  const inside=draft.sites.filter(s=>contains(box,s.coordinates));
  let sites=draft.sites,fleet=draft.fleet;
  if(!inside.some(s=>s.kind==='launch_landing')){
   const id=`site-${pack.id}`;
   sites=[...draft.sites.filter(s=>s.id!==id),{id,name:`Площадка ${pack.id}`,kind:'launch_landing',coordinates:pack.site}];
   fleet=draft.fleet.map(a=>a.start_site_id&&inside.some(s=>s.id===a.start_site_id)?a:{...a,start_site_id:id});
  }
  change({...withSurveyAreas(draft,parts),name:`Пакет ${pack.id}`,allowed_airspace:bboxPolygon(box),sites,fleet});
  setFitVersion(v=>v+1);
  setMessage(`Пакет ${pack.id} взят в расчет: ${plots(parts.length)}. Площадка поставлена в центр пакета — проверьте ее на местности и при необходимости перенесите.`);
 };
 const load=(id:string)=>{if(!id)return;void act(async()=>{const stored=await api.load(id);change(stored.scenario);setFitVersion(v=>v+1);setMessage('Сохраненный сценарий открыт.');});};
 const loadPlan=async(id:string)=>{
  setBusy(true);
  try{
   const result=await api.loadPlan(id);
   const stored=await api.load(result.scenario_id);
   change(stored.scenario);setResults([result]);setPlayhead(0);setFitVersion(v=>v+1);
   setShowArchive(false);setMessage('Открыт сохраненный план с исходным сценарием. Повторный расчет не выполнялся.');
  }finally{setBusy(false);}
 };
 const importScenario=(file:File)=>act(async()=>{
  if(file.size>30_000_000)throw new Error('Максимальный размер сценария — 30 МБ.');
  const input=JSON.parse(await file.text());const stored=await api.save(input);
  change(stored.scenario);setSaved(await api.list());setFitVersion(v=>v+1);setMessage('Сценарий проверен сервером и импортирован.');
 });
 const downloadScenario=()=>{try{const blob=new Blob([JSON.stringify(asScenario(),null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='bas-scenario.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError((e as Error).message);}};
 const removeSite=(id:string)=>change({...draft,sites:draft.sites.filter(s=>s.id!==id),fleet:draft.fleet.map(a=>a.start_site_id===id?{...a,start_site_id:null}:a)});
 const updateSite=(id:string,patch:Partial<Site>)=>change({...draft,sites:draft.sites.map(s=>s.id===id?{...s,...patch}:s)});
 const openDemo=(next:Draft)=>{change(next);setTask(null);setEstimate(null);setFitVersion(v=>v+1);};
 const tabs:{id:Tab;label:string;meta:string}[]=[
  {id:'scenario',label:'Район',meta:areas.length?`${areas.length} зон`:task?'задание':'—'},
  {id:'fleet',label:'Парк',meta:`${draft.fleet.length} БПЛА`},
  {id:'settings',label:'Параметры',meta:draft.survey_type.toUpperCase()},
 ];
 const charging=plan&&(plan.metrics.charging_s||0)>0;
 return <div className="shell">
  {showDatabase&&<DatabaseSettings onClose={()=>setShowDatabase(false)}/>}
  {showArchive&&<PlanArchive onClose={()=>setShowArchive(false)} onOpen={loadPlan}/>}
  <header className="topbar">
   <div className="brand"><span className="brand-mark" aria-hidden="true">K</span><div><strong>KUMOsystems</strong><small>Mission Planner</small></div></div>
   <label className="operation-name"><span>Операция</span><input aria-label="Название операции" maxLength={120} value={draft.name} onChange={e=>change({...draft,name:e.target.value})}/></label>
   <div className="topbar-actions">
    <span className={`status-pill${status.ok===false?' bad':status.ok?' good':''}`}><i/>{status.text}</span>
    <button className="ghost" disabled={busy} onClick={()=>openDemo(demoDraft())} title="Два коптера, учебная геометрия">Пример: коптеры</button>
    <button className="ghost" disabled={busy} onClick={()=>openDemo(fixedWingDemo())} title="Геоскан 201 и Gemini с камерами">Пример: 201</button>
    <button className="ghost" disabled={busy} onClick={()=>openDemo(echelonDemo())} title="8 Gemini: по четыре с каждой площадки летят к дальнему району общим коридором — эшелоны разводят их по высоте">Пример: эшелоны</button>
    <button className="ghost" disabled={busy} onClick={()=>{setShowDatabase(false);setShowArchive(v=>!v);}}>Архив расчетов</button>
    <button className="ghost" disabled={busy} onClick={()=>{setShowArchive(false);setShowDatabase(v=>!v);}}>БД компании</button>
    <button className="ghost theme-toggle" onClick={toggleTheme} aria-pressed={theme==='dark'} aria-label={theme==='dark'?'Включить светлую тему':'Включить темную тему'} title={theme==='dark'?'Светлая тема':'Темная тема'}>{theme==='dark'?'☀':'☾'}</button>
   </div>
  </header>
  <div className="workbench">
   <aside className="sidebar">
    <nav className="steps" aria-label="Этапы планирования">{tabs.map((t,index)=><button key={t.id} className={tab===t.id?'active':''} aria-current={tab===t.id?'step':undefined} onClick={()=>setTab(t.id)}>
     <span className="step-number">{index+1}</span><span className="step-text"><b>{t.label}</b><small>{t.meta}</small></span></button>)}</nav>
    <div className="editor-scroll"><fieldset disabled={busy} className="editor">
    {tab==='scenario'&&<>
     <Section title="Данные заказчика · KML" meta={(draft.restriction_zones?.length||draft.obstacles?.length)?airspaceSummary(draft):'задание, зоны, препятствия'}>
      <ImportPanel draft={draft} task={task} disabled={busy} onChange={change} onSurvey={importSurvey}/>
     </Section>
     {task&&<Section title="Задание целиком" meta={plots(task.length)}>
      <TaskEstimatePanel areas={task} draft={draft} catalog={catalog} estimate={estimate} selected={selectedPackage} disabled={busy}
       onEstimate={value=>{setEstimate(value);setSelectedPackage(null);}} onSelect={setSelectedPackage} onTake={takePackage} onClose={()=>{setTask(null);setEstimate(null);setSelectedPackage(null);}}/>
     </Section>}
     <Section title="Зоны съемки" meta={`${areas.length} / ${MAX_SURVEY_AREAS}`}>
      {!areas.length&&<p className="empty">Нарисуйте зону инструментом «Зона съемки» над картой или импортируйте задание.</p>}
      {areas.length>12?<div className="geometry-row"><i className="swatch survey"/> {areas.length} зон съемки<button className="small-button" onClick={()=>change(withSurveyAreas(draft,[]))}>Убрать все</button></div>:
       areas.map((_,index)=><div className="geometry-row" key={index}><i className="swatch survey"/> Зона {index+1}<button className="small-button" onClick={()=>change(withSurveyAreas(draft,areas.filter((_,i)=>i!==index)))} aria-label={`Удалить зону съемки ${index+1}`}>Убрать</button></div>)}
      {areas.length>1&&<p className="hint">Пересечения зон учитываются один раз.</p>}
     </Section>
     <Section title="Пространство и запреты" meta={`${draft.allowed_airspace?'задано':'не задано'} · ${draft.no_fly_zones.length} запр.`}>
      <div className="geometry-row"><i className="swatch allowed"/> Разрешенное пространство <b>{draft.allowed_airspace?'задано':'не задано'}</b>{draft.allowed_airspace&&<button className="icon-button" onClick={()=>change({...draft,allowed_airspace:null})} aria-label="Удалить разрешенную зону">×</button>}</div>
      {draft.no_fly_zones.map((_,i)=><div className="geometry-row" key={i}><i className="swatch forbidden"/> Запретная зона {i+1}<button className="icon-button" onClick={()=>change({...draft,no_fly_zones:draft.no_fly_zones.filter((_,j)=>j!==i)})} aria-label={`Удалить запрет ${i+1}`}>×</button></div>)}
      {!draft.allowed_airspace&&<p className="hint">Нарисуйте рамку, в пределах которой разрешены подходы и возвращение.</p>}
     </Section>
     <Section title="Площадки" meta={`${draft.sites.length} · ЗУ ${draft.sites.reduce((n,s)=>n+chargersOf(s),0)}`}>
      {!draft.sites.length&&<p className="empty">Поставьте на карте точку «Старт / посадка».</p>}
      {draft.sites.map(site=><div className="site-card" key={site.id}>
       <div className="site-head"><i className={`swatch ${site.kind==='alternate'?'alternate':'site'}`}/><input aria-label="Название площадки" value={site.name} onChange={e=>updateSite(site.id,{name:e.target.value})}/><button className="icon-button" onClick={()=>removeSite(site.id)} aria-label={`Удалить ${site.name}`}>×</button></div>
       <div className="site-meta"><span>{site.kind==='alternate'?'Запасная':'Старт и посадка'} · {site.coordinates[1].toFixed(5)}, {site.coordinates[0].toFixed(5)}</span>
        <label className="inline-field">ЗУ<input type="number" min={0} max={50} value={chargersOf(site)} aria-label={`Зарядных мест на площадке ${site.name}`} onChange={e=>updateSite(site.id,{chargers:Math.max(0,Math.min(50,e.target.valueAsNumber||0))})}/></label></div>
       {site.kind==='launch_landing'&&<button className="site-roster" onClick={()=>{setFleetSite(site.id);setTab('fleet');}}><strong>Парк · {draft.fleet.filter(a=>a.start_site_id===site.id).length} БПЛА →</strong><span>{draft.fleet.filter(a=>a.start_site_id===site.id).map(a=>a.id).join(', ')||'Добавить и назначить борта'}</span></button>}
      </div>)}
      {draft.sites.length>0&&<p className="hint">ЗУ — число зарядных устройств; каждое заряжает одну АКБ за раз. Используется в режиме «Зарядка на площадке»; запасной площадке с ЗУ борт может садиться на зарядку.</p>}
     </Section>
     <Section title="Сценарий" meta="открыть · JSON" defaultOpen={false}>
      <label>Открыть сохраненный сценарий<select value="" onChange={e=>load(e.target.value)}><option value="">Выберите снимок…</option>{saved.map(s=><option key={s.id} value={s.id}>{s.name} · {new Date(s.created_at).toLocaleString('ru-RU')}</option>)}</select></label>
      <div className="row actions"><button onClick={()=>upload.current?.click()}>Импорт JSON</button><button onClick={downloadScenario}>Скачать JSON</button></div>
      <input ref={upload} hidden type="file" accept=".json,application/json" onChange={e=>{const file=e.target.files?.[0];if(file)void importScenario(file);e.target.value='';}}/>
     </Section>
    </>}
    {tab==='fleet'&&<FleetPanel draft={draft} catalog={catalog} cameras={cameras} initialSiteId={fleetSite} onChange={change} onCreated={profile=>setProfiles(p=>[...p,profile])} onLocate={id=>setFocusSite(previous=>({id,revision:(previous?.revision||0)+1}))}/>}
    {tab==='settings'&&<>
     <Section title="Съемка" meta={`${draft.survey_type.toUpperCase()} · ${draft.settings.altitude_m} м`}>
      <label>Тип съемки<select value={draft.survey_type} onChange={e=>{
       const survey_type=e.target.value as Scenario['survey_type'];
       const cameraCapable=survey_type==='rgb'||survey_type==='multispectral';
       change({...draft,survey_type,fleet:draft.fleet.map(a=>({...a,payload:survey_type,camera_id:null})),
        settings:{...draft.settings,coverage_mode:cameraCapable?draft.settings.coverage_mode:'manual'}});
      }}><option value="rgb">RGB · камера или ручная ширина</option><option value="multispectral">Мультиспектральная · Pollux или ручная ширина</option><option value="thermal">ИК · ручная ширина</option><option value="lidar">LiDAR · ручная ширина полосы</option><option value="geophysical">Геофизическая · ручной шаг линий</option></select></label>
      <p className="hint">Смена типа переназначает нагрузку всего парка и сбрасывает выбранные камеры.</p>
      <CameraSettings draft={draft} catalog={catalog} onChange={change}/>
     </Section>
     <Section title="Высоты и эшелоны" meta={echelonSummary(draft)}>
      <EchelonFields draft={draft} onChange={change}/>
     </Section>
     <Section title="Вылеты и зарядка" meta={sortieSummary(draft)}>
      <SortieFields draft={draft} onChange={change} catalog={catalog}/>
     </Section>
     <Section title="Сроки и ограничения" meta={deadlineSummary(draft)}>
      <DeadlineFields draft={draft} onChange={change} areas={areas}/>
     </Section>
     <Section title="Воздушное пространство" meta={airspaceSummary(draft)} defaultOpen={false}>
      <AirspaceFields draft={draft} onChange={change}/>
     </Section>
     <Section title="Погода" meta={`${draft.weather.wind_speed_mps} м/с · ${draft.weather.wind_from_deg}°`} defaultOpen={false}>
      <div className="row"><NumberField label="Ветер, м/с" value={draft.weather.wind_speed_mps} min={0} max={100} onChange={v=>change({...draft,weather:{...draft.weather,wind_speed_mps:v,observation:null}})}/><NumberField label="Откуда дует, °" value={draft.weather.wind_from_deg} min={0} max={359} onChange={v=>change({...draft,weather:{...draft.weather,wind_from_deg:v,observation:null}})}/></div>
      <p className="hint">{draft.weather.observation?`Источник: Open-Meteo, ${new Date(draft.weather.observation.valid_at).toLocaleString('ru-RU')}; порывы ${draft.weather.observation.wind_gusts_mps} м/с.`:'Источник: ручной ввод.'}</p>
      <WeatherPanel key={JSON.stringify(areas)} areas={areas} disabled={busy} onApply={observation=>change({...draft,weather:{wind_speed_mps:observation.wind_speed_mps,wind_from_deg:observation.wind_from_deg,observation}})}/>
     </Section>
     <Section title="Допущения и посадка" meta={`резерв ${draft.settings.reserve_percent}%`} defaultOpen={false}>
      <div className="row"><NumberField label="Резерв заряда, %" value={draft.settings.reserve_percent} min={15} max={60} onChange={v=>settings('reserve_percent',v)}/><NumberField label="Отступ от границ, м" value={draft.settings.boundary_clearance_m} min={5} max={100} onChange={v=>settings('boundary_clearance_m',v)}/></div>
      <NumberField label="Коэффициент паспортной длительности" value={draft.settings.endurance_factor} min={.3} max={.9} step={.05} onChange={v=>settings('endurance_factor',v)}/>
      <label>Посадка<select value={draft.settings.landing_policy??'return_to_start'} onChange={e=>change({...draft,settings:{...draft.settings,landing_policy:e.target.value as SurveySettings['landing_policy']}})}><option value="return_to_start">На площадку старта</option><option value="best_main_site">На любую основную площадку · ближайшую по времени</option></select></label>
      {draft.settings.landing_policy==='best_main_site'&&<p className="hint">Борт садится на основную площадку с кратчайшим возвращением. Для самолета — только если линия сноса парашюта в разрешенном пространстве. При повторных вылетах посадку определяет режим между вылетами.</p>}
      <p className="hint">Оценка для плоского участка. Резерв и длительность — допущения демонстрации, не эксплуатационные нормативы.</p>
     </Section>
    </>}
    </fieldset></div>
    <div className="run-panel">
     <div className="segmented" role="radiogroup" aria-label="Цель расчета">
      {(['min_completion_time','min_total_airtime'] as const).map(o=><button key={o} role="radio" aria-checked={draft.objective===o} disabled={busy} className={draft.objective===o?'active':''} onClick={()=>change({...draft,objective:o})}>{o==='min_completion_time'?'Быстрее завершить':'Меньше налет'}</button>)}
     </div>
     <button className="primary full" disabled={busy||!draft.survey_area} onClick={()=>calculate()}>{busy?'Выполняется…':'Рассчитать план'}</button>
     <div className="run-secondary"><button disabled={busy||!draft.survey_area} onClick={()=>calculate(true)}>Сравнить</button><button disabled={busy||!draft.survey_area||!draft.fleet.length} onClick={analyse} title="Сколько бортов нужно к сроку и сколько времени займет работа с разным числом бортов">Анализ парка</button><button disabled={busy||!draft.survey_area} onClick={save}>Сохранить</button></div>
    </div>
   </aside>
   <main className="center-column">
    <MapWorkspace draft={draft} plan={plan} playhead={playhead} onChange={change} fitVersion={fitVersion} focusSite={focusSite} disabled={busy} shortcutsDisabled={showArchive||showDatabase} canUndo={history.past.length>0} canRedo={history.future.length>0} onUndo={()=>navigateHistory('undo')} onRedo={()=>navigateHistory('redo')}
     task={task} packages={estimate?.packages||null} selectedPackage={selectedPackage} onPackage={setSelectedPackage} theme={theme}/>
    <div className={`feedback${error?' is-error':busy?' is-busy':''}`} aria-live="polite">{error?<span role="alert">{error}</span>:message||'Задайте район на карте, импортируйте задание заказчика или откройте пример.'}</div>
    {analysis&&<FleetAnalysisView analysis={analysis.result} stale={analysis.key!==analysisKey(draft)} disabled={busy} onPlan={planWith} onClose={()=>setAnalysis(null)}/>}
    {plan&&<section className="results">
     <div className="results-heading"><div><span className="eyebrow">Результат · демонстрационная модель</span><h2>{objectiveName(plan.objective)}</h2></div>
      {results.length>1&&<div className="segmented">{results.map((p,i)=><button key={p.id} className={i===selected?'active':''} onClick={()=>setSelected(i)}>{objectiveName(p.objective)}</button>)}</div>}
      <div className="results-actions"><a className="button" href={api.reportUrl(plan.id)} target="_blank" rel="noopener noreferrer">Отчет</a><a className="button" href={api.exportUrl(plan.id,'kml')}>KML</a><a className="button" href={api.exportUrl(plan.id,'geojson')}>GeoJSON</a></div></div>
     {plan.constraints&&(plan.constraints.time_limit_s!==null||plan.constraints.max_aircraft!==null)&&<div className={`constraint-banner ${plan.constraints.meets_time_limit===false?'bad':'good'}`}>
      {plan.constraints.time_limit_s!==null&&<strong>{plan.constraints.meets_time_limit?'Укладывается в срок':'Не укладывается в срок'}: {duration(plan.constraints.completion_s)} из {duration(plan.constraints.time_limit_s)}{plan.constraints.time_limit_source==='sunset'?' (до заката)':''}</strong>}
      {plan.constraints.max_aircraft!==null&&<span>Бортов: {plan.constraints.aircraft_used} из разрешенных {plan.constraints.max_aircraft}</span>}
      {plan.constraints.operation_end&&<span>Окончание работ: {new Date(plan.constraints.operation_end).toLocaleString('ru-RU')}</span>}</div>}
     <div className="metrics">
      <div><small>Завершение операции</small><strong>{duration(plan.metrics.completion_time_s)}</strong></div>
      <div><small>Суммарный налет</small><strong>{duration(plan.metrics.total_airtime_s)}</strong></div>
      <div><small>Бортов · вылетов</small><strong>{plan.metrics.aircraft_used} · {plan.metrics.sortie_count??plan.missions.length}</strong></div>
      <div><small>Площадь · покрытие</small><strong>{(plan.metrics.area_m2/10000).toFixed(1)} га · {plan.metrics.coverage_percent.toFixed(0)}%</strong></div>
      {charging&&<div className="metric-accent"><small>Зарядка · ожидание ЗУ</small><strong>{duration(plan.metrics.charging_s||0)} · {duration(plan.metrics.charger_wait_s||0)}</strong></div>}
     </div>
     {(plan.separation?.vertical_m||levelCounts(plan).length>1)&&<div className="chips" aria-label="Высоты и разведение">
      <span className="chip">Высоты: {levelCounts(plan).map(([h,n])=>`${h} м · ${n}`).join(', ')}</span>
      {plan.separation?.vertical_m&&<span className="chip">Разведение: {plan.separation.horizontal_m} м по горизонтали или {plan.separation.vertical_m} м по высоте</span>}
      {plan.echelons&&plan.echelons.shared_same_level_before_m>=1&&<span className={`chip${plan.echelons.applied?'':' warn'}`}>Общие участки маршрутов на одной высоте: {(plan.echelons.shared_same_level_before_m/1000).toFixed(1)}{plan.echelons.applied?` → ${(plan.echelons.shared_same_level_after_m/1000).toFixed(1)}`:''} км</span>}
     </div>}
     {plan.airspace&&((draft.restriction_zones?.length||0)+(draft.obstacles?.length||0)>0||plan.airspace.excluded_area_m2>0)&&<div className="chips">
      <span className="chip">Зоны: облетается <b>{plan.airspace.zones_applied.length}</b> · выше полета {plan.airspace.zones_above.length} · неактивны {plan.airspace.zones_inactive.length}</span>
      <span className="chip">Препятствия: облетается <b>{plan.airspace.obstacles_applied.length}</b> · ниже полета {plan.airspace.obstacles_below}</span>
      {plan.airspace.excluded_area_m2>0&&<span className="chip warn">Исключено из съемки {(plan.airspace.excluded_area_m2/10000).toFixed(2)} га</span>}</div>}
     <ScheduleChart plan={plan} time={playhead} onTime={setPlayhead} siteName={siteName}/>
     <Playback key={plan.id} plan={plan} time={playhead} onTime={setPlayhead}/>
     <h3 className="block-title">Вылеты <span>{(plan.metrics.area_m2/10000).toFixed(1)} га · расчет {plan.metrics.calculation_time_s.toFixed(2)} с</span></h3>
     <div className="table-scroll"><table className="data-table" aria-label="Вылеты"><thead><tr><th>Борт</th><th>Вылет</th><th>Старт → посадка</th><th>Высота</th><th>Взлет</th><th>Посадка</th><th>Полет</th><th>Путь</th><th>Перед вылетом</th><th>Резерв</th><th/></tr></thead>
      <tbody>{[...plan.missions].sort((a,b)=>a.aircraft_id.localeCompare(b.aircraft_id,'ru',{numeric:true})||(a.sortie??1)-(b.sortie??1)).map(m=>{const alt=alternateLabel(m,draft.sites);return <tr key={missionKey(m)}>
       <td><AircraftTag mission={m}/></td><td>{m.sortie??1}</td><td>{siteName(m.site_id)}{(m.landing_site_id||m.site_id)!==m.site_id?` → ${siteName(m.landing_site_id!)}`:''}</td>
       <td className="nowrap">{m.altitude_m??draft.settings.altitude_m} м</td>
       <td className="nowrap">{timeOfDay(m.start_s,plan.constraints?.operation_start)}</td><td className="nowrap">{timeOfDay(m.finish_s,plan.constraints?.operation_start)}</td><td>{minute(m.flight_time_s)} <small className="muted">/ {minute(m.budget_s)}</small></td><td>{(m.distance_m/1000).toFixed(2)} км</td>
       <td>{m.charge_s!==undefined?<span className="ground-charge">зарядка {minute(m.charge_s)}{(m.charge_wait_s||0)>1?`, очередь ${minute(m.charge_wait_s!)}`:''}</span>:m.ground_s?`замена АКБ ${minute(m.ground_s)}`:'—'}</td>
       <td className={alt.cls}>{alt.text}</td>
       <td className="nowrap"><a href={api.exportUrl(plan.id,'kml',m.aircraft_id)} title={`KML борта ${m.aircraft_id}`}>KML</a></td></tr>;})}</tbody></table></div>
     {plan.metrics.coverage_by_area&&plan.metrics.coverage_by_area.length>1&&<details className="subtle"><summary>Покрытие по зонам · {plan.metrics.coverage_by_area.length}</summary><div className="table-scroll"><table className="data-table"><thead><tr><th>Зона</th><th>Площадь</th><th>Покрытие</th>{plan.metrics.coverage_by_area.some(a=>a.excluded_m2)&&<th>Исключено</th>}</tr></thead><tbody>{plan.metrics.coverage_by_area.map(a=><tr key={a.index}><td>Зона {a.index}</td><td>{(a.area_m2/10000).toFixed(1)} га</td><td>{a.coverage_percent.toFixed(1)}%</td>{plan.metrics.coverage_by_area!.some(z=>z.excluded_m2)&&<td>{((a.excluded_m2||0)/10000).toFixed(2)} га</td>}</tr>)}</tbody></table></div></details>}
     {results.length>1&&<div className="table-scroll"><table className="data-table"><thead><tr><th>Критерий</th><th>Завершение</th><th>Налет</th><th>Бортов</th></tr></thead><tbody>{results.map(p=><tr key={p.id}><td>{objectiveName(p.objective)}</td><td>{duration(p.metrics.completion_time_s)}</td><td>{duration(p.metrics.total_airtime_s)}</td><td>{p.metrics.aircraft_used}</td></tr>)}</tbody></table></div>}
     {plan.missions.some(m=>m.survey?.mode==='camera')&&<details className="subtle" open><summary>Камеры и снимки</summary><div className="table-scroll"><table className="data-table" aria-label="Результаты расчета камер"><thead><tr><th>Борт / камера</th><th>Вылет</th><th>GSD</th><th>Захват</th><th>Снимки</th><th>Мин. интервал</th></tr></thead><tbody>{plan.missions.map(m=><tr key={missionKey(m)}><td>{m.aircraft_id}<br/><small className="muted">{m.survey?.camera?.name||'Ручной режим'}</small></td><td>{m.sortie??1}</td><td>{m.survey?.gsd_cm?.toFixed(2)} см/пикс</td><td>{m.survey?.swath_width_m.toFixed(1)} м{(m.survey?.crab_deg??0)>=.5?<small className="muted"> · снос {m.survey!.crab_deg!.toFixed(0)}°</small>:null}</td><td>{m.survey?.photo_count}</td><td>{m.survey?.min_photo_interval_s?.toFixed(2)} с</td></tr>)}</tbody></table></div><p className="hint">Номинальные значения для надира и плоской поверхности; интервал — требуемый.</p></details>}
     {plan.metrics.maintenance&&plan.metrics.maintenance.length>0&&<details className="subtle"><summary>Расход ресурса до ТО · {((plan.metrics.maintenance_share_total||0)*100).toFixed(2)}% интервала</summary><div className="table-scroll"><table className="data-table" aria-label="Расход ресурса до ТО"><thead><tr><th>Борт</th><th>Вылетов</th><th>Налет</th><th>Норматив ТО</th><th>Доля интервала</th></tr></thead>
      <tbody>{plan.metrics.maintenance.map(m=><tr key={m.aircraft_id}><td>{m.aircraft_id}</td><td>{m.sorties}</td><td>{minute(m.flight_time_s)}</td><td>{m.interval?`${m.interval} ${m.unit==='flights'?'полетов':m.unit==='motor_hours'?'моточасов':'ч налета'}`:'нет данных'}</td><td>{m.interval_share!==null?`${(m.interval_share*100).toFixed(2)}%`:'—'}</td></tr>)}</tbody></table></div>
      <p className="hint">201 — каждые 80 полетов, 801 — каждые 160 ч; для Gemini норматив принят по аналогии с 801.</p></details>}
     <details className="limitations"><summary>Допущения и ограничения модели · {plan.warnings.length}</summary><ul>{plan.warnings.map(w=><li key={w}>{w}</li>)}</ul><p>Эвристика выбирает из двух направлений галсов, для самолета в ветре — еще вдоль и поперек ветра. Глобальная оптимальность не доказана.</p></details>
    </section>}
   </main>
  </div>
  <footer><span>KUMOsystems {VERSION} · предварительное планирование, не задание для автопилота</span><span>{draft.fleet.length} БВС · {draft.sites.length} площадок · {draft.no_fly_zones.length} запретов · {draft.restriction_zones?.length||0} зон · {draft.obstacles?.length||0} препятствий</span></footer>
 </div>;
}
