import type { FleetAnalysis } from '../../shared/types';

const minute=(seconds?:number|null)=>seconds===undefined||seconds===null?'—':`${(seconds/60).toFixed(0)} мин`;

export default function FleetAnalysisView({analysis,stale,disabled,onPlan,onClose}:{analysis:FleetAnalysis;stale:boolean;disabled:boolean;onPlan:(aircraft:number)=>void;onClose:()=>void}){
 const deadline=analysis.deadline_s;
 return <section className="analysis" aria-label="Анализ парка">
  <div className="results-heading"><div><span className="eyebrow">АНАЛИЗ ПАРКА · БЫСТРЕЙШИЙ ПЛАН ДЛЯ РАЗНОГО ЧИСЛА БОРТОВ</span>
   <h2>{analysis.aircraft_needed!==null?`Нужно бортов: ${analysis.aircraft_needed} из ${analysis.fleet_size}`:deadline!==null?'Срок не выполним':'Время от числа бортов'}</h2></div>
   <button onClick={onClose}>Скрыть</button></div>
  <p className={analysis.aircraft_needed===null&&deadline!==null?'error':''}>{analysis.verdict}</p>
  {stale&&<p className="error">Сценарий изменился после анализа — повторите его.</p>}
  {deadline!==null&&<p className="muted">Срок: {minute(deadline)}{analysis.deadline_source==='sunset'&&analysis.sunset?` — до заката ${new Date(analysis.sunset).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}`:''}.</p>}
  <div className="table-scroll"><table aria-label="Время и налет по числу бортов"><thead><tr><th>Бортов</th><th>Завершение</th><th>Налет</th><th>Вылетов</th><th>Ресурс до ТО</th><th>В срок</th><th/></tr></thead>
   <tbody>{analysis.rows.map(r=><tr key={r.aircraft} className={r.aircraft===analysis.aircraft_needed?'selected':''}>
    <td>{r.aircraft}{r.feasible&&r.aircraft_used!==undefined&&r.aircraft_used<r.aircraft?` (занято ${r.aircraft_used})`:''}</td>
    {r.feasible?<><td>{minute(r.completion_s)}{r.best_aircraft!==null&&r.best_aircraft!==r.aircraft&&r.best_completion_s!==null&&r.best_completion_s<(r.completion_s??0)?<small><br/>лучше план с {r.best_aircraft}: {minute(r.best_completion_s)}</small>:null}</td>
     <td>{minute(r.total_airtime_s)}</td><td>{r.sorties}</td><td>{r.maintenance_share!==undefined?`${(r.maintenance_share*100).toFixed(2)}%`:'—'}</td>
     <td>{r.meets_deadline===null||r.meets_deadline===undefined?'—':r.meets_deadline?<span className="ok">да</span>:<span className="error">нет</span>}</td>
     <td><button disabled={disabled} onClick={()=>onPlan(r.aircraft)}>План</button></td></>
    :<td colSpan={6} className="muted" title={r.message}>не рассчитан: {r.code==='NO_PLAN_FOUND'||r.code==='CAPACITY'?'не хватает ресурса бортов':r.code}</td>}
   </tr>)}</tbody></table></div>
  {analysis.balanced&&analysis.balanced.feasible&&<p className="muted">Меньше всего налета в срок: {analysis.balanced.aircraft} бортов — завершение {minute(analysis.balanced.completion_s)}, налет {minute(analysis.balanced.total_airtime_s)} (критерий «меньше налет»).</p>}
  {analysis.saturated_at!==null&&<p className="muted">Больше {analysis.saturated_at} бортов план не использует: дальнейший рост парка время не сокращает.</p>}
  {analysis.truncated&&<p className="error">Анализ остановлен по времени расчета: показаны не все варианты.</p>}
  <p className="muted">Для каждого числа бортов строится полный план с критерием «быстрее завершить». Эвристика не монотонна: при большем парке план иногда хуже — тогда указан лучший вариант с меньшим числом бортов. Расчет занял {analysis.calculation_time_s.toFixed(1)} с.</p>
 </section>;
}
