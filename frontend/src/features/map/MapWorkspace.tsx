import { useEffect, useRef, useState } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import Feature from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import Draw from 'ol/interaction/Draw';
import Modify from 'ol/interaction/Modify';
import { fromLonLat } from 'ol/proj';
import { defaults, ScaleLine } from 'ol/control';
import { Fill, Stroke, Style, Circle, Text, Icon, RegularShape } from 'ol/style';
import type { FeatureLike } from 'ol/Feature';
import type { Polygon, Point } from 'geojson';
import type { Draft, Plan, Position, RestrictionZone, WorkPackage } from '../../shared/types';
import { zoneInfo, obstacleInfo, ZONE_COLORS, type MapInfo } from './layerInfo';
import { bboxOf, bboxPolygon } from '../airspace/bbox';
import { BRAND, MAP_COLORS } from '../../app/theme';
import type { ThemeMode } from '../../app/themeMode';

// Labels: dark text on a white halo over the light map, the reverse over the darkened one.
const LABELS:Record<ThemeMode,{text:string;halo:string}>={light:{text:BRAND.charcoal,halo:'#ffffff'},dark:{text:'#E6E9EC',halo:'#121417'}};
import { createSource, createUserSource, hasMapTilerKey } from './providers';
import { unByKey } from 'ol/Observable';
import type { EventsKey } from 'ol/events';
import UserMaps from './UserMaps';
import { deserializeMaps, serializeMaps, MAP_STORAGE_KEY, type UserMap } from './mapConfig';
import { currentSorties, sampleMission } from '../plans/playback';
import { missionHeading } from '../plans/heading';
import { aircraftIcon } from './aircraftIcons';
import { surveyAreas, MAX_SURVEY_AREAS } from '../scenarios/areas';
import { areaIds, isMulti, replaceAreas, requirementsOf, TYPE_NAMES } from '../scenarios/requirements';

export interface MapFocus {id:string|null;revision:number;kind?:'site'|'area'|'zone'|'obstacle'}
import { isEditingTarget } from '../scenarios/history';

type Tool='edit'|'survey'|'allowed'|'forbidden'|'launch_landing'|'alternate';
const colors=MAP_COLORS;
const names:Record<Tool,string>={edit:'Правка',survey:'Зона съемки',allowed:'Разрешенная зона',forbidden:'Запретная зона',launch_landing:'Старт / посадка',alternate:'Запасная площадка'};
const format=new GeoJSON();
const projection={featureProjection:'EPSG:3857',dataProjection:'EPSG:4326'};
interface Props {draft:Draft;plan:Plan|null;playhead:number;onChange:(draft:Draft)=>void;fitVersion:number;focusSite:MapFocus|null;disabled:boolean;shortcutsDisabled:boolean;canUndo:boolean;canRedo:boolean;onUndo:()=>void;onRedo:()=>void;
 task:Polygon[]|null;packages:WorkPackage[]|null;selectedPackage:string|null;onPackage:(id:string)=>void;theme:ThemeMode}

