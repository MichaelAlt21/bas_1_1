import { useEffect, useRef, useState } from 'react';
import type { Aircraft, AircraftProfile, CameraProfile, Draft } from '../../shared/types';
import ProfileForm from './ProfileForm';
import { addAircraft, groupFleet, MAX_FLEET } from './fleetModel';
import { aircraftAltitude } from '../scenarios/levels';

interface Props {
 draft:Draft; catalog:AircraftProfile[]; cameras:CameraProfile[]; initialSiteId:string|null;
 onChange:(draft:Draft)=>void; onCreated:(profile:AircraftProfile)=>void; onLocate:(id:string)=>void;
}

export default function FleetPanel({draft,catalog,cameras,initialSiteId,onChange,onCreated,onLocate}:Props){
 const [adding,setAdding]=useState(!draft.fleet.length);
 const [profileId,setProfileId]=useState('');
 const [targetId,setTargetId]=useState(initialSiteId||'');
 const [quantity,setQuantity]=useState(1);
 const [notice,setNotice]=useState('');
 const [error,setError]=useState('');
 const composer=useRef<HTMLDivElement>(null);
 const noticeFleet=useRef<Aircraft[]|null>(null);
 const selectedGroup=useRef<HTMLElement>(null);
 useEffect(()=>{selectedGroup.current?.scrollIntoView({block:'nearest'});},[initialSiteId]);
 const groups=groupFleet(draft.fleet,draft.sites);
 const available=Math.max(0,MAX_FLEET-draft.fleet.length);
 const profile=catalog.find(p=>p.id===profileId)||catalog.find(p=>p.airframe==='multirotor')||catalog[0];
 const target=groups.assigned.some(g=>g.site.id===targetId)?targetId:'';
 const count=Math.min(quantity,available);
 const beginAdd=(siteId:string|null)=>{
  setTargetId(siteId||'');setAdding(true);setError('');setNotice('');
  requestAnimationFrame(()=>composer.current?.scrollIntoView({block:'nearest',behavior:'smooth'}));
 };
 const commit=(fleet:Aircraft[])=>{noticeFleet.current=fleet;onChange({...draft,fleet});};
 const update=(id:string,patch:Partial<Aircraft>)=>{commit(draft.fleet.map(a=>a.id===id?{...a,...patch}:a));setNotice('');};
 const renderAircraft=(aircraft:Aircraft)=>{
  const model=catalog.find(p=>p.id===aircraft.profile_id);
  const validSite=groups.assigned.some(g=>g.site.id===aircraft.start_site_id);
  const compatible=cameras.filter(c=>c.sensor_type===aircraft.payload&&(model?.origin==='user'||c.compatible_profiles.includes(aircraft.profile_id)));
  const camera=cameras.find(c=>c.id===aircraft.camera_id);
  const height=aircraftAltitude(aircraft,draft.settings.altitude_m);
  return <article className="fleet-unit" key={aircraft.id} aria-label={`Борт ${aircraft.id}`}>
   <div className="fleet-unit-heading"><div><strong>{aircraft.id}</strong><small>{model?.name||aircraft.profile_id}</small></div><button className="remove-aircraft" aria-label={`Убрать ${aircraft.id} из парка`} onClick={()=>{commit(draft.fleet.filter(a=>a.id!==aircraft.id));setNotice(`${aircraft.id} убран из парка. Вернуть: «Отменить» в верхней панели.`);}}>Убрать</button></div>
   <div className="fleet-unit-fields"><label>Заряд, %<input aria-label={`Заряд ${aircraft.id}, %`} type="number" min={1} max={100} value={aircraft.battery_percent} onChange={e=>update(aircraft.id,{battery_percent:e.target.valueAsNumber||0})}/></label>
    <label>Площадка<select aria-label={`Площадка для ${aircraft.id}`} value={validSite?aircraft.start_site_id||'':''} onChange={e=>{const id=e.target.value;update(aircraft.id,{start_site_id:id||null});setNotice(`${aircraft.id}: ${groups.assigned.find(g=>g.site.id===id)?.site.name||'без площадки'}.`);}}><option value="">Без площадки</option>{groups.assigned.map(({site})=><option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
   </div>
   <label>Скорость {aircraft.id}, м/с<input type="number" min={model?.airframe==='fixed_wing'?18:2} max={model?.max_horizontal_speed_mps||100} step={.5} placeholder={`По умолчанию: ${model?.airframe==='fixed_wing'?draft.settings.fixed_wing?.airspeed_mps||22:draft.settings.airspeed_mps}`} value={aircraft.airspeed_mps??''} onChange={e=>update(aircraft.id,{airspeed_mps:e.target.value===''?null:e.target.valueAsNumber||0})}/></label>
   <label>Высота {aircraft.id}, м<input type="number" min={model?.airframe==='fixed_wing'?100:20} max={300} placeholder={draft.settings.echelons?`Авто: от ${draft.settings.altitude_m}`:`Общая: ${draft.settings.altitude_m}`} value={aircraft.altitude_m??''} onChange={e=>update(aircraft.id,{altitude_m:e.target.value===''?null:Math.max(20,Math.min(300,e.target.valueAsNumber||20))})}/></label>
   {draft.settings.coverage_mode==='camera'&&<>
    <label>Камера {aircraft.id}<select value={aircraft.camera_id||''} onChange={e=>update(aircraft.id,{camera_id:e.target.value||null})}><option value="">Выберите камеру</option>{compatible.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}{aircraft.camera_id&&!compatible.some(c=>c.id===aircraft.camera_id)&&<option value={aircraft.camera_id}>Несовместимый профиль · {aircraft.camera_id}</option>}</select></label>
    {!compatible.length&&<p className="fleet-warning">Подтвержденного профиля камеры пока нет. Используйте ручную ширину захвата или другой борт.</p>}
    {camera&&<p className="muted">GSD ≈ {(Math.max(camera.sensor_width_mm/camera.image_width_px,camera.sensor_height_mm/camera.image_height_px)*height/camera.focal_length_mm*100).toFixed(2)} см/пикс · захват {(height*camera.sensor_width_mm/camera.focal_length_mm).toFixed(1)} м на {height} м. <a href={camera.source_url} target="_blank" rel="noopener noreferrer">Источник</a></p>}
    <label>Минимальный интервал снимков {aircraft.id}, с<input type="number" min={.1} max={60} step={.1} placeholder="Не подтвержден" value={aircraft.trigger_interval_s??''} onChange={e=>update(aircraft.id,{trigger_interval_s:e.target.value===''?null:e.target.valueAsNumber||0})}/></label>
   </>}
   {model?.airframe==='fixed_wing'&&<p className="fleet-warning">Самолет участвует в расчете по демонстрационной модели. Радиус, заход, набор и парашют — шаг «Параметры», раздел «Съемка».</p>}
   {aircraft.battery_percent<=draft.settings.reserve_percent&&<p className="fleet-warning">Заряд должен быть выше резерва {draft.settings.reserve_percent}%.</p>}
  </article>;
 };
 return <section className="fleet-manager" aria-label="Управление парком">
  <div className="fleet-overview"><div><strong>{draft.fleet.length} / {MAX_FLEET}</strong><span>БПЛА в операции</span></div><div><strong>{groups.assigned.length}</strong><span>стартовых площадок</span></div></div>
  <button className="primary full" disabled={!available} onClick={()=>beginAdd(initialSiteId)}>+ Добавить БПЛА</button>
  {!available&&<p className="muted">Достигнут лимит демонстрации: {MAX_FLEET} БПЛА. Уберите борт, чтобы добавить другой.</p>}
  {groups.unassigned.length>0&&<p className="fleet-warning">Без площадки: {groups.unassigned.length}. Назначьте каждому борту место старта перед расчетом.</p>}
  <div aria-live="polite">{notice&&noticeFleet.current===draft.fleet&&<p className="fleet-notice">{notice}</p>}{error&&<p className="error" role="alert">{error}</p>}</div>
  {adding&&<div ref={composer} className="fleet-composer" role="region" aria-label="Добавление БПЛА">
   <div className="row between"><strong>Добавить в операцию</strong><button aria-label="Закрыть добавление БПЛА" onClick={()=>setAdding(false)}>×</button></div>
   <label>Модель БПЛА<select value={profile?.id||''} onChange={e=>setProfileId(e.target.value)}>{!catalog.length&&<option value="">Каталог недоступен</option>}{catalog.map(p=><option key={p.id} value={p.id}>{p.name}{p.airframe==='fixed_wing'?' · самолет, демо':''}</option>)}</select></label>
   {profile&&<div className="fleet-model-preview">{profile.origin==='builtin'&&['gemini','geoscan_801','geoscan_201'].includes(profile.id)&&<img src={`/aircraft/${profile.id}.png`} alt={profile.name}/>}<div><strong>{profile.name}</strong><span>До {profile.max_flight_time_min} мин</span><span>Ветер до {profile.max_wind_mps} м/с</span></div></div>}
   {profile&&<details className="muted"><summary>Характеристики и источник</summary><p>Скорость до {profile.max_horizontal_speed_mps} м/с. Источник: {profile.source}</p></details>}
   {profile?.airframe==='fixed_wing'&&<p className="fleet-warning">Учитываются дуги разворотов, прямой набор высоты и парашютная посадка. Для примера с достаточным пространством откройте «Демо 201».</p>}
   <label>Добавить на площадку<select value={target} onChange={e=>setTargetId(e.target.value)}><option value="">Назначить позже</option>{groups.assigned.map(({site})=><option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
   <div className="fleet-add-actions"><label>Количество<select value={count||''} disabled={!available} onChange={e=>setQuantity(Number(e.target.value))}>{!available&&<option value="">0</option>}{Array.from({length:available},(_,i)=><option key={i+1} value={i+1}>{i+1}</option>)}</select></label><button className="primary" disabled={!available||!profile} onClick={()=>{if(!profile)return;try{const fleet=addAircraft(draft.fleet,profile,draft.sites,target||null,count,draft.survey_type);commit(fleet);setAdding(false);setQuantity(1);setError('');setNotice(`Добавлено: ${count}. ${groups.assigned.find(g=>g.site.id===target)?.site.name||'Назначьте площадку в карточке борта.'}`);}catch(e){setError((e as Error).message);}}}>Добавить · {count}</button></div>
  </div>}
  <div className="section-title">Парк по площадкам <span>назначено {draft.fleet.length-groups.unassigned.length} из {draft.fleet.length}</span></div>
  {groups.assigned.map(({site,aircraft})=><section ref={initialSiteId===site.id?selectedGroup:undefined} className={`site-fleet${initialSiteId===site.id?' highlighted':''}`} aria-label={`Парк площадки ${site.name}`} key={site.id}>
   <div className="site-fleet-heading"><h3>{site.name}</h3><span className="fleet-count">{aircraft.length} БПЛА</span></div>
   <div className="site-fleet-actions"><button onClick={()=>onLocate(site.id)}>На карте</button><button disabled={!available} aria-label={`Добавить БПЛА на ${site.name}`} onClick={()=>beginAdd(site.id)}>+ БПЛА</button></div>
   {aircraft.length?aircraft.map(renderAircraft):<p className="empty">Парк площадки пуст.</p>}
  </section>)}
  {(groups.unassigned.length>0||!groups.assigned.length)&&<section className="site-fleet unassigned-fleet" aria-label="БПЛА без площадки"><div className="site-fleet-heading"><h3>Без площадки</h3><span className="fleet-count">{groups.unassigned.length} БПЛА</span></div>{!groups.assigned.length&&<p className="muted">Создайте точку «Старт / посадка» на карте. БПЛА можно добавить заранее.</p>}{groups.unassigned.map(renderAircraft)}</section>}
  {draft.sites.some(s=>s.kind==='alternate')&&<p className="muted">Запасные площадки не используются для назначения старта: {draft.sites.filter(s=>s.kind==='alternate').map(s=>s.name).join(', ')}.</p>}
  <ProfileForm onCreated={p=>{onCreated(p);setProfileId(p.id);beginAdd(target||null);}}/>
 </section>;
}
