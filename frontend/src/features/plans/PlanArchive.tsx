import { useEffect, useRef, useState } from 'react';
import { api } from '../../shared/api';
import type { PlanPage } from '../../shared/types';

export default function PlanArchive({onClose,onOpen}:{onClose:()=>void;onOpen:(id:string)=>Promise<void>}){
 const [offset,setOffset]=useState(0);
 const [revision,setRevision]=useState(0);
 const [page,setPage]=useState<PlanPage|null>(null);
 const [loading,setLoading]=useState(true);
 const [opening,setOpening]=useState<string|null>(null);
 const [error,setError]=useState('');
 const panel=useRef<HTMLElement>(null);
 useEffect(()=>{
  const controller=new AbortController();
  setLoading(true);setError('');setPage(null);
  panel.current?.scrollTo({top:0});
  api.plans(offset,controller.signal).then(result=>{if(!controller.signal.aborted)setPage(result);})
   .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:'Не удалось загрузить архив');})
   .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[offset,revision]);
 const open=async(id:string)=>{setOpening(id);setError('');try{await onOpen(id);}catch(e){setError(e instanceof Error?e.message:'Не удалось открыть план');}finally{setOpening(null);}};
 return <section ref={panel} className="connection-panel archive-panel" aria-label="Архив расчетов">
  <div className="row between"><div><span className="eyebrow">СОХРАНЕННЫЕ ОПЕРАЦИИ</span><h2>Архив расчетов</h2></div><button disabled={!!opening} onClick={onClose} aria-label="Закрыть архив">×</button></div>
  <p className="muted">Откройте план вместе с исходным сценарием, маршрутами и симуляцией. Текущий черновик можно будет вернуть кнопкой «Отменить».</p>
  <div className="row between"><span className="muted">{page?`Всего расчетов: ${page.total}`:'Загрузка списка…'}</span><button disabled={loading||!!opening} onClick={()=>{setOffset(0);setRevision(v=>v+1);}}>Обновить список</button></div>
  <div aria-live="polite">{loading&&<p>Загрузка архива…</p>}{error&&<p role="alert" className="error">{error}</p>}</div>
  {page&&!page.items.length&&<p className="empty">{offset?'На этой странице расчетов нет. Вернитесь назад или обновите список.':'Здесь появятся выполненные расчеты. Загрузите демо-сценарий и нажмите «Рассчитать».'}</p>}
  {page?.items.map(p=><article className="archive-card" key={p.id}>
   <div className="row between"><h3>{p.name}</h3><span className="archive-badge">Демо</span></div>
   <p className="muted">{p.calculated_at?'Расчет':'Исходный снимок'} · {new Date(p.created_at).toLocaleString('ru-RU')} · {p.id.slice(0,8)}</p>
   <strong>{p.objective==='min_completion_time'?'Быстрее завершить':'Меньше суммарный налет'}</strong>
   <div className="archive-metrics"><span>Завершение <b>{(p.metrics.completion_time_s/60).toFixed(1)} мин</b></span><span>Налет <b>{(p.metrics.total_airtime_s/60).toFixed(1)} мин</b></span><span>Площадь <b>{(p.metrics.area_m2/10000).toFixed(1)} га</b></span><span>Аппараты <b>{p.metrics.aircraft_used}</b></span></div>
   <div className="row actions"><button className="primary" disabled={!!opening} onClick={()=>void open(p.id)}>{opening===p.id?'Открывается…':'Открыть план'}</button><a className="button" href={api.reportUrl(p.id)} target="_blank" rel="noopener noreferrer">Отчет / печать</a></div>
  </article>)}
  <div className="row between archive-pagination"><button disabled={loading||!!opening||offset===0} onClick={()=>setOffset(v=>Math.max(0,v-12))}>Назад</button><span>{page&&page.total>0?`${page.items.length?offset+1:0}–${offset+page.items.length} из ${page.total}`:''}</span><button disabled={loading||!!opening||!page||offset+page.items.length>=page.total} onClick={()=>setOffset(v=>v+12)}>Далее</button></div>
 </section>;
}
