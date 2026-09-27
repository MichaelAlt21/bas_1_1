import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source=await readFile(new URL('../src/features/plans/playback.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {sampleMission,currentSorties,sortieState}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const mission={start_s:10,finish_s:40,legs:[
 {start:10,end:20,phase:'climb',coordinates:[[37,55],[37,55]],altitude_start_m:0,altitude_end_m:100},
 {start:20,end:30,phase:'survey',coordinates:[[37,55],[37.01,55.01]],altitude_start_m:100,altitude_end_m:100},
 {start:30,end:40,phase:'descent',coordinates:[[37.01,55.01],[37.01,55.01]],altitude_start_m:100,altitude_end_m:0}
]};
test('waits until scheduled departure',()=>assert.deepEqual(sampleMission(mission,0),{position:[37,55],altitude:0,phase:'Ожидание старта'}));
test('interpolates climb independently of horizontal motion',()=>assert.deepEqual(sampleMission(mission,15),{position:[37,55],altitude:50,phase:'Набор высоты'}));
test('interpolates survey midpoint',()=>{const value=sampleMission(mission,25);assert.ok(Math.abs(value.position[0]-37.005)<1e-9);assert.equal(value.altitude,100);assert.equal(value.phase,'Съемка');});
test('switches phase at exact boundary',()=>assert.equal(sampleMission(mission,30).phase,'Снижение'));
test('holds final position after landing',()=>assert.deepEqual(sampleMission(mission,100),{position:[37.01,55.01],altitude:0,phase:'На земле'}));
test('shows one sortie per aircraft: in progress, next, or last',()=>{
 const first={...mission,aircraft_id:'G1',sortie:1},second={...mission,aircraft_id:'G1',sortie:2,start_s:60,finish_s:90,legs:mission.legs.map(l=>({...l,start:l.start+50,end:l.end+50}))};
 const other={...mission,aircraft_id:'G2'};
 assert.deepEqual(currentSorties([second,first,other],25).map(m=>[m.aircraft_id,m.sortie??1]),[['G1',1],['G2',1]]);
 assert.equal(currentSorties([first,second],50)[0].sortie,2);
 assert.equal(sortieState(second,50).phase,'Подготовка · рейс 2');
 assert.equal(currentSorties([first,second],500)[0].sortie,2);
});
