import { useState } from 'react';
import { newMap, validateMap, type UserMap } from './mapConfig';

export default function UserMaps({maps,onSave,onDelete,onSelect,onClose}:{maps:UserMap[];onSave:(map:UserMap)=>void;onDelete:(id:string)=>void;onSelect:(id:string)=>void;onClose:()=>void}){
 const [input,setInput]=useState<UserMap>(newMap),[error,setError]=useState('');
 return <section className="connection-panel" aria-label="Пользовательские подложки"><div className="row between"><h2>Мои подложки</h2><button onClick={onClose} aria-label="Закрыть настройки подложек">×</button></div>
 <p className="muted">Стандартные OSM и hybrid-v4 остаются доступны с настройками проекта.</p>
 {maps.map(map=><div className="user-map-row" key={map.id}><strong>{map.name}</strong><small>{map.kind} · {map.remember?'сохранена в браузере':'до перезагрузки'}</small><div className="row"><button onClick={()=>onSelect(map.id)}>Выбрать</button><button onClick={()=>{setInput({...map});setError('');}}>Изменить</button><button onClick={()=>{onDelete(map.id);if(input.id===map.id)setInput(newMap());}}>Удалить</button></div></div>)}
 <form onSubmit={e=>{e.preventDefault();setError('');try{const value=validateMap(input);if(!value.id&&maps.length>=20)throw new Error('Максимум 20 пользовательских карт.');onSave({...value,id:value.id||`custom_${crypto.randomUUID()}`});setInput(newMap());}catch(error){setError((error as Error).message);}}}>
 <div className="section-title">{input.id?'Редактирование подложки':'Новая подложка'}</div>
 <label>Название подложки<input required maxLength={100} value={input.name} onChange={e=>setInput({...input,name:e.target.value})}/></label>
 <label>Провайдер / формат<select value={input.kind} onChange={e=>setInput({...input,kind:e.target.value as UserMap['kind'],attribution:e.target.value==='maptiler'?'© MapTiler © OpenStreetMap contributors':''})}><option value="maptiler">MapTiler · свой ключ</option><option value="xyz">XYZ · растровые тайлы</option><option value="tilejson">TileJSON · растровый источник</option></select></label>
 {input.kind==='maptiler'?<label>ID карты MapTiler<input required list="maptiler-styles" value={input.mapId} onChange={e=>setInput({...input,mapId:e.target.value})}/><datalist id="maptiler-styles"><option value="hybrid-v4"/><option value="streets-v4"/><option value="satellite"/></datalist></label>:<label>URL {input.kind==='xyz'?'тайлов':'TileJSON'}<input required placeholder={input.kind==='xyz'?'https://tiles.example.com/{z}/{x}/{y}.png?key={key}':'https://tiles.example.com/tiles.json?key={key}'} value={input.url} onChange={e=>setInput({...input,url:e.target.value})}/></label>}
 <label>Ключ провайдера {input.kind==='maptiler'?'':'(если нужен)'}<input type="password" autoComplete="off" maxLength={1000} value={input.apiKey} onChange={e=>setInput({...input,apiKey:e.target.value})}/></label>
 <label>Атрибуция — текст правообладателей<input required maxLength={500} value={input.attribution} onChange={e=>setInput({...input,attribution:e.target.value})}/></label>
 <div className="row"><label>Минимальный zoom<input type="number" min={0} max={24} required value={input.minZoom} onChange={e=>setInput({...input,minZoom:e.target.valueAsNumber})}/></label><label>Максимальный zoom<input type="number" min={0} max={24} required value={input.maxZoom} onChange={e=>setInput({...input,maxZoom:e.target.valueAsNumber})}/></label></div>
 <label className="check-row"><input type="checkbox" checked={input.remember} onChange={e=>setInput({...input,remember:e.target.checked})}/> Сохранить карту и ключ в этом браузере</label>
 <p className="note">Ключ карты используется браузером и виден в его сетевых запросах. Используйте публичный ключ с ограничениями по домену. Без сохранения настройки действуют до перезагрузки. Ключи не входят в сценарии и экспорт миссий.</p>
 <p className="muted">XYZ: EPSG:3857, тайлы 256 px. Для TileJSON нужен CORS и растровые тайлы. WMS, WMTS, векторные стили и загрузка GeoTIFF пока не поддерживаются.</p>
 {error&&<p role="alert" className="error">{error}</p>}
 <div className="row actions"><button type="submit" className="primary">{input.id?'Сохранить и выбрать':'Добавить и выбрать'}</button>{input.id&&<button type="button" onClick={()=>setInput(newMap())}>Новая карта</button>}</div>
 </form></section>;
}
