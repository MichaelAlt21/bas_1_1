import { useState } from 'react';
import { api } from '../../shared/api';
import type { AircraftProfile, AircraftProfileInput } from '../../shared/types';

const initial:AircraftProfileInput={name:'',airframe:'multirotor',max_flight_time_min:30,max_wind_mps:8,max_horizontal_speed_mps:12,source:''};
export default function ProfileForm({onCreated}:{onCreated:(profile:AircraftProfile)=>void}){
 const [input,setInput]=useState(initial),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const numeric=(key:'max_flight_time_min'|'max_wind_mps'|'max_horizontal_speed_mps',label:string,min:number,max:number)=><label>{label}<input required type="number" min={min} max={max} step="0.1" value={input[key]} onChange={e=>setInput({...input,[key]:e.target.valueAsNumber||0})}/></label>;
 return <details className="profile-form"><summary>+ Новый тип БПЛА</summary><form onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');setMessage('');try{const profile=await api.createProfile(input);onCreated(profile);setInput(initial);setMessage(`Профиль «${profile.name}» сохранен в каталоге.`);}catch(error){setError((error as Error).message);}finally{setBusy(false);}}}>
  <fieldset disabled={busy}><label>Название модели<input required maxLength={100} value={input.name} onChange={e=>setInput({...input,name:e.target.value})}/></label>
  <label>Тип аппарата<select value={input.airframe} onChange={e=>setInput({...input,airframe:e.target.value as AircraftProfileInput['airframe']})}><option value="multirotor">Коптер</option><option value="fixed_wing">Самолет · только каталог</option></select></label>
  {numeric('max_flight_time_min','Паспортная длительность, мин',.1,600)}
  {numeric('max_wind_mps','Предельный ветер, м/с',0,40)}
  {numeric('max_horizontal_speed_mps','Предельная скорость, м/с',.1,100)}
  <label>Источник характеристик<input required maxLength={300} placeholder="Модель, руководство, страница" value={input.source} onChange={e=>setInput({...input,source:e.target.value})}/></label>
  <p className="note">Характеристики вводите по документации. Новый коптер использует общую демонстрационную модель. Для исправлений создавайте новый профиль: старые расчеты сохраняют свой снимок параметров.</p>
  <button type="submit" className="full">{busy?'Сохранение…':'Сохранить тип БПЛА'}</button></fieldset>
 </form>{error&&<p role="alert" className="error">{error}</p>}{message&&<p role="status" className="muted">{message}</p>}</details>;
}
