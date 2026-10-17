import type { Polygon } from 'geojson';
import type { Aircraft, CostModel, CostPreset, Draft, Payload, SensorProfile, SurveyRequirement } from '../../shared/types';

/** Survey requirements (version 1.2): zones + survey type + quality parameters. Mirrors backend domain/migration.py. */
export const TYPE_NAMES:Record<Payload,string>={rgb:'RGB',multispectral:'мультиспектральная',thermal:'ИК',lidar:'LiDAR',geophysical:'геофизическая'};
export const SURVEY_TYPES=Object.keys(TYPE_NAMES) as Payload[];
export const LEGACY_ID='survey';
/** Used only while the open-data preset has not loaded: the formula without the cost of flying. */
export const DEFAULT_COST:CostModel={alpha_per_h:60,airtime_default:0,airtime:{},default_diagonal:30,diagonal:{},pairs:[]};

export const isMulti=(draft:Draft)=>Array.isArray(draft.requirements);
const areaCount=(draft:Draft)=>(draft.survey_area?1:0)+(draft.additional_survey_areas?.length||0);
/** Stable zone ids in the order of the areas; scenarios before 1.2 number them. */
export function areaIds(draft:Draft):string[]{
 const count=areaCount(draft);
 return draft.area_ids&&draft.area_ids.length===count?draft.area_ids:Array.from({length:count},(_,n)=>`zone-${n+1}`);
}
const freeId=(taken:Set<string>,prefix:string)=>{let n=1;while(taken.has(`${prefix}-${n}`))n++;return `${prefix}-${n}`;};

/** Scenario 1.1 → requirements: one requirement over every zone with the old settings; plans stay the same. */
export function upgradeDraft(draft:Draft):Draft{
 if(isMulti(draft))return draft;
 // Imported JSON may omit settings: the backend defaults apply.
 const s:Partial<Draft['settings']>=draft.settings||{},ids=areaIds(draft),camera=(s.coverage_mode||'manual')==='camera';
 const requirement:SurveyRequirement={id:LEGACY_ID,name:'Съемка',area_ids:ids,survey_type:draft.survey_type||'rgb',
  side_overlap_percent:s.side_overlap_percent??30,front_overlap_percent:s.front_overlap_percent??70};
 if(camera){if(s.max_gsd_cm!=null)requirement.max_gsd_cm=s.max_gsd_cm;}
 else requirement.swath_width_m=s.swath_width_m??100;
 return {...draft,area_ids:ids,requirements:[requirement],
  fleet:draft.fleet.map(a=>a.sensors!=null?a:{...a,sensors:a.camera_id?[a.camera_id]:[`generic_${a.payload}`]})};
}

/**
 * Replace the survey areas keeping zone ids: `kept[i]` is the index of the old area at position i, or null for a new one.
 * Requirements lose the zones that disappear; a requirement left without zones stays and is reported before planning.
 */
export function replaceAreas(draft:Draft,areas:Polygon[],kept?:(number|null)[]):Draft{
 const old=areaIds(draft),taken=new Set<string>();
 const ids=areas.map((_,i)=>{const from=kept?kept[i]:i<old.length?i:null;const id=from!=null&&old[from]!==undefined&&!taken.has(old[from])?old[from]:null;if(id)taken.add(id);return id;});
 for(const id of old)taken.add(id);
 const final=ids.map(id=>id??(()=>{const next=freeId(taken,'zone');taken.add(next);return next;})());
 const live=new Set(final);
 const next:Draft={...draft,survey_area:areas[0]||null,additional_survey_areas:areas.slice(1)};
 if(!isMulti(draft)&&!draft.area_ids)return next;
 return {...next,area_ids:areas.length?final:null,
  requirements:draft.requirements?.map(r=>({...r,area_ids:r.area_ids.filter(a=>live.has(a))}))??draft.requirements};
}
/** Index map for removing area `index`. */
export const withoutArea=(draft:Draft,areas:Polygon[],index:number)=>replaceAreas(draft,areas.filter((_,i)=>i!==index),areas.map((_,i)=>i).filter(i=>i!==index));

export function newRequirement(draft:Draft,type:Payload):SurveyRequirement{
 const taken=new Set((draft.requirements||[]).map(r=>r.id));
 return {id:freeId(taken,type),name:TYPE_NAMES[type][0].toUpperCase()+TYPE_NAMES[type].slice(1),area_ids:areaIds(draft),survey_type:type,
  side_overlap_percent:30,front_overlap_percent:70,...(type==='geophysical'?{line_spacing_m:50}:type==='thermal'||type==='lidar'?{swath_width_m:60}:{})};
}
export const requirementsOf=(draft:Draft,areaId:string)=>(draft.requirements||[]).filter(r=>r.area_ids.includes(areaId));