export default function MapWorkspace({draft,plan,playhead,onChange,fitVersion,focusSite,disabled,shortcutsDisabled,canUndo,canRedo,onUndo,onRedo,task,packages,selectedPackage,onPackage,theme}:Props){
 const labels=useRef(LABELS[theme]);
 const target=useRef<HTMLDivElement>(null);
 const [basemap,setBasemap]=useState('osm');
 const [userMaps,setUserMaps]=useState<UserMap[]>(()=>{try{return deserializeMaps(localStorage.getItem(MAP_STORAGE_KEY));}catch{return [];}});
 const [showMaps,setShowMaps]=useState(false);
 const [mapMessage,setMapMessage]=useState('');
 const [storageError,setStorageError]=useState('');
 const [mapRevision,setMapRevision]=useState(0);
 const [tool,setTool]=useState<Tool>('edit');
 const [vertexCount,setVertexCount]=useState(0);
 const drawing=useRef<Draw|null>(null);
 const sketchActive=useRef(false);
 const [modifying,setModifying]=useState(false);
 const [error,setError]=useState('');
 const [info,setInfo]=useState<MapInfo|null>(null);
 const context=useRef({draft,onChange,disabled,tool,onPackage});context.current={draft,onChange,disabled,tool,onPackage};
 const handle=useRef<{map:Map;source:VectorSource;routes:VectorSource;vehicles:VectorSource;zones:VectorSource;obstacles:VectorSource;task:VectorSource;packages:VectorSource;base:TileLayer;modify:Modify;layers:VectorLayer[]}|null>(null);
 useEffect(()=>{
  const source=new VectorSource({wrapX:false}),routes=new VectorSource({wrapX:false}),vehicles=new VectorSource({wrapX:false});
  const zones=new VectorSource({wrapX:false}),obstacles=new VectorSource({wrapX:false}),task=new VectorSource({wrapX:false}),packages=new VectorSource({wrapX:false});
  const cache=new globalThis.Map<string,Style>();
  const cached=(key:string,make:()=>Style)=>{let style=cache.get(key);if(!style){style=make();cache.set(key,style);}return style;};
  // Zones: colour by category; after a plan, the ones it avoided are solid, the rest faint and dashed.
  const zoneLayer=new VectorLayer({source:zones,style:(feature:FeatureLike)=>{
   const category=feature.get('category'),state=feature.get('state'),color=ZONE_COLORS[category]||ZONE_COLORS.other;
   return cached(`z:${category}:${state}`,()=>new Style({stroke:new Stroke({color:state==='ignored'?color+'88':color,width:state==='applied'?2.5:1.5,lineDash:state==='ignored'?[6,6]:undefined}),
    fill:new Fill({color:color+(state==='applied'?'38':state==='ignored'?'0d':'22')})}));
  }});
  const obstacleLayer=new VectorLayer({source:obstacles,zIndex:5,style:(feature:FeatureLike)=>{
   const state=feature.get('state'),color=state==='applied'?'#D92D20':state==='ignored'?'#A7AFB8':'#475467',type=feature.getGeometry()?.getType();
   return cached(`o:${state}:${type}`,()=>type==='Point'?new Style({image:new RegularShape({points:3,radius:state==='ignored'?4:6,fill:new Fill({color}),stroke:new Stroke({color:'#ffffff',width:1.2})})})
    :new Style({stroke:new Stroke({color,width:2}),fill:new Fill({color:color+'33'})}));
  }});
  const taskLayer=new VectorLayer({source:task,style:cached('task',()=>new Style({fill:new Fill({color:MAP_COLORS.task+'33'}),stroke:new Stroke({color:MAP_COLORS.task+'aa',width:1})}))});
  const packageLayer=new VectorLayer({source:packages,style:(feature:FeatureLike,resolution:number)=>{
   const selected=feature.get('selected'),reachable=feature.get('reachable');
   const style=cached(`p:${labels.current.text}:${selected}:${reachable}`,()=>new Style({stroke:new Stroke({color:selected?BRAND.orange:reachable?MAP_COLORS.package+'aa':MAP_COLORS.forbidden,width:selected?3:1.2,lineDash:selected?undefined:[4,4]}),
    fill:new Fill({color:selected?BRAND.orange+'1f':'#00000000'}),text:new Text({font:'600 11px Segoe UI',fill:new Fill({color:labels.current.text}),stroke:new Stroke({color:labels.current.halo,width:4}),overflow:true})}));
   style.getText()!.setText(resolution<120||selected?feature.get('label'):'');
   return style;
  }});
  // Own class and canvas, so the dark theme can darken the basemap without touching the vector layers.
  const base=new TileLayer({source:createSource('osm'),className:'basemap-layer'});
  const editable=new VectorLayer({source,style:feature=>{
   const kind=feature.get('kind'), color=colors[kind]||BRAND.charcoal;
   const reserve=kind==='alternate',site=kind==='launch_landing'||reserve;
   // Sites: a charcoal disc ringed in orange, reserve sites hollow and dashed; labels on a white halo.
   return new Style({stroke:new Stroke({color,width:kind==='allowed'?2:kind==='survey'?2.5:2,lineDash:kind==='allowed'?[8,6]:undefined}),fill:new Fill({color:color+(kind==='allowed'?'0a':kind==='survey'?'2e':'22')}),
    image:site?new Circle({radius:reserve?7:8,fill:new Fill({color:reserve?'#ffffff':BRAND.charcoal}),stroke:new Stroke({color:reserve?color:BRAND.orange,width:3,lineDash:reserve?[3,2]:undefined})}):undefined,
    text:new Text({text:feature.get('label')||'',offsetY:site?-19:0,font:'600 12px Segoe UI',fill:new Fill({color:labels.current.text}),stroke:new Stroke({color:labels.current.halo,width:4}),overflow:!site})});
  }});
  const routeLayer=new VectorLayer({source:routes,style:feature=>new Style({stroke:new Stroke({color:feature.get('color'),width:feature.get('phase')==='survey'?3:2,lineDash:feature.get('phase')==='survey'?undefined:[5,7]})})});
  const vehicleStyles=new globalThis.Map<string,Style>();
  const vehicleLayer=new VectorLayer({source:vehicles,zIndex:10,style:feature=>{
   const profile=feature.get('profileId'),color=feature.get('color'),key=`${profile}:${color}`;
   let style=vehicleStyles.get(key);
   if(!style){
    style=new Style({image:new Icon({src:aircraftIcon(profile,color),rotateWithView:true}),
     text:new Text({offsetY:40,font:'600 12px Segoe UI',fill:new Fill({color:'#fff'}),backgroundFill:new Fill({color:BRAND.charcoal+'e6'}),padding:[3,7,3,7]})});
    vehicleStyles.set(key,style);
   }
   // The artwork is drawn nose-up, so the map heading is the rotation.
   style.getImage()!.setRotation(feature.get('heading'));
   style.getText()!.setText(feature.get('label'));
   return style;
  }});
  const map=new Map({target:target.current!,layers:[base,taskLayer,zoneLayer,packageLayer,editable,obstacleLayer,routeLayer,vehicleLayer],view:new View({center:fromLonLat([37.30,55.8]),zoom:10,multiWorld:false}),controls:defaults({attributionOptions:{collapsible:false}}).extend([new ScaleLine()])});
  const modify=new Modify({source});map.addInteraction(modify);
  modify.on('modifystart',()=>setModifying(true));
  modify.on('modifyend',()=>{
   setModifying(false);
   const {draft:current,onChange:change,disabled:blocked}=context.current;if(blocked)return;
   const next={...current,no_fly_zones:[...current.no_fly_zones],sites:current.sites.map(s=>({...s}))};
   const areas=surveyAreas(current);
   source.getFeatures().forEach(f=>{
    const geometry=format.writeGeometryObject(f.getGeometry()!,projection);
    const kind=f.get('kind');
    if(kind==='survey')areas[f.get('index')]=geometry as Polygon;
    else if(kind==='allowed')next.allowed_airspace=geometry as Polygon;
    else if(kind==='forbidden')next.no_fly_zones[f.get('index')]=geometry as Polygon;
    else {const site=next.sites.find(s=>s.id===f.get('siteId'));if(site)site.coordinates=(geometry as Point).coordinates as Position;}
   });change(replaceAreas(next,areas));
  });
  map.on('singleclick',event=>{
   if(context.current.tool!=='edit')return;
   const hit=map.forEachFeatureAtPixel(event.pixel,feature=>feature.get('info')||feature.get('packageId')?feature:undefined,{layerFilter:layer=>layer===zoneLayer||layer===obstacleLayer||layer===packageLayer,hitTolerance:4});
   if(!hit){setInfo(null);return;}
   if(hit.get('packageId')){context.current.onPackage(hit.get('packageId'));setInfo(null);return;}
   setInfo(hit.get('info'));
  });
  handle.current={map,source,routes,vehicles,zones,obstacles,task,packages,base,modify,layers:[taskLayer,zoneLayer,packageLayer,editable,obstacleLayer,routeLayer,vehicleLayer]};
  return()=>{handle.current=null;map.setTarget(undefined);map.dispose();};
 },[]);
 useEffect(()=>{
  const state=handle.current;if(!state)return;
  state.source.clear();
  const add=(geometry:Polygon|Point,props:Record<string,unknown>)=>{const feature=new Feature(format.readGeometry(geometry,projection));feature.setProperties(props);state.source.addFeature(feature);};
  if(draft.allowed_airspace)add(draft.allowed_airspace,{kind:'allowed'});
  // With requirements a zone shows its id and survey types: «field · RGB, мультиспектральная».
  const ids=areaIds(draft),multi=isMulti(draft);
  surveyAreas(draft).forEach((area,index)=>{const types=[...new Set(requirementsOf(draft,ids[index]).map(r=>TYPE_NAMES[r.survey_type]))];
   add(area,{kind:'survey',index,areaId:ids[index],label:multi?`${ids[index]} · ${types.join(', ')||'нет требований'}`:`Зона ${index+1}`});});
  draft.no_fly_zones.forEach((zone,index)=>add(zone,{kind:'forbidden',index}));
  // With charging on, every site shows its chargers: reserve sites with chargers become charging stops.
  const charging=draft.settings.recharge_mode==='charge'&&(draft.settings.sorties_per_aircraft??1)>1;
  draft.sites.forEach(site=>{
   const chargers=site.chargers??(site.kind==='launch_landing'?2:0);
   const parts=[site.name,site.kind==='launch_landing'?`${draft.fleet.filter(a=>a.start_site_id===site.id).length} БПЛА`:'',charging&&chargers?`ЗУ ${chargers}`:''].filter(Boolean);
   add({type:'Point',coordinates:site.coordinates},{kind:site.kind,siteId:site.id,label:parts.join(' · ')});
  });
 },[draft]);
 useEffect(()=>{
  const state=handle.current;if(!state)return;
  const decided=plan?.airspace;
  const applied=new Set(decided?.zones_applied.map(z=>z.id)||[]),hit=new Set(decided?.obstacles_applied.map(o=>o.id)||[]);
  const zoneFeatures=(draft.restriction_zones||[]).map((zone:RestrictionZone)=>{
   const feature=new Feature(format.readGeometry(zone.geometry,projection));
   const state=!decided?'unknown':applied.has(zone.id)?'applied':'ignored';
   feature.setProperties({category:zone.category,state,info:zoneInfo(zone,decided),zoneId:zone.id});return feature;
  });
  state.zones.clear();state.zones.addFeatures(zoneFeatures);
  const obstacleFeatures=(draft.obstacles||[]).map(obstacle=>{
   const feature=new Feature(format.readGeometry(obstacle.geometry,projection));
   feature.setProperties({state:!decided?'unknown':hit.has(obstacle.id)?'applied':'ignored',info:obstacleInfo(obstacle,decided),obstacleId:obstacle.id});return feature;
  });
  state.obstacles.clear();state.obstacles.addFeatures(obstacleFeatures);
  setInfo(null);
 },[draft.restriction_zones,draft.obstacles,plan]);
 useEffect(()=>{
  labels.current=LABELS[theme];
  handle.current?.layers.forEach(layer=>layer.changed());
 },[theme]);
 useEffect(()=>{
  const state=handle.current;if(!state)return;
  state.task.clear();
  state.task.addFeatures((task||[]).map(area=>new Feature(format.readGeometry(area,projection))));
 },[task]);
 useEffect(()=>{
  const state=handle.current;if(!state)return;
  state.packages.clear();
  state.packages.addFeatures((packages||[]).map(pack=>{
   const box=bboxOf(pack.areas)!;
   const feature=new Feature(format.readGeometry(bboxPolygon(box),projection));
   feature.setProperties({packageId:pack.id,selected:pack.id===selectedPackage,reachable:pack.reachable,label:`${pack.id} · ${pack.sorties??'—'} выл.`});
   return feature;
  }));
 },[packages,selectedPackage]);
 useEffect(()=>{
  const source=handle.current?.routes;if(!source)return;source.clear();
  plan?.missions.forEach(m=>m.legs.filter(l=>l.coordinates.some(p=>p[0]!==l.coordinates[0][0]||p[1]!==l.coordinates[0][1])).forEach(l=>{const feature=new Feature(format.readGeometry({type:'LineString',coordinates:l.coordinates},projection));feature.setProperties({phase:l.phase,color:m.color});source.addFeature(feature);}));
 },[plan]);
 useEffect(()=>{
  const source=handle.current?.vehicles;if(!source)return;source.clear();
  (plan?currentSorties(plan.missions,playhead):[]).forEach(m=>{const state=sampleMission(m,playhead);const feature=new Feature(format.readGeometry({type:'Point',coordinates:state.position},projection));feature.setProperties({profileId:m.profile_id,heading:missionHeading(m,playhead),color:m.color,label:`${m.aircraft_id} · ${state.altitude.toFixed(0)} м`});source.addFeature(feature);});
 },[plan,playhead]);
 useEffect(()=>{
  const state=handle.current;if(!state)return;
  state.modify.setActive(tool==='edit'&&!disabled);
  setVertexCount(0);sketchActive.current=false;
  if(tool==='edit'||disabled)return;
  const interaction=new Draw({type:tool==='launch_landing'||tool==='alternate'?'Point':'Polygon'});
  drawing.current=interaction;
  let geometryListener:EventsKey|undefined;
  const clearSketch=()=>{if(geometryListener)unByKey(geometryListener);geometryListener=undefined;sketchActive.current=false;setVertexCount(0);};
  interaction.on('drawstart',event=>{
   sketchActive.current=true;
   const geometry=event.feature.getGeometry()!;
   const update=()=>{if(geometry.getType()==='Polygon')setVertexCount(Math.max(0,(format.writeGeometryObject(geometry) as Polygon).coordinates[0].length-2));};
   if(geometry.getType()==='Polygon')setVertexCount(1);
   geometryListener=geometry.on('change',update);
  });
  interaction.on('drawabort',clearSketch);
  state.map.addInteraction(interaction);
  interaction.on('drawend',event=>{
   clearSketch();
   const {draft:current,onChange:change}=context.current;
   const geometry=format.writeGeometryObject(event.feature.getGeometry()!,projection);
   if(tool==='survey'&&surveyAreas(current).length<MAX_SURVEY_AREAS)change(replaceAreas(current,[...surveyAreas(current),geometry as Polygon]));
   if(tool==='allowed')change({...current,allowed_airspace:geometry as Polygon});
   if(tool==='forbidden')change({...current,no_fly_zones:[...current.no_fly_zones,geometry as Polygon]});
   if(tool==='launch_landing'||tool==='alternate'){
    const id=crypto.randomUUID();const firstMain=tool==='launch_landing'&&!current.sites.some(s=>s.kind==='launch_landing');
    change({...current,sites:[...current.sites,{id,kind:tool,name:`${tool==='alternate'?'Запасная':'Основная'} ${current.sites.length+1}`,coordinates:(geometry as Point).coordinates as Position}],fleet:firstMain?current.fleet.map(a=>({...a,start_site_id:id})):current.fleet});
   }
   setTool('edit');
  });
  return()=>{clearSketch();interaction.abortDrawing();state.map.removeInteraction(interaction);drawing.current=null;};
 },[tool,disabled]);
 const undo=()=>{
  if(disabled||modifying)return;
  if(sketchActive.current){drawing.current?.removeLastPoint();return;}
  if(canUndo){setTool('edit');onUndo();}
 };
 const redo=()=>{if(!disabled&&!modifying&&!sketchActive.current&&canRedo){setTool('edit');onRedo();}};
 useEffect(()=>{
  const keydown=(event:KeyboardEvent)=>{
   if(event.defaultPrevented||isEditingTarget(event.target)||shortcutsDisabled||showMaps)return;
   const key=event.key.toLowerCase();
   const command=event.ctrlKey||event.metaKey;
   if(command&&!event.altKey&&(key==='z'||key==='y')){
    event.preventDefault();if(disabled||modifying)return;
    if(key==='y'||event.shiftKey)redo();else undo();
   }else if(!command&&!event.altKey&&tool!=='edit'){
    if(key==='escape'){event.preventDefault();setTool('edit');}
    if(key==='backspace'){event.preventDefault();if(!disabled)drawing.current?.removeLastPoint();}
    if(key==='enter'&&vertexCount>=3&&!(event.target instanceof Element&&event.target.closest('button,a'))){event.preventDefault();if(!disabled)drawing.current?.finishDrawing();}
   }
  };
  window.addEventListener('keydown',keydown);
  return()=>window.removeEventListener('keydown',keydown);
 });
 useEffect(()=>{
  if(basemap==='none'){
   // Without a basemap the plan still works offline: vector layers on a plain grid.
   handle.current?.base.setSource(null);setError('');setMapMessage('');
   return;
  }
  const controller=new AbortController();let disposed=false;const listeners:EventsKey[]=[];
  setError('');setMapMessage('Загрузка подложки…');
  const timer=setTimeout(()=>{controller.abort();if(!disposed)setError('Тайлы не появились за 12 секунд. Проверьте соединение или выберите «Без подложки» — расчет работает и без карты.');},12000);
  const custom=userMaps.find(m=>m.id===basemap);
  handle.current?.base.setSource(null);
  void (async()=>{
   try{
    const source=custom?await createUserSource(custom,controller.signal):createSource(basemap==='satellite'?'satellite':'osm');
    if(disposed)return;
    listeners.push(source.on('tileloadend',()=>{clearTimeout(timer);if(!disposed)setMapMessage('');}));
    listeners.push(source.on('tileloaderror',()=>{clearTimeout(timer);if(!disposed){setMapMessage('');setError('Не загрузились тайлы. Проверьте ключ, CORS, формат и лимиты провайдера или выберите «Без подложки».');}}));
    listeners.push(source.on('change',()=>{if(source.getState()==='error'&&!disposed){clearTimeout(timer);setMapMessage('');setError('Не загрузилось описание слоя. Проверьте ключ и доступ к карте.');}}));
    handle.current?.base.setSource(source);
   }catch(e){if(!disposed){clearTimeout(timer);setMapMessage('');setError(controller.signal.aborted?'Превышено время загрузки подложки.':e instanceof Error?e.message:'Не удалось подключить карту.');}}
  })();
  return()=>{disposed=true;controller.abort();clearTimeout(timer);unByKey(listeners);};
 },[basemap,userMaps,mapRevision]);
 const updateMaps=(next:UserMap[])=>{setStorageError('');try{localStorage.setItem(MAP_STORAGE_KEY,serializeMaps(next));}catch{if(next.some(m=>m.remember)||userMaps.some(m=>m.remember)){setStorageError('Браузер не смог сохранить настройки карт. Изменение не применено.');return false;}}setUserMaps(next);return true;};
 const fit=()=>{const state=handle.current;if(!state)return;const source=[state.routes,state.source,state.task,state.zones].find(s=>s.getFeatures().length);if(source){state.map.updateSize();state.map.getView().cancelAnimations();state.map.getView().fit(source.getExtent()!,{padding:[60,40,70,40],maxZoom:16,duration:300});}};
 useEffect(()=>{fit();},[fitVersion,Boolean(plan)]);
 useEffect(()=>{
  const state=handle.current;if(!state||!focusSite)return;
  const kind=focusSite.kind||'site';
  if(kind==='site'){
   const site=context.current.draft.sites.find(s=>s.id===focusSite.id);
   if(site){state.map.updateSize();state.map.getView().animate({center:fromLonLat(site.coordinates),zoom:16,duration:300});target.current?.scrollIntoView({block:'nearest',behavior:'smooth'});}
   return;
  }
  // Zones and obstacles: fit the object; without an id, all objects of that kind.
  const [source,key]=kind==='area'?[state.source,'areaId']:kind==='zone'?[state.zones,'zoneId']:[state.obstacles,'obstacleId'];
  const found=source.getFeatures().filter(f=>(kind!=='area'||f.get('kind')==='survey')&&(focusSite.id===null||f.get(key)===focusSite.id));
  if(!found.length)return;
  const extent=found.map(f=>f.getGeometry()!.getExtent()).reduce((a,b)=>[Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[2],b[2]),Math.max(a[3],b[3])]);
  state.map.updateSize();state.map.getView().fit(extent,{padding:[80,60,80,60],maxZoom:17,duration:300});target.current?.scrollIntoView({block:'nearest',behavior:'smooth'});
 },[focusSite]);
 const basemapClass=['osm','satellite','none'].includes(basemap)?basemap:'custom';
 return <section className={`workspace basemap-${basemapClass}${plan?' has-plan':''}`} aria-label="Карта планирования">
  <div className="map-editor-bar">
   <div className="draw-tools" role="group" aria-label="Инструменты карты">{(Object.keys(names) as Tool[]).map(t=><button key={t} disabled={disabled||(t==='survey'&&surveyAreas(draft).length>=MAX_SURVEY_AREAS)} aria-pressed={tool===t} className={tool===t?'active':''} onClick={()=>setTool(t)}><i style={{background:colors[t]||'#94a3b8'}}/>{names[t]}</button>)}</div>
   <div className="map-edit-status">
    <p aria-live="polite">{tool==='edit'?'Перетащите вершину для изменения границы. Alt + щелчок удаляет вершину.':tool==='alternate'||tool==='launch_landing'?'Укажите точку на карте. Esc — отмена.':`Отмечайте вершины · двойной щелчок или Enter — завершить · Backspace — убрать вершину${vertexCount?` · Вершин: ${vertexCount}`:''}`}</p>
    <div className="map-history" role="group" aria-label="История изменений">
     <button disabled={disabled||modifying||(!vertexCount&&!canUndo)} onClick={undo} title={vertexCount?'Убрать последнюю вершину (Ctrl+Z / Backspace)':'Отменить изменение (Ctrl+Z)'}>{vertexCount?'↶ Убрать вершину':'↶ Отменить'}</button>
     <button disabled={disabled||modifying||vertexCount>0||!canRedo} onClick={redo} title="Повторить изменение (Ctrl+Shift+Z / Ctrl+Y)">↷ Повторить</button>
     {tool!=='edit'&&<>{tool!=='alternate'&&tool!=='launch_landing'&&<button disabled={disabled||vertexCount<3} onClick={()=>drawing.current?.finishDrawing()}>Завершить</button>}<button onClick={()=>setTool('edit')}>Отменить рисование</button></>}
    </div>
   </div>
  </div>
  <div className="map-stage">
  <div ref={target} className="map"/>
  <div className="map-top"><select aria-label="Подложка карты" value={basemap} onChange={e=>setBasemap(e.target.value)}><option value="osm">Схема · OpenStreetMap</option><option value="none">Без подложки · офлайн</option><option value="satellite" disabled={!hasMapTilerKey}>Спутник с подписями{!hasMapTilerKey?' · нужен ключ':''}</option>{userMaps.map(m=><option value={m.id} key={m.id}>{m.name}</option>)}</select><button onClick={()=>setShowMaps(v=>!v)}>Мои карты</button><button onClick={fit}>К участку</button></div>
  {showMaps&&<UserMaps maps={userMaps} onSave={map=>{if(!updateMaps([...userMaps.filter(m=>m.id!==map.id),map]))throw new Error('Не удалось сохранить карту в браузере. Проверьте доступ к локальному хранилищу.');setBasemap(map.id);}} onDelete={id=>{if(updateMaps(userMaps.filter(m=>m.id!==id))&&basemap===id)setBasemap('osm');}} onSelect={setBasemap} onClose={()=>setShowMaps(false)}/>}
  {(error||storageError)&&<div className="map-error" role="alert">{storageError||error}{error&&<button onClick={()=>setMapRevision(v=>v+1)}>Повторить загрузку</button>}</div>}
  {mapMessage&&!error&&<div className="map-loading" role="status">{mapMessage}</div>}
  {info&&<div className="map-info" role="dialog" aria-label={info.title}><div className="row between"><strong>{info.title}</strong><button onClick={()=>setInfo(null)} aria-label="Закрыть">×</button></div>{info.lines.map(l=><p key={l}>{l}</p>)}</div>}
  {(plan||(draft.restriction_zones?.length||0)+(draft.obstacles?.length||0)>0||packages)&&<div className="map-legend">{plan&&<span>Сплошная линия — съемка · пунктир — перелет</span>}
   {(draft.restriction_zones?.length||0)>0&&<span><i style={{background:ZONE_COLORS.prohibited}}/>Зоны: {plan?'сплошные — облетаются, пунктир — не мешают полету':'цвет — категория'}</span>}
   {(draft.obstacles?.length||0)>0&&<span><i style={{background:plan?'#D92D20':'#475467'}}/>Препятствия{plan?': красные — облетаются, серые — ниже полета':''}</span>}
   {packages&&<span><i style={{background:BRAND.orange}}/>Пакеты работ: щелкните, чтобы выбрать</span>}</div>}
  </div>
 </section>;
}
