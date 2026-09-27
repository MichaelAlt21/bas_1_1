export interface History<T> { past:T[]; present:T; future:T[] }
export type HistoryAction<T> = {type:'change';value:T}|{type:'undo'}|{type:'redo'};
export const HISTORY_LIMIT=100;
export function createHistory<T>(value:T):History<T>{return {past:[],present:structuredClone(value),future:[]};}
export function historyReducer<T>(state:History<T>,action:HistoryAction<T>):History<T>{
 if(action.type==='change'){
  if(JSON.stringify(state.present)===JSON.stringify(action.value))return state;
  return {past:[...state.past,state.present].slice(-HISTORY_LIMIT),present:structuredClone(action.value),future:[]};
 }
 if(action.type==='undo'){
  if(!state.past.length)return state;
  return {past:state.past.slice(0,-1),present:state.past.at(-1)!,future:[state.present,...state.future]};
 }
 if(!state.future.length)return state;
 return {past:[...state.past,state.present].slice(-HISTORY_LIMIT),present:state.future[0],future:state.future.slice(1)};
}

// Text controls retain their native undo; dialogs must not change the underlying map.
export function isEditingTarget(target:EventTarget|null):boolean{
 return target instanceof Element && Boolean(target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="dialog"]'));
}
