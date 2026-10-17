import type { Plan, TradeoffPoint } from '../../shared/types';

const fmt=(x:number,d=1)=>x.toLocaleString('ru-RU',{minimumFractionDigits:d,maximumFractionDigits:d});
const hours=(h:number)=>`${fmt(h,2)} ч`;
const minutes=(h:number)=>`${fmt(h*60,1)} мин`;
export const OBJECTIVE_NAMES:Record<Plan['objective'],string>={min_completion_time:'Быстрее завершить',min_total_airtime:'Меньше суммарный налет',min_cost:'Минимум стоимости J'};
const SHORT:Record<Plan['objective'],string>={min_completion_time:'T',min_total_airtime:'L',min_cost:'J'};
// Plans saved before cᵀt existed carry no airtime_total: their c was 0.
const flyingOf=(c:NonNullable<Plan['cost']>)=>c.airtime_total??0;

/** J of one plan: total and every contribution with its units. */
export function CostBreakdown({plan}:{plan:Plan}){
 const c=plan.cost;if(!c)return null;
 const used=c.individual.filter(x=>x.t_h>0),withRate=used.some(x=>(x.c??0)>0);
 return <details className="subtle" open={plan.objective==='min_cost'}><summary>Стоимость J = α·T + cᵀt + tᵀQt · {fmt(c.total,0)} у.е.{c.default_model?' · коэффициенты по открытым данным':''}</summary>
  <div className="metrics cost-metrics">
   <div><small>J, у.е.</small><strong>{fmt(c.total,0)}</strong></div>
   <div><small>α·T · срок</small><strong>{fmt(c.linear,0)}</strong></div>
   <div><small>cᵀt · налет</small><strong>{fmt(flyingOf(c),0)}</strong></div>
   <div><small>tᵀQt · риск и пары</small><strong>{fmt(c.quadratic,1)}</strong></div>
   <div><small>T · L · бортов</small><strong>{hours(c.completion_h)} · {hours(c.airtime_h)} · {c.aircraft_used.length}</strong></div>
  </div>
  <div className="table-scroll"><table className="data-table compact" aria-label="Слагаемые стоимости"><thead><tr><th>Слагаемое</th><th>Расчет</th><th>у.е.</th></tr></thead><tbody>
   <tr><td>α·T — срок операции</td><td>{fmt(c.alpha_per_h,0)} у.е./ч × {fmt(c.completion_h,3)} ч</td><td>{fmt(c.linear,2)}</td></tr>
   {withRate&&used.map(x=><tr key={`c${x.aircraft_id}`}><td>c·t — налет {x.aircraft_id}</td><td>{fmt(x.c??0,0)} у.е./ч × {fmt(x.t_h,3)} ч</td><td>{fmt(x.airtime_value??0,2)}</td></tr>)}
   {used.map(x=><tr key={`q${x.aircraft_id}`}><td>q·t² — {x.aircraft_id}</td><td>{fmt(x.q,2)} у.е./ч² × {fmt(x.t_h,3)}² ч²</td><td>{fmt(x.value,2)}</td></tr>)}
   {c.pairs.map(x=><tr key={`${x.a}|${x.b}`}><td>2·q·t·t — {x.a} и {x.b}</td><td>2 × {fmt(x.q,2)} × {fmt(x.t_a_h,3)} × {fmt(x.t_b_h,3)}</td><td>{fmt(x.value,2)}</td></tr>)}
   <tr className="total"><td>J</td><td>у.е. — условные единицы стоимости</td><td>{fmt(c.total,2)}</td></tr></tbody></table></div>
  {c.individual.some(x=>x.t_h===0)&&<p className="hint">Не летали и не входят в J: {c.individual.filter(x=>x.t_h===0).map(x=>x.aircraft_id).join(', ')}.</p>}
  {c.search&&<p className="hint">Поиск по J: начальное распределение {fmt(c.search.initial,0)} → {fmt(c.search.final,0)} у.е.; проверено перестановок {c.search.trials}, точных пересчетов {c.search.evaluations}{c.search.budget_exhausted?'; бюджет поиска исчерпан — возможно, есть план лучше':''}. Глобальный минимум не гарантируется.</p>}
 </details>;
}

/** Which point of the trade-off a plan is: the nearest by T and cost of flying. */
function pointOf(points:TradeoffPoint[],plan:Plan){
 const c=plan.cost;if(!c)return -1;
 const t=c.completion_h,f=c.total-c.linear;
 let best=-1,gap=Infinity;
 points.forEach((p,i)=>{const d=Math.abs(p.t_h-t)/Math.max(t,1e-6)+Math.abs(p.flying-f)/Math.max(Math.abs(f),1);if(d<gap){gap=d;best=i;}});
 return gap<.01?best:-1;
}

