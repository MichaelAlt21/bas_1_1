import { useEffect, useState } from 'react';
import type { Plan } from '../../shared/types';
import { currentSorties, sortieState } from './playback';
import AircraftTag from './AircraftTag';

export default function PlaybackControls({plan,time,onTime}:{plan:Plan;time:number;onTime:(value:number)=>void}){
 const [playing,setPlaying]=useState(false),[speed,setSpeed]=useState(10);
 const end=plan.metrics.completion_time_s;
 useEffect(()=>{
  if(!playing)return;
  const initial=time,started=performance.now();
  const timer=setInterval(()=>{const next=Math.min(end,initial+(performance.now()-started)/1000*speed);onTime(next);if(next>=end)setPlaying(false);},100);
  return()=>clearInterval(timer);
 },[playing,speed,end,onTime]);
 const two=(n:number)=>String(Math.floor(n)).padStart(2,'0');
 const stamp=(v:number)=>v>=3600?`${Math.floor(v/3600)}:${two(v%3600/60)}:${two(v%60)}`:`${two(v/60)}:${two(v%60)}`;
 return <section className="playback" aria-label="Симуляция полета"><div className="row between"><div><span className="eyebrow">СИМУЛЯЦИЯ · БЕЗ ТЕЛЕМЕТРИИ</span><h2>Воспроизведение миссии</h2></div><strong>{stamp(time)} / {stamp(end)}</strong></div>
  <div className="row"><button onClick={()=>{if(time>=end)onTime(0);setPlaying(p=>!p);}}>{playing?'Пауза':'Воспроизвести'}</button><button onClick={()=>{setPlaying(false);onTime(0);}}>В начало</button><select aria-label="Скорость воспроизведения" value={speed} onChange={e=>setSpeed(Number(e.target.value))}>{[1,10,30,60,300,600].map(v=><option key={v} value={v}>×{v}</option>)}</select></div>
  <label>Время миссии<input aria-label="Время миссии" type="range" min={0} max={end} step="any" value={time} onChange={e=>{setPlaying(false);onTime(Number(e.target.value));}}/></label>
  <div className="playback-states">{currentSorties(plan.missions,time).map(m=>{const sample=sortieState(m,time);return <div key={m.aircraft_id}><AircraftTag mission={m}/><span>{sample.phase} · {sample.altitude.toFixed(0)} м AGL</span></div>;})}</div>
  <p className="muted">Аппараты движутся по рассчитанному расписанию. Показанные позиции — симуляция, не данные реальных БПЛА.</p>
 </section>;
}
