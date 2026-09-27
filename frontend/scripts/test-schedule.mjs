import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source=await readFile(new URL('../src/features/plans/schedule.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {scheduleRows,tickStep,timeOfDay}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const base={profile_id:'gemini',color:'#1fc1ad',site_id:'a',legs:[]};

test('charging sortie splits the ground time into handling, queue and charging',()=>{
 const first={...base,aircraft_id:'G1',sortie:1,start_s:0,finish_s:1200};
 const second={...base,aircraft_id:'G1',sortie:2,start_s:6000,finish_s:7200,charge_start_s:1800,charge_end_s:5800,charge_wait_s:300};
 const [row]=scheduleRows([second,first]);
 assert.deepEqual(row.segments.map(s=>[s.kind,s.start,s.end]),[['flight',0,1200],['handling',1200,1500],['waiting',1500,1800],['charging',1800,5800],['flight',6000,7200]]);
});

test('battery swap shows as one ground block and aircraft are sorted naturally',()=>{
 const rows=scheduleRows([{...base,aircraft_id:'G10',sortie:1,start_s:0,finish_s:10},{...base,aircraft_id:'G2',sortie:1,start_s:0,finish_s:600},{...base,aircraft_id:'G2',sortie:2,start_s:1600,finish_s:2000,ground_s:900}]);
 assert.deepEqual(rows.map(r=>r.aircraftId),['G2','G10']);
 assert.deepEqual(rows[0].segments.map(s=>s.kind),['flight','swap','flight']);
});

test('ticks stay readable over minutes and hours',()=>{
 assert.equal(tickStep(1800),300);
 assert.equal(tickStep(12*3600),7200);
});

test('clock times are relative without an operation start',()=>{
 assert.equal(timeOfDay(3720),'+1:02');
 assert.match(timeOfDay(3600,'2026-09-25T07:00:00+03:00'),/^\d\d:\d\d$/);
});