/** The plans the criteria chose from, as (T, cost of flying): J picks the point where α·T + cost is least. */
function TradeoffChart({plans}:{plans:Plan[]}){
 const source=plans.find(p=>p.cost?.tradeoff)?.cost!.tradeoff;
 if(!source||source.points.length<2)return null;
 const points=source.points,alpha=source.alpha_per_h;
 const W=560,H=230,pad={l:58,r:16,t:14,b:36};
 const ts=points.map(p=>p.t_h),fs=points.map(p=>p.flying);
 const [t0,t1]=[Math.min(...ts),Math.max(...ts)],[f0,f1]=[Math.min(...fs),Math.max(...fs)];
 const dt=(t1-t0)||t1*.1||1,df=(f1-f0)||f1*.1||1;
 const x=(t:number)=>pad.l+(t-(t0-dt*.08))/(dt*1.16)*(W-pad.l-pad.r);
 const y=(f:number)=>H-pad.b-(f-(f0-df*.12))/(df*1.24)*(H-pad.t-pad.b);
 const marks=plans.map(p=>({objective:p.objective,index:pointOf(points,p)})).filter(m=>m.index>=0);
 const j=marks.find(m=>m.objective==='min_cost');
 const hull=points.filter(p=>p.alpha_range).sort((a,b)=>a.t_h-b.t_h);
 // The line of equal J through the plan of J: cost = J − α·T.
 const jp=j?points[j.index]:null;
 const line=jp?[t0-dt*.08,t1+dt*.08].map(t=>[x(t),y(jp.j-alpha*t)]):null;
 return <figure className="tradeoff" aria-label="Компромисс между сроком и стоимостью налета">
  <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Варианты плана: срок от ${minutes(t0)} до ${minutes(t1)}, стоимость налета от ${fmt(f0,0)} до ${fmt(f1,0)} у.е.`}>
   <defs><clipPath id="tradeoff-area"><rect x={pad.l} y={pad.t} width={W-pad.l-pad.r} height={H-pad.t-pad.b}/></clipPath></defs>
   <line className="axis" x1={pad.l} y1={H-pad.b} x2={W-pad.r} y2={H-pad.b}/><line className="axis" x1={pad.l} y1={pad.t} x2={pad.l} y2={H-pad.b}/>
   <text className="axis-label" x={(W+pad.l)/2} y={H-6} textAnchor="middle">T — срок операции, мин</text>
   <text className="axis-label" x={12} y={(H-pad.b+pad.t)/2} textAnchor="middle" transform={`rotate(-90 12 ${(H-pad.b+pad.t)/2})`}>cᵀt + tᵀQt, у.е.</text>
   {[t0,t1].map((t,i)=><text key={`t${i}`} className="tick" x={x(t)} y={H-pad.b+14} textAnchor="middle">{fmt(t*60,1)}</text>)}
   {[f0,f1].map((f,i)=><text key={`f${i}`} className="tick" x={pad.l-6} y={y(f)+4} textAnchor="end">{fmt(f,0)}</text>)}
   {line&&<polyline className="iso" clipPath="url(#tradeoff-area)" points={line.map(p=>p.join(',')).join(' ')}/>}
   {hull.length>1&&<polyline className="front" points={hull.map(p=>`${x(p.t_h)},${y(p.flying)}`).join(' ')}/>}
   {points.map((p,i)=><circle key={i} className={`variant${p.dominated?' dominated':''}`} cx={x(p.t_h)} cy={y(p.flying)} r={p.dominated?3:4.5}><title>{`${minutes(p.t_h)} · налет ${hours(p.l_h)} · ${fmt(p.flying,0)} у.е. · J ${fmt(p.j,0)} · бортов ${p.aircraft_used}${p.dominated?' · хуже другого варианта по обоим показателям':''}`}</title></circle>)}
   {marks.map((m,k)=>{const p=points[m.index];const same=marks.filter(o=>o.index===m.index);const shift=same.indexOf(m);
    return <g key={k} className={`pick pick-${SHORT[m.objective]}`}><circle cx={x(p.t_h)} cy={y(p.flying)} r={7+shift*4}/><text x={x(p.t_h)+10+shift*12} y={y(p.flying)-8}>{SHORT[m.objective]}</text></g>;})}
  </svg>
  <figcaption className="hint">Точки — варианты плана, из которых выбирали критерии; кружки — выбор «Быстрее» (T), «Меньше налет» (L) и «Стоимость» (J). Пунктир — линия равной J через план J: ниже и левее нее планов нет.
   {hull.length>1&&<> J выбирает (при неизменных c и q): {hull.map(p=>`${minutes(p.t_h)} — при α ${p.alpha_range![1]===null?`от ${fmt(p.alpha_range![0],0)}`:`${fmt(p.alpha_range![0],0)}–${fmt(p.alpha_range![1]!,0)}`} у.е./ч`).join('; ')}. Сейчас α = {fmt(alpha,0)}.</>}
   {hull.length<=1&&' Один вариант лучше остальных и по сроку, и по стоимости налета: его выберет любой критерий.'}
   {source.relevelled?' Точки — до разведения по высоте (эшелоны), итоговый план может немного отличаться.':''}</figcaption>
 </figure>;
}

/** Plans of the three criteria on the same inputs, J evaluated with the same cost model for each. */
export function CostComparison({plans,selected,onSelect}:{plans:Plan[];selected:number;onSelect:(i:number)=>void}){
 const withCost=plans.filter(p=>p.cost);
 if(withCost.length<2)return null;
 const max=Math.max(...withCost.map(p=>p.cost!.total))||1;
 const best={T:Math.min(...plans.map(p=>p.metrics.completion_time_s)),L:Math.min(...plans.map(p=>p.metrics.total_airtime_s)),J:Math.min(...withCost.map(p=>p.cost!.total))};
 const same=JSON.stringify(withCost[0].cost!.model);
 const jPlan=plans.find(p=>p.objective==='min_cost'),tPlan=plans.find(p=>p.objective==='min_completion_time'),lPlan=plans.find(p=>p.objective==='min_total_airtime');
 const coincides=jPlan&&[tPlan,lPlan].find(p=>p&&p.cost&&Math.abs(p.cost.total-jPlan.cost!.total)<1e-6&&p.metrics.completion_time_s===jPlan.metrics.completion_time_s);
 return <section className="cost-compare" aria-label="Сравнение критериев">
  <h3 className="block-title">Сравнение критериев <span>одни исходные данные · J по одной модели стоимости</span></h3>
  <div className="cost-bars" role="list">{plans.map((p,i)=>{const c=p.cost;return <button key={p.id} role="listitem" className={`cost-bar-row${i===selected?' active':''}`} onClick={()=>onSelect(i)} aria-label={`${OBJECTIVE_NAMES[p.objective]}: J ${c?fmt(c.total,0):'—'} у.е.`}>
   <span className="cost-bar-label">{OBJECTIVE_NAMES[p.objective]}</span>
   <span className="cost-bar-track">{c&&<><i className="cost-seg linear" style={{width:`${c.linear/max*100}%`}}/><i className="cost-seg flying" style={{width:`${flyingOf(c)/max*100}%`}}/><i className="cost-seg individual" style={{width:`${c.individual_total/max*100}%`}}/><i className="cost-seg pairs" style={{width:`${Math.max(0,c.pairs_total)/max*100}%`}}/></>}</span>
   <span className="cost-bar-value">{c?fmt(c.total,0):'—'}</span></button>;})}</div>
  <div className="cost-legend"><span><i className="cost-seg linear"/>α·T — срок</span><span><i className="cost-seg flying"/>cᵀt — налет</span><span><i className="cost-seg individual"/>Σ q·t² — риск по бортам</span><span><i className="cost-seg pairs"/>Σ 2q·t·t — пары</span></div>
  <div className="table-scroll"><table className="data-table compact" aria-label="Показатели планов"><thead><tr><th>Цель расчета</th><th>T, срок</th><th>L, налет</th><th>J, у.е.</th><th>α·T</th><th>cᵀt</th><th>tᵀQt</th><th>Бортов</th></tr></thead><tbody>
   {plans.map((p,i)=>{const c=p.cost;return <tr key={p.id} className={i===selected?'active':''}><td>{OBJECTIVE_NAMES[p.objective]}</td>
    <td className={p.metrics.completion_time_s===best.T?'best':''}>{minutes(p.metrics.completion_time_s/3600)}</td><td className={p.metrics.total_airtime_s===best.L?'best':''}>{minutes(p.metrics.total_airtime_s/3600)}</td>
    <td className={c&&c.total===best.J?'best':''}>{c?fmt(c.total,0):'—'}</td><td>{c?fmt(c.linear,0):'—'}</td><td>{c?fmt(flyingOf(c),0):'—'}</td><td>{c?fmt(c.quadratic,1):'—'}</td><td>{p.metrics.aircraft_used}</td></tr>;})}</tbody></table></div>
  <TradeoffChart plans={plans}/>
  <p className="hint">«Быстрее завершить» минимизирует T, «Меньше налет» — L = Σt<sub>i</sub>, «Стоимость» — J = α·T + cᵀt + tᵀQt: цену срока, цену налета каждого борта и риск сосредоточить работу на одном борту. Лучшее значение в столбце выделено.
   Все три критерия выбирают из одного набора вариантов, поэтому план критерия не хуже двух других по своему показателю.
   {coincides?` Сейчас план J совпал с планом «${OBJECTIVE_NAMES[coincides.objective]}»: при этих коэффициентах он дешевле всех найденных вариантов; при каких α выгоднее другие варианты — на графике.`:''}
   {' '}Все три — эвристики, оптимальность не доказана.{withCost.some(p=>JSON.stringify(p.cost!.model)!==same)?' Внимание: модели стоимости планов различаются.':''}</p>
 </section>;
}
