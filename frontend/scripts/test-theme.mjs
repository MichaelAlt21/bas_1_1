import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source=await readFile(new URL('../src/app/themeMode.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {initialTheme}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));

test('a saved choice wins over the system setting',()=>{
 assert.equal(initialTheme('light',true),'light');
 assert.equal(initialTheme('dark',false),'dark');
});
test('without a valid saved choice the system setting decides',()=>{
 assert.equal(initialTheme(null,true),'dark');
 assert.equal(initialTheme('purple',false),'light');
});
