import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
async function load(path){
 const source=await readFile(new URL(path,import.meta.url),'utf8');
 const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
 return import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
}
const {segmentHeading,missionHeading}=await load('../src/features/plans/heading.ts');
const {surveyAreas,withSurveyAreas}=await load('../src/features/scenarios/areas.ts');
const close=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-8,`${actual} != ${expected}`);
test('map heading follows all cardinal directions',()=>{
 close(segmentHeading([37,55],[37,56]),0);
 close(segmentHeading([37,55],[38,55]),Math.PI/2);
 close(segmentHeading([37,55],[37,54]),Math.PI);
 close(segmentHeading([37,55],[36,55]),3*Math.PI/2);
 assert.equal(segmentHeading([37,55],[37,55]),null);
});
test('heading holds on vertical stages and after landing; scrubbing backward is deterministic',()=>{
 const mission={legs:[{start:0,end:10,coordinates:[[37,55],[37,55]]},{start:10,end:20,coordinates:[[37,55],[38,55]]},{start:20,end:30,coordinates:[[38,55],[38,55]]},{start:30,end:40,coordinates:[[38,55],[38,56]]},{start:40,end:50,coordinates:[[38,56],[38,56]]}]};
 for(const t of [-5,0,15,25])close(missionHeading(mission,t),Math.PI/2);
 for(const t of [30,35,45,100])close(missionHeading(mission,t),0);
 close(missionHeading(mission,15),Math.PI/2);
});
test('legacy single area, adding and removing the primary polygon preserve all other areas',()=>{
 const a={type:'Polygon',coordinates:[[[0,0],[1,0],[1,1],[0,0]]]},b={type:'Polygon',coordinates:[[[2,0],[3,0],[3,1],[2,0]]]};
 const legacy={survey_area:a,name:'Legacy'};
 assert.deepEqual(surveyAreas(legacy),[a]);
 const multiple=withSurveyAreas(legacy,[a,b]);
 assert.deepEqual(surveyAreas(multiple),[a,b]);
 const removed=withSurveyAreas(multiple,surveyAreas(multiple).slice(1));
 assert.deepEqual(removed.survey_area,b);
 assert.deepEqual(removed.additional_survey_areas,[]);
 assert.deepEqual(surveyAreas(withSurveyAreas(removed,[])),[]);
 assert.equal(legacy.survey_area,a);
});
