import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source=await readFile(new URL('../src/features/scenarios/levels.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {bankDeg,echelonLevels,aircraftAltitude}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));

test('levels go up from the survey altitude in steps and stop at the ceiling',()=>{
 assert.deepEqual(echelonLevels(100,20,150),[100,120,140]);
 assert.deepEqual(echelonLevels(60,30,150),[60,90,120,150]);
});
test('a survey altitude above the ceiling leaves a single level',()=>{
 assert.deepEqual(echelonLevels(200,20,150),[200]);
});
test('bank angle of the default 201 turn in still air',()=>{
 assert.ok(Math.abs(bankDeg(150,22)-18.2)<.1);
});
test('an aircraft keeps its own level, otherwise the survey altitude',()=>{
 assert.equal(aircraftAltitude({altitude_m:120},100),120);
 assert.equal(aircraftAltitude({altitude_m:null},100),100);
 assert.equal(aircraftAltitude({},100),100);
});
