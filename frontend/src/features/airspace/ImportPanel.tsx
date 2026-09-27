import { useRef, useState } from 'react';
import type { Polygon } from 'geojson';
import { api } from '../../shared/api';
import type { Draft, ObstaclesImport, SurveyImport, ZonesImport } from '../../shared/types';
import { surveyAreas } from '../scenarios/areas';
import { bboxOf, type BBox } from './bbox';
import { CATEGORY_NAMES } from '../map/layerInfo';

type Kind='survey'|'zones'|'obstacles';
const titles:Record<Kind,string>={survey:'Задание на съемку',zones:'Зоны ограничений',obstacles:'Препятствия'};
// Zones and obstacles are cut to the work area plus this margin: routes never leave the allowed airspace.
const MARGIN_KM=10;

interface Props {draft:Draft;task:Polygon[]|null;disabled:boolean;onChange:(draft:Draft)=>void;onSurvey:(result:SurveyImport)=>void}

export default function ImportPanel({draft,task,disabled,onChange,onSurvey}:Props){
 const inputs={survey:useRef<HTMLInputElement>(null),zones:useRef<HTMLInputElement>(null),obstacles:useRef<HTMLInputElement>(null)};
 const [busy,setBusy]=useState<Kind|null>(null);
 const [report,setReport]=useState<{kind:Kind;lines:string[];warnings:string[]}|null>(null);
 const [error,setError]=useState('');
 const zones=draft.restriction_zones||[],obstacles=draft.obstacles||[];
 const download=async()=>{
  setError('');
  try{
   const url=URL.createObjectURL(await api.exportZones(zones));
   const link=document.createElement('a');link.href=url;link.download='kumo-zones.kml';link.click();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){setError(e instanceof Error?e.message:'Не удалось выгрузить зоны');}
 };
 const workArea=():BBox|null=>bboxOf([...surveyAreas(draft),...(draft.allowed_airspace?[draft.allowed_airspace]:[]),...(task||[])],draft.sites.map(s=>s.coordinates),MARGIN_KM);
 const load=async(kind:Kind,file:File)=>{
  setBusy(kind);setError('');setReport(null);
  try{
   if(file.size>25_000_000)throw new Error('Файл больше 25 МБ.');
   const bbox=kind==='survey'?null:workArea();
   if(kind==='survey'){
    const result=await api.importKml<SurveyImport>('survey',file);
    const s=result.stats as {polygons_read:number;repaired:number;dropped_degenerate:number;collapsed_in_repair:number;imported:number;area_ha:number;extent_km:[number,number]};
    setReport({kind,warnings:[],lines:[`Полигонов прочитано: ${s.polygons_read}, исправлено: ${s.repaired}, отброшено вырожденных: ${s.dropped_degenerate+s.collapsed_in_repair}.`,
     `Импортировано участков: ${s.imported}, площадь ${s.area_ha.toFixed(1)} га, охват ${s.extent_km[0]}×${s.extent_km[1]} км.`]});
    onSurvey(result);
   }else if(kind==='zones'){
    const result=await api.importKml<ZonesImport>('zones',file,bbox);
    onChange({...draft,restriction_zones:result.zones});
    const counts=Object.entries(result.zones.reduce<Record<string,number>>((acc,z)=>({...acc,[z.category]:(acc[z.category]||0)+1}),{})).map(([k,v])=>`${CATEGORY_NAMES[k]||k}: ${v}`).join(', ');
    setReport({kind,warnings:result.warnings,lines:[`Зон импортировано: ${result.zones.length}${bbox?` (в пределах района +${MARGIN_KM} км; за его пределами: ${result.stats.outside_area})`:''}.`,counts]});
   }else{
    const result=await api.importKml<ObstaclesImport>('obstacles',file,bbox);
    onChange({...draft,obstacles:result.obstacles});
    const refs=result.stats.height_reference as {AGL:number;AMSL:number};
    setReport({kind,warnings:result.warnings,lines:[`Препятствий импортировано: ${result.obstacles.length}${bbox?` (в пределах района +${MARGIN_KM} км)`:''}; без высоты пропущено: ${result.stats.dropped_without_height}.`,
     `Высота над землей: ${refs.AGL}, над уровнем моря: ${refs.AMSL}.`]});
   }
  }catch(e){setError(e instanceof Error?e.message:'Импорт не выполнен');}
  finally{setBusy(null);}
 };
 return <section className="import-panel" aria-label="Импорт данных заказчика">
  <div className="import-buttons">{(Object.keys(titles) as Kind[]).map(kind=><button key={kind} disabled={disabled||busy!==null} onClick={()=>inputs[kind].current?.click()}>{busy===kind?'Импорт…':titles[kind]}</button>)}</div>
  {(Object.keys(titles) as Kind[]).map(kind=><input key={kind} ref={inputs[kind]} hidden type="file" accept=".kml,application/vnd.google-earth.kml+xml" onChange={e=>{const file=e.target.files?.[0];if(file)void load(kind,file);e.target.value='';}}/>)}
  {!surveyAreas(draft).length&&!task&&<p className="muted">Начните с задания на съемку: зоны и препятствия затем отбираются по его району.</p>}
  {error&&<p role="alert" className="error">{error}</p>}
  {report&&<div className="import-report"><strong>{titles[report.kind]}</strong>{report.lines.filter(Boolean).map(l=><p key={l}>{l}</p>)}
   {report.warnings.length>0&&<details><summary>Замечания · {report.warnings.length}</summary><ul>{report.warnings.map(w=><li key={w}>{w}</li>)}</ul></details>}</div>}
  {(zones.length>0||obstacles.length>0)&&<div className="layer-summary">
   {zones.length>0&&<div className="geometry-row"><span className="amber">●</span> Зоны ограничений <b>{zones.length}</b><button onClick={()=>void download()} title="Скачать зоны в формате KUMO (KML с высотами и временем действия)">KML</button><button onClick={()=>onChange({...draft,restriction_zones:[]})} aria-label="Удалить зоны ограничений">×</button></div>}
   {obstacles.length>0&&<div className="geometry-row"><span className="pink">▲</span> Препятствия <b>{obstacles.length}</b><button onClick={()=>onChange({...draft,obstacles:[]})} aria-label="Удалить препятствия">×</button></div>}
   <p className="muted">Учитываются только зоны и препятствия, достающие до высоты полета с запасом («Параметры» → «Воздушное пространство»). Щелчок по объекту на карте показывает его параметры.</p>
  </div>}
 </section>;
}