/** Profiles of payloads declared without a sensor profile; same ids and names as the backend. */
export function genericSensors():SensorProfile[]{
 return SURVEY_TYPES.map(type=>({id:`generic_${type}`,version:1,origin:'builtin',name:`Нагрузка ${TYPE_NAMES[type]} без профиля`,sensor_type:type,geometry:'unknown',
  compatible_profiles:[],source:'Нагрузка задана у борта без профиля сенсора: ширина полосы — из требования съемки.'}));
}
/** Sensors the aircraft carries (only one is installed per sortie). */
export function sensorsOf(aircraft:Aircraft):string[]{
 return aircraft.sensors??(aircraft.camera_id?[aircraft.camera_id]:[`generic_${aircraft.payload}`]);
}
export function sensorUsable(sensor:SensorProfile,profileId:string,userProfile:boolean){
 return sensor.id.startsWith('generic_')||userProfile||sensor.compatible_profiles.includes(profileId);
}

/** Requirement problems the planner would reject anyway, shown next to the fields. */
export function requirementIssues(draft:Draft):{id:string;field:string;text:string}[]{
 const issues:{id:string;field:string;text:string}[]=[];
 const ids=new Set(areaIds(draft)),seen=new Set<string>();
 for(const r of draft.requirements||[]){
  if(seen.has(r.id))issues.push({id:r.id,field:'id',text:`Идентификатор ${r.id} повторяется`});seen.add(r.id);
  if(!/^[a-zA-Z0-9_.:-]{1,100}$/.test(r.id))issues.push({id:r.id,field:'id',text:'Идентификатор: латиница, цифры и _ . : -'});
  if(!r.area_ids.length)issues.push({id:r.id,field:'area_ids',text:'Не выбрано ни одной зоны'});
  const missing=r.area_ids.filter(a=>!ids.has(a));
  if(missing.length)issues.push({id:r.id,field:'area_ids',text:`Нет зон: ${missing.join(', ')}`});
 }
 if(isMulti(draft)&&!(draft.requirements||[]).length)issues.push({id:'',field:'',text:'Добавьте хотя бы одно требование съемки'});
 return issues;
}

// ---- Cost model J = α·T + cᵀt + tᵀQt --------------------------------------------------------

/** The open-data preset for this fleet by aircraft type (catalog/costs.py); the server applies the same values per aircraft. */
export function presetModel(fleet:Aircraft[],preset:CostPreset):CostModel{
 const generic=preset.by_profile.gemini;
 const types=[...new Set(fleet.map(a=>a.profile_id))].filter(id=>preset.by_profile[id]);
 return {alpha_per_h:preset.alpha_per_h,airtime_default:generic.airtime,airtime:{},default_diagonal:generic.diagonal,diagonal:{},pairs:[],
  by_profile:Object.fromEntries(types.map(id=>[id,{airtime:preset.by_profile[id].airtime,diagonal:preset.by_profile[id].diagonal}]))};
}
/** Type coefficients written out per aircraft, as the server does: own entry, else its type's, else the default. */
export function resolveCost(model:CostModel,fleet:Aircraft[]):CostModel{
 const types=model.by_profile||{};
 if(!Object.keys(types).length)return model;
 const airtime={...(model.airtime||{})},diagonal={...model.diagonal};
 for(const a of fleet){
  const t=types[a.profile_id];if(!t)continue;
  if(t.airtime!=null&&!(a.id in airtime))airtime[a.id]=t.airtime;
  if(t.diagonal!=null&&!(a.id in diagonal))diagonal[a.id]=t.diagonal;
 }
 return {...model,airtime,diagonal};
}
const keep=(map:Record<string,number>|undefined,known:Set<string>)=>Object.fromEntries(Object.entries(map||{}).filter(([k])=>known.has(k)));
const renamed=(map:Record<string,number>|undefined,from:string,to:string)=>Object.fromEntries(Object.entries(map||{}).map(([k,v])=>[k===from?to:k,v]));

