import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source=await readFile(new URL('../src/features/fleet/fleetModel.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {addAircraft,groupFleet,MAX_FLEET}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const sites=[{id:'west',kind:'launch_landing'},{id:'east',kind:'launch_landing'},{id:'reserve',kind:'alternate'}];
const profile={id:'gemini',name:'Gemini'};

test('bulk addition keeps existing assignments, generates unique ids and assigns every new aircraft to the chosen site',()=>{
 const original=[{id:'Gemini-01',profile_id:'gemini',start_site_id:'west',battery_percent:72}];
 const result=addAircraft(original,profile,sites,'east',2);
 assert.equal(original.length,1);
 assert.deepEqual(result[0],original[0]);
 assert.equal(new Set(result.map(a=>a.id)).size,3);
 assert.ok(result.slice(1).every(a=>a.start_site_id==='east'&&a.battery_percent===100&&a.payload==='rgb'));
 assert.deepEqual(groupFleet(result,sites).assigned.map(g=>g.aircraft.length),[1,2]);
});
test('capacity overflow and invalid counts leave the original fleet untouched',()=>{
 const original=addAircraft([],profile,sites,null,MAX_FLEET-1);
 for(const count of [2,0,-1,1.5])assert.throws(()=>addAircraft(original,profile,sites,'west',count));
 assert.equal(original.length,MAX_FLEET-1);
 assert.equal(addAircraft(original,profile,sites,'west',1).length,MAX_FLEET);
});
test('missing or alternate sites cannot be selected as launch sites',()=>{
 for(const site of ['missing','reserve'])assert.throws(()=>addAircraft([],profile,sites,site,1));
 assert.equal(addAircraft([],profile,[],null,1)[0].start_site_id,null);
});
test('new aircraft take the payload passed to addAircraft and default a compatible camera',()=>{
 const rgb=addAircraft([],profile,sites,null,1,'rgb');
 assert.equal(rgb[0].payload,'rgb');
 assert.equal(rgb[0].camera_id,'geoscan_pf1b_v1');
 const multispectral=addAircraft([],profile,sites,null,1,'multispectral');
 assert.equal(multispectral[0].payload,'multispectral');
 assert.equal(multispectral[0].camera_id,'geoscan_pollux_v1');
 const lidar=addAircraft([],profile,sites,null,1,'lidar');
 assert.equal(lidar[0].payload,'lidar');
 assert.equal(lidar[0].camera_id,null);
});
test('all aircraft remain visible after removal of a site or import of an invalid assignment',()=>{
 const fleet=[{id:'one',start_site_id:'west'},{id:'two',start_site_id:'missing'},{id:'three',start_site_id:'reserve'},{id:'four',start_site_id:null}];
 const groups=groupFleet(fleet,sites);
 assert.deepEqual(groups.unassigned.map(a=>a.id),['two','three','four']);
 assert.equal(groups.assigned[1].aircraft.length,0);
 assert.equal(groupFleet(fleet,[]).unassigned.length,4);
});
