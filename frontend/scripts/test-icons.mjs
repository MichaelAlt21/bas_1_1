import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source=await readFile(new URL('../src/features/map/iconSvg.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {recolorIcon,MAP_ICON,UI_ICON}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const artwork=await Promise.all(['gemini','geoscan_801','geoscan_201'].map(async name=>[name,await readFile(new URL(`../src/features/map/icons/${name}.svg`,import.meta.url),'utf8')]));
const decode=uri=>{assert.ok(uri.startsWith('data:image/svg+xml;charset=utf-8,'));return decodeURIComponent(uri.slice(uri.indexOf(',')+1));};

test('every shipped icon recolors into a self-contained SVG with the mission color and a dark casing',()=>{
 for(const [name,svg] of artwork){
  const out=decode(recolorIcon(svg,'#69d3c6',MAP_ICON));
  assert.match(out,/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" width="48" height="\d+"/,name);
  assert.equal((out.match(/<use /g)||[]).length,2,name);
  assert.ok(out.includes('fill="#69d3c6"')&&out.includes('fill="#08131c"'),name);
  assert.ok(!/<!DOCTYPE|<\?xml|<script|href="http/i.test(out),name);
 }
});
test('the casing widens the viewBox so the outline is not clipped at the artwork edges',()=>{
 const [,svg]=artwork[0];
 const [x,y,w]=decode(recolorIcon(svg,'#69d3c6',MAP_ICON)).match(/viewBox="([^"]+)"/)[1].split(' ').map(Number);
 const original=Number(svg.match(/viewBox="([^"]+)"/)[1].split(/\s+/)[2]);
 assert.ok(x<0&&y<0&&w>original);
});
test('UI icons have no casing layer',()=>{
 const out=decode(recolorIcon(artwork[1][1],'#d3a769',UI_ICON));
 assert.equal((out.match(/<use /g)||[]).length,1);
 assert.ok(!out.includes('#08131c'));
});
test('a malformed color from a stored plan cannot inject markup',()=>{
 const out=decode(recolorIcon(artwork[0][1],'red"/><script>alert(1)</script>',MAP_ICON));
 assert.ok(!out.includes('<script')&&out.includes('fill="#94a3b8"'));
});
test('unrecognised artwork is rejected instead of rendering nothing',()=>{
 assert.throws(()=>recolorIcon('<svg viewBox="0 0 10 10"><circle r="4"/></svg>','#69d3c6',MAP_ICON));
});