/** Cost model without references to aircraft that are not in the fleet. */
export function pruneCost(model:CostModel|null|undefined,ids:string[]):CostModel|null{
 if(!model)return model??null;
 const known=new Set(ids);
 return {...model,diagonal:keep(model.diagonal,known),...(model.airtime?{airtime:keep(model.airtime,known)}:{}),
  pairs:model.pairs.filter(p=>known.has(p.a)&&known.has(p.b))};
}
/** Rename an aircraft: its coefficients follow it, so the matrix keeps meaning the same aircraft. */
export function renameAircraft(draft:Draft,from:string,to:string):Draft{
 if(from===to)return draft;
 if(!to.trim()||draft.fleet.some(a=>a.id===to))throw new Error(`Борт ${to} уже есть в парке или имя пустое.`);
 const model=draft.cost_model;
 const cost=model?{...model,diagonal:renamed(model.diagonal,from,to),...(model.airtime?{airtime:renamed(model.airtime,from,to)}:{}),
  pairs:model.pairs.map(p=>({...p,a:p.a===from?to:p.a,b:p.b===from?to:p.b}))}:model;
 return {...draft,fleet:draft.fleet.map(a=>a.id===from?{...a,id:to}:a),cost_model:cost};
}
export function withFleet(draft:Draft,fleet:Aircraft[]):Draft{
 return {...draft,fleet,cost_model:draft.cost_model?pruneCost(draft.cost_model,fleet.map(a=>a.id)):draft.cost_model};
}
/** Dense symmetric Q in fleet order. */
export function costMatrix(model:CostModel,ids:string[]):number[][]{
 const index=new Map(ids.map((id,n)=>[id,n]));
 const q=ids.map((id,i)=>ids.map((_,j)=>i===j?(model.diagonal[id]??model.default_diagonal):0));
 for(const p of model.pairs){const i=index.get(p.a),j=index.get(p.b);if(i===undefined||j===undefined||i===j)continue;q[i][j]=p.q;q[j][i]=p.q;}
 return q;
}
/** Eigenvalues of a symmetric matrix (cyclic Jacobi); enough for up to 20 aircraft. */
export function eigenvalues(matrix:number[][]):number[]{
 const n=matrix.length,a=matrix.map(r=>[...r]);
 for(let sweep=0;sweep<100;sweep++){
  let off=0;for(let i=0;i<n;i++)for(let j=i+1;j<n;j++)off+=a[i][j]**2;
  if(off<1e-22)break;
  for(let p=0;p<n;p++)for(let q=p+1;q<n;q++){
   if(Math.abs(a[p][q])<1e-300)continue;
   const theta=(a[q][q]-a[p][p])/(2*a[p][q]);
   const t=Math.sign(theta||1)/(Math.abs(theta)+Math.sqrt(theta*theta+1)),c=1/Math.sqrt(t*t+1),s=t*c;
   for(let k=0;k<n;k++){const akp=a[k][p],akq=a[k][q];a[k][p]=c*akp-s*akq;a[k][q]=s*akp+c*akq;}
   for(let k=0;k<n;k++){const apk=a[p][k],aqk=a[q][k];a[p][k]=c*apk-s*aqk;a[q][k]=s*apk+c*aqk;}
  }
 }
 return a.map((r,i)=>r[i]).sort((x,y)=>x-y);
}
/** Same rules as backend planning/cost.py validate_cost_model; null when the model is valid. */
export function costModelError(model:CostModel,ids:string[]):string|null{
 const known=new Set(ids);
 const unknown=[...new Set([...Object.keys(model.diagonal),...Object.keys(model.airtime||{}),...model.pairs.flatMap(p=>[p.a,p.b])].filter(k=>!known.has(k)))];
 if(unknown.length)return `Модель стоимости ссылается на борта, которых нет в парке: ${unknown.join(', ')}`;
 if(!(model.alpha_per_h>=0)||!(model.default_diagonal>=0))return 'α и коэффициент по умолчанию — неотрицательные числа';
 for(const [k,v] of Object.entries(model.diagonal))if(!(v>=0))return `Индивидуальный коэффициент ${k}: нужно число от 0`;
 if(!((model.airtime_default??0)>=0))return 'Стоимость часа налета по умолчанию — неотрицательное число';
 for(const [k,v] of Object.entries(model.airtime||{}))if(!(v>=0))return `Стоимость часа налета ${k}: нужно число от 0`;
 const seen=new Set<string>();
 for(const p of model.pairs){
  if(p.a===p.b)return `Пара ${p.a}–${p.b}: для одного борта задайте индивидуальный коэффициент`;
  if(!Number.isFinite(p.q))return `Пара ${p.a}–${p.b}: коэффициент должен быть числом`;
  const key=[p.a,p.b].sort().join('\u0000');if(seen.has(key))return `Пара ${p.a}–${p.b} задана дважды`;seen.add(key);
 }
 if(!ids.length)return null;
 const q=costMatrix(model,ids),smallest=eigenvalues(q)[0],scale=Math.max(1,...q.flat().map(Math.abs));
 if(smallest<-1e-9*scale)return `Матрица Q не положительно полуопределена: наименьшее собственное значение ${smallest.toPrecision(3)} у.е./ч². Для пары достаточно q_ij² ≤ q_ii·q_jj, для всей матрицы нужна проверка целиком.`;
 return null;
}
export const airtimeRate=(model:CostModel,id:string)=>model.airtime?.[id]??model.airtime_default??0;
/** J for given T and flight times, h; for examples in the editor. */
export function costValue(model:CostModel,ids:string[],completionH:number,airtimeH:Record<string,number>){
 const q=costMatrix(model,ids),t=ids.map(id=>airtimeH[id]||0);
 let quadratic=0;for(let i=0;i<ids.length;i++)for(let j=0;j<ids.length;j++)quadratic+=t[i]*q[i][j]*t[j];
 const flying=ids.reduce((sum,id,n)=>sum+airtimeRate(model,id)*t[n],0);
 return {linear:model.alpha_per_h*completionH,flying,quadratic,total:model.alpha_per_h*completionH+flying+quadratic};
}
