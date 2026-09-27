import { useEffect, useState } from 'react';
import type { Polygon } from 'geojson';
import { api } from '../../shared/api';
import type { WeatherObservation } from '../../shared/types';

export default function WeatherPanel({areas,disabled,onApply}:{areas:Polygon[];disabled:boolean;onApply:(observation:WeatherObservation)=>void}){
 const points=areas.flatMap(area=>area.coordinates[0]);
 const lon=points.length?(Math.min(...points.map(p=>p[0]))+Math.max(...points.map(p=>p[0])))/2:0;
 const lat=points.length?(Math.min(...points.map(p=>p[1]))+Math.max(...points.map(p=>p[1])))/2:0;
 const [enabled,setEnabled]=useState(false),[auto,setAuto]=useState(false),[revision,setRevision]=useState(0);
 const [data,setData]=useState<WeatherObservation|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[now,setNow]=useState(Date.now());
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),30000);return()=>clearInterval(timer);},[]);
 useEffect(()=>{if(!enabled||!auto)return;const timer=setInterval(()=>setRevision(r=>r+1),300000);return()=>clearInterval(timer);},[enabled,auto]);
 useEffect(()=>{
  if(!enabled||!areas.length)return;
  const controller=new AbortController();setLoading(true);setError('');
  api.weather(lat,lon,controller.signal).then(value=>{if(!controller.signal.aborted){setData(value);setNow(Date.now());}}).catch(e=>{if(!controller.signal.aborted)setError((e as Error).message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[enabled,revision,lat,lon]);
 const stale=data?now-Date.parse(data.valid_at)>3600000:false;
 return <section className="weather-panel" aria-label="Погода участка"><div className="section-title">Погода участка <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a></div>
  <p className="muted">Модельные данные для центра охвата всех зон. Один снимок применяется ко всей операции. Ветер на 10 м; условия на высоте съемки могут отличаться.</p>
  <button disabled={disabled||!areas.length||loading} onClick={()=>{setEnabled(true);setRevision(r=>r+1);}}>{loading?'Получение погоды…':'Обновить погоду'}</button>
  {!areas.length&&<p className="muted">Сначала задайте зону съемки.</p>}
  <label className="check-row"><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/> Обновлять каждые 5 минут, пока открыта панель</label>
  {error&&<p role="alert" className="error">{error}</p>}
  {data&&<><div className="weather-values"><span>Ветер <b>{data.wind_speed_mps} м/с</b></span><span>Откуда <b>{data.wind_from_deg}°</b></span><span>Порывы <b>{data.wind_gusts_mps} м/с</b></span><span>Температура <b>{data.temperature_c} °C</b></span><span>Осадки <b>{data.precipitation_mm} мм</b></span></div>
   <p className="muted">Время данных: {new Date(data.valid_at).toLocaleString('ru-RU')}. Получено: {new Date(data.fetched_at).toLocaleTimeString('ru-RU')}.</p>
   {stale&&<p role="alert" className="error">Данные устарели: требуется обновление.</p>}
   <button className="full" disabled={disabled||loading||stale||Boolean(error)} onClick={()=>onApply(data)}>Применить ветер к сценарию</button>
   <p className="muted">Обновление панели не меняет план. Применение сбросит результат и потребует пересчета. Осадки и температура — справочно.</p>
  </>}
 </section>;
}
