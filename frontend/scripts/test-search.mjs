import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source=await readFile(new URL('../src/features/search/commands.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {COMMANDS,searchCommands}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const ids=q=>searchCommands(q).map(m=>m.command.id);

test('example queries find the expected place first or near the top',()=>{
 const expected={'ветер':'weather','аккумулятор':'reserve','резерв':'reserve','камера':'sensors','матрица':'cost-matrix','стоимость':'cost','площадка':'sites','база данных':'database','экспорт':'export'};
 for(const [query,id] of Object.entries(expected)){
  const found=ids(query);
  assert.ok(found.slice(0,3).includes(id),`${query}: ${found.slice(0,5).join(', ')}`);
 }
});
test('partial words, other endings, ё and synonyms match',()=>{
 assert.ok(ids('аккум').includes('reserve'));
 assert.ok(ids('площадки').includes('sites'));
 assert.ok(ids('АКБ').includes('sorties'));
 assert.ok(ids('бд').includes('database'));
 assert.ok(ids('тепловизор').includes('sensors'));
 assert.ok(ids('матрица').includes('sensors'),'the camera sensor matrix is found too, in its own category');
});
test('several words narrow the result; unknown words give nothing',()=>{
 assert.equal(ids('резерв заряда')[0],'reserve');
 assert.deepEqual(ids('кофемашина'),[]);
});
test('entries are unique and every one leads somewhere; dangerous actions only navigate',()=>{
 assert.equal(new Set(COMMANDS.map(c=>c.id)).size,COMMANDS.length);
 for(const c of COMMANDS){
  assert.ok(c.target||c.action,c.id);
  if(c.action)assert.ok(['archive','database','theme'].includes(c.action),c.id);
 }
 for(const id of ['calculate','compare','save','export','demos'])assert.ok(!COMMANDS.find(c=>c.id===id).action,id);
});
test('an empty query lists sections and actions without individual settings',()=>{
 const all=searchCommands('');
 assert.ok(all.length>0&&all.every(m=>m.command.category!=='Настройка'));
});
