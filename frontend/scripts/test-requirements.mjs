import {readFile,readdir} from 'node:fs/promises';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source=await readFile(new URL('../src/features/scenarios/requirements.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const m=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const square=(x)=>({type:'Polygon',coordinates:[[[x,0],[x+1,0],[x+1,1],[x,1],[x,0]]]});
const legacy={survey_area:square(0),additional_survey_areas:[square(2)],survey_type:'rgb',fleet:[{id:'G1',profile_id:'gemini',payload:'rgb',camera_id:'geoscan_pf1b_v1'},{id:'8',profile_id:'geoscan_801',payload:'rgb'}],
 settings:{side_overlap_percent:35,front_overlap_percent:75,coverage_mode:'camera',max_gsd_cm:3,swath_width_m:100}};

test('a 1.1 scenario becomes one requirement over every zone, keeping cameras and overlaps',()=>{
 const up=m.upgradeDraft(legacy);
 assert.deepEqual(up.area_ids,['zone-1','zone-2']);
 assert.deepEqual(up.requirements,[{id:'survey',name:'Съемка',area_ids:['zone-1','zone-2'],survey_type:'rgb',side_overlap_percent:35,front_overlap_percent:75,max_gsd_cm:3}]);
 assert.deepEqual(up.fleet.map(a=>a.sensors),[['geoscan_pf1b_v1'],['generic_rgb']]);
 assert.equal(m.upgradeDraft(up),up,'already converted scenarios are returned as they are');
 const manual=m.upgradeDraft({...legacy,settings:{...legacy.settings,coverage_mode:'manual'}});
 assert.equal(manual.requirements[0].swath_width_m,100);assert.equal(manual.requirements[0].max_gsd_cm,undefined);
});
test('conversion matches the backend migration for every contract example',async()=>{
 const dir=new URL('../../contracts/',import.meta.url);
 const expected=JSON.parse(await readFile(new URL('../scripts/fixtures/upgraded-examples.json',import.meta.url),'utf8'));
 for(const name of (await readdir(dir)).filter(n=>n.startsWith('scenario.')&&n.endsWith('.json'))){
  const data=JSON.parse(await readFile(new URL(name,dir),'utf8'));
  const up=m.upgradeDraft(data);
  assert.deepEqual({area_ids:up.area_ids,requirements:up.requirements,sensors:up.fleet.map(a=>a.sensors)},expected[name],name);
 }
});
test('removing a zone keeps the ids of the others and drops it from requirements',()=>{
 const up=m.upgradeDraft({...legacy,additional_survey_areas:[square(2),square(4)]});
 const areas=[square(0),square(2),square(4)];
 const next=m.withoutArea(up,areas,1);
 assert.deepEqual(next.area_ids,['zone-1','zone-3']);
 assert.deepEqual(next.requirements[0].area_ids,['zone-1','zone-3']);
 const added=m.replaceAreas(next,[square(0),square(4),square(6)],[0,1,null]);
 assert.deepEqual(added.area_ids,['zone-1','zone-3','zone-2'],'a new zone takes a free id and is not added to requirements silently');
 assert.deepEqual(added.requirements[0].area_ids,['zone-1','zone-3']);
 assert.deepEqual(m.requirementIssues({...added,requirements:[{...added.requirements[0],area_ids:[]}]}).map(i=>i.field),['area_ids']);
 const plain=m.replaceAreas(legacy,[square(0)]);
 assert.equal(plain.area_ids,undefined,'1.1 scenarios stay without zone ids');
});
test('cost model follows renamed aircraft and drops removed ones',()=>{
 const draft={fleet:[{id:'A'},{id:'B'},{id:'C'}],cost_model:{alpha_per_h:60,default_diagonal:30,diagonal:{A:10,C:5},pairs:[{a:'A',b:'B',q:2},{a:'B',b:'C',q:1}]}};
 const renamed=m.renameAircraft(draft,'A','Z');
 assert.deepEqual(renamed.cost_model.diagonal,{Z:10,C:5});assert.deepEqual(renamed.cost_model.pairs[0],{a:'Z',b:'B',q:2});
 assert.throws(()=>m.renameAircraft(draft,'A','B'));
 const pruned=m.withFleet(renamed,renamed.fleet.filter(a=>a.id!=='B'));
 assert.deepEqual(pruned.cost_model.pairs,[]);assert.deepEqual(pruned.cost_model.diagonal,{Z:10,C:5});
 assert.equal(m.costModelError(pruned.cost_model,pruned.fleet.map(a=>a.id)),null);
});
test('Q must be symmetric positive semidefinite; reordering aircraft does not change J',()=>{
 const ids=['A','B','C'];
 const ok={alpha_per_h:60,default_diagonal:30,diagonal:{},pairs:[{a:'A',b:'B',q:30}]};
 assert.equal(m.costModelError(ok,ids),null,'q_ij² = q_ii·q_jj is the boundary: semidefinite');
 const bad={...ok,pairs:[{a:'A',b:'B',q:31}]};
 assert.match(m.costModelError(bad,ids),/не положительно полуопределена/);
 // Each pair alone passes q_ij² ≤ q_ii·q_jj, the whole matrix does not.
 const triangle={alpha_per_h:0,default_diagonal:1,diagonal:{},pairs:[{a:'A',b:'B',q:-.6},{a:'B',b:'C',q:-.6},{a:'A',b:'C',q:-.6}]};
 assert.match(m.costModelError(triangle,ids),/полуопределена/);
 assert.match(m.costModelError({...ok,pairs:[{a:'A',b:'A',q:1}]},ids),/индивидуальный/);
 assert.match(m.costModelError({...ok,pairs:[{a:'A',b:'B',q:1},{a:'B',b:'A',q:1}]},ids),/дважды/);
 assert.match(m.costModelError({...ok,diagonal:{X:1}},ids),/нет в парке: X/);
 const t={A:1,B:2,C:.5};
 const a=m.costValue(ok,ids,2,t),b=m.costValue(ok,['C','B','A'],2,t);
 assert.ok(Math.abs(a.total-b.total)<1e-9);
 // α·T + Σ q_ii t_i² + 2·q_AB·t_A·t_B = 120 + 30·(1+4+0.25) + 2·30·1·2
 assert.ok(Math.abs(a.total-(120+157.5+120))<1e-9);
 assert.deepEqual(m.eigenvalues([[2,1],[1,2]]).map(x=>+x.toFixed(9)),[1,3]);
});
test('the cost of flying follows aircraft, enters J, and the preset is applied by aircraft type',()=>{
 const draft={fleet:[{id:'A',profile_id:'gemini'},{id:'B',profile_id:'geoscan_801'}],cost_model:{alpha_per_h:100,airtime_default:20,airtime:{A:50,B:70},default_diagonal:0,diagonal:{},pairs:[]}};
 const renamed=m.renameAircraft(draft,'A','Z');
 assert.deepEqual(renamed.cost_model.airtime,{Z:50,B:70});
 assert.deepEqual(m.withFleet(renamed,renamed.fleet.filter(a=>a.id!=='B')).cost_model.airtime,{Z:50});
 // J = α·T + cᵀt = 100·2 + 50·1 + 70·0.5
 assert.equal(m.costValue(draft.cost_model,['A','B'],2,{A:1,B:.5}).total,200+50+35);
 assert.match(m.costModelError({...draft.cost_model,airtime:{X:1}},['A','B']),/нет в парке: X/);
 const preset={alpha_per_h:2491,by_profile:{gemini:{airtime:1890,diagonal:2.19},geoscan_801:{airtime:3400,diagonal:2.95}}};
 const model=m.presetModel(draft.fleet,preset);
 assert.deepEqual(model.by_profile,{gemini:{airtime:1890,diagonal:2.19},geoscan_801:{airtime:3400,diagonal:2.95}});
 assert.equal(model.alpha_per_h,2491);assert.equal(model.airtime_default,1890);
 assert.deepEqual(m.resolveCost(model,draft.fleet).airtime,{A:1890,B:3400});
 assert.equal(m.costModelError(m.resolveCost(model,draft.fleet),['A','B']),null);
 // Own value of an aircraft wins over its type, the type over the default.
 const fleet=[{id:'A',profile_id:'gemini'},{id:'B',profile_id:'gemini'},{id:'C',profile_id:'geoscan_801'}];
 const own={alpha_per_h:1,airtime_default:5,airtime:{B:9},default_diagonal:1,diagonal:{},pairs:[],by_profile:{gemini:{airtime:7}}};
 const r=m.resolveCost(own,fleet);
 assert.deepEqual(r.airtime,{A:7,B:9});assert.equal(m.airtimeRate(r,'C'),5);
 assert.equal(m.costValue(r,['A','B','C'],0,{A:1,B:1,C:1}).flying,7+9+5);
});
