import { useState } from 'react';
import type { Diagnostic, DiagnosticAction, NavTarget } from '../../shared/types';

const SUBJECTS:Record<string,string>={aircraft:'Борта',sites:'Площадки',requirements:'Требования',areas:'Зоны',zones:'Зоны ограничений'};
const SEVERITY:Record<Diagnostic['severity'],string>={error:'Ошибка',warning:'Предупреждение',info:'Сведения'};

type Props={diagnostics:Diagnostic[];title:string;onNavigate:(target:NavTarget)=>void;onApply:(action:DiagnosticAction)=>void;onClose?:()=>void};

/** Structured problems: cause, subjects, actual and required values, and the way to the field. Changes only after a preview. */
export default function DiagnosticsPanel({diagnostics,title,onNavigate,onApply,onClose}:Props){
 const [preview,setPreview]=useState<{index:number;action:DiagnosticAction}|null>(null);
 if(!diagnostics.length)return null;
 return <section className="diagnostics" aria-label={title}>
  <div className="row between"><h3 className="block-title">{title} <span>{diagnostics.length}</span></h3>{onClose&&<button className="icon-button" aria-label="Скрыть" onClick={onClose}>×</button>}</div>
  <ul>{diagnostics.map((d,i)=><li key={`${d.code}-${i}`} className={`diagnostic ${d.severity}`}>
   <div className="diagnostic-head"><span className={`badge ${d.severity}`}>{SEVERITY[d.severity]}</span><span className="badge">{d.category_name}</span><b>{d.title}</b><small className="muted">{d.code}</small></div>
   <p>{d.message}</p>
   {(d.actual!=null||d.required!=null)&&<p className="diagnostic-values">{d.actual!=null&&<span>Фактически: <b>{d.actual}</b></span>}{d.required!=null&&<span>Требуется: <b>{d.required}</b></span>}</p>}
   {Object.entries(d.subjects||{}).filter(([,v])=>v&&v.length).map(([k,v])=><p key={k} className="muted">{SUBJECTS[k]||k}: {v!.join(', ')}</p>)}
   {d.actions.length>0&&<div className="row actions">{d.actions.map((a,n)=>a.kind==='navigate'&&a.target?
    <button key={n} onClick={()=>onNavigate(a.target!)}>{a.label} →</button>:
    <button key={n} onClick={()=>setPreview({index:i,action:a})}>{a.label}…</button>)}</div>}
   {preview?.index===i&&<div className="note" role="group" aria-label="Предпросмотр изменения">
    <p><b>Будет изменено:</b> {preview.action.label}.{preview.action.description?` ${preview.action.description}`:''} Требования к качеству, ограничения и резерв не ослабляются. Изменение можно отменить кнопкой «Отменить».</p>
    <div className="row actions"><button className="primary" onClick={()=>{onApply(preview.action);setPreview(null);}}>Применить</button><button onClick={()=>setPreview(null)}>Не применять</button></div>
   </div>}
  </li>)}</ul>
 </section>;
}
