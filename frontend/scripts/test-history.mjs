import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source=await readFile(new URL('../src/features/scenarios/history.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {createHistory,historyReducer,HISTORY_LIMIT}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const change=(state,value)=>historyReducer(state,{type:'change',value});
const undo=state=>historyReducer(state,{type:'undo'});
const redo=state=>historyReducer(state,{type:'redo'});
test('moving and deleting a zone can be undone and redone in order',()=>{
 const before={zones:[{points:[[0,0],[1,0],[1,1]]}],fleet:['a']};
 const moved={...before,zones:[{points:[[0,0],[2,0],[1,1]]}]};
 let state=change(change(createHistory(before),moved),{...moved,zones:[]});
 state=undo(state);assert.deepEqual(state.present,moved);
 state=undo(state);assert.deepEqual(state.present,before);
 state=redo(state);assert.deepEqual(state.present,moved);
 state=redo(state);assert.deepEqual(state.present.zones,[]);
 assert.deepEqual(state.present.fleet,['a']);
});
test('an edit after undo discards the obsolete redo branch',()=>{
 let state=change(change(createHistory(0),1),2);
 state=change(undo(state),3);
 assert.equal(redo(state),state);
 assert.equal(undo(state).present,1);
});
test('no-op changes preserve history and snapshots do not share caller mutations',()=>{
 const original={zones:[[1,2]]};
 let state=createHistory(original);original.zones[0][0]=9;
 assert.equal(state.present.zones[0][0],1);
 const next={zones:[[3,4]]};state=change(state,next);next.zones[0][0]=8;
 assert.equal(state.present.zones[0][0],3);
 const previous=undo(state);
 assert.equal(change(previous,{zones:[[1,2]]}),previous);
 assert.equal(redo(previous).present.zones[0][0],3);
});
test('history is bounded and remains reversible at the boundary',()=>{
 let state=createHistory(0);
 for(let i=1;i<=HISTORY_LIMIT+20;i++)state=change(state,i);
 assert.equal(state.past.length,HISTORY_LIMIT);
 for(let i=0;i<HISTORY_LIMIT;i++)state=undo(state);
 assert.equal(state.present,20);assert.equal(undo(state),state);
 for(let i=0;i<HISTORY_LIMIT;i++)state=redo(state);
 assert.equal(state.present,HISTORY_LIMIT+20);assert.equal(redo(state),state);
});
