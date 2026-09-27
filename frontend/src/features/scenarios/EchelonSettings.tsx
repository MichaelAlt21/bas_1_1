import type { Draft, SurveySettings } from '../../shared/types';
import { echelonLevels, LEGAL_CEILING_M } from './levels';

type Props={draft:Draft;onChange:(draft:Draft)=>void};

export function echelonSummary(draft:Draft){
 const s=draft.settings;
 const manual=draft.fleet.filter(a=>a.altitude_m!=null).length;
 if(s.echelons)return `авто · шаг ${s.vertical_separation_m??20} м`;
 return manual?`вручную · ${manual}`:'одна высота';
}

export function EchelonFields({draft,onChange}:Props){
 const s=draft.settings;
 const set=(patch:Partial<SurveySettings>)=>onChange({...draft,settings:{...s,...patch}});
 const step=s.vertical_separation_m??20,ceiling=s.altitude_ceiling_m??LEGAL_CEILING_M;
 const levels=echelonLevels(s.altitude_m,step,ceiling);
 const manual=draft.fleet.filter(a=>a.altitude_m!=null);
 const above=[s.altitude_m,...manual.map(a=>a.altitude_m as number)].some(h=>h>ceiling);
 return <>
  <label className="check-row"><input type="checkbox" checked={!!s.echelons} onChange={e=>set({echelons:e.target.checked})}/> Эшелоны: разводить борта по высоте автоматически</label>
  <div className="row">
   <label>Вертикальный интервал, м<input type="number" min={5} max={100} value={step} onChange={e=>set({vertical_separation_m:Math.max(5,Math.min(100,e.target.valueAsNumber||20))})}/></label>
   <label>Потолок над поверхностью, м<input type="number" min={20} max={1000} value={ceiling} onChange={e=>set({altitude_ceiling_m:Math.max(20,Math.min(1000,e.target.valueAsNumber||LEGAL_CEILING_M))})}/></label>
  </div>
  {s.echelons?<p className="hint">Борта, чьи маршруты сближаются ближе 30 м, получают разные эшелоны от высоты съемки вверх: {levels.map(h=>`${h} м`).join(', ')}.
   Коптеры поднимаются, если хватает заряда и GSD; самолет остается на своей высоте. Эшелоны сохраняются, если убирают общие участки маршрутов
   и удлиняют срок не больше чем на 5%. Разведение: 30 м по горизонтали или {step} м по высоте, в том числе на наборе и снижении над общей площадкой.</p>
   :<p className="hint">Высоту отдельного борта можно задать в его карточке на шаге «Парк». Без эшелонов и своих высот борта летят на одной высоте и разводятся только по времени.</p>}
  <p className="hint">Высоты — над поверхностью. Без разрешения на использование воздушного пространства БВС до 30 кг летают ниже {LEGAL_CEILING_M} м от поверхности, в пределах прямой видимости, днем, вне диспетчерских зон и зон ограничений.</p>
  {above&&<p className="fleet-warning">Высота выше потолка {ceiling} м: расчет выполнится с предупреждением о разрешительном порядке.</p>}
 </>;
}
