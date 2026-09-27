export interface UserMap {
 id:string; name:string; kind:'maptiler'|'xyz'|'tilejson'; url:string; mapId:string;
 apiKey:string; attribution:string; minZoom:number; maxZoom:number; remember:boolean;
}
export const MAP_STORAGE_KEY='kumosystems.maps.v1';
export const newMap=():UserMap=>({id:'',name:'',kind:'maptiler',url:'',mapId:'hybrid-v4',apiKey:'',attribution:'© MapTiler © OpenStreetMap contributors',minZoom:0,maxZoom:20,remember:false});

export function allowedMapUrl(value:string){
 const sample=value.replace(/\{(?:z|x|y|key)\}/g,'0');
 const parsed=new URL(sample);
 if(parsed.protocol!=='https:'&&!(parsed.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(parsed.hostname)))throw new Error('Используйте HTTPS; HTTP поддерживается только для localhost.');
 if(parsed.username||parsed.password||parsed.hash)throw new Error('Уберите логин, пароль и фрагмент # из URL карты.');
 return parsed;
}
export function validateMap(input:UserMap):UserMap {
 const value={...input,name:input.name.trim(),url:input.url.trim(),apiKey:input.apiKey.trim(),mapId:input.mapId.trim(),attribution:input.attribution.trim()};
 if(!['maptiler','xyz','tilejson'].includes(value.kind))throw new Error('Неизвестный формат подложки.');
 if(!value.name||value.name.length>100)throw new Error('Укажите название длиной до 100 символов.');
 if(!value.attribution||value.attribution.length>500)throw new Error('Укажите текст атрибуции провайдера (до 500 символов).');
 if(value.apiKey.length>1000||value.url.length>3000)throw new Error('Слишком длинный ключ или URL.');
 if(!Number.isInteger(value.minZoom)||!Number.isInteger(value.maxZoom)||value.minZoom<0||value.maxZoom>24||value.maxZoom<value.minZoom)throw new Error('Масштабы должны быть целыми от 0 до 24; максимум не меньше минимума.');
 if(value.kind==='maptiler'){
  if(!/^[a-zA-Z0-9_-]{1,100}$/.test(value.mapId))throw new Error('Укажите ID стиля MapTiler, например hybrid-v4, а не ссылку.');
  if(!value.apiKey||/\s|:\/\//.test(value.apiKey))throw new Error('В поле ключа нужен сам ключ MapTiler, без ссылки.');
 }else{
  allowedMapUrl(value.url);
  if(value.kind==='xyz'&&!['{z}','{x}','{y}'].every(token=>value.url.includes(token)))throw new Error('В XYZ-ссылке обязательны {z}, {x} и {y}.');
  if(value.kind==='tilejson'&&/\{[zxy]\}/.test(value.url))throw new Error('Для TileJSON нужен адрес JSON-описания, без {z}/{x}/{y}.');
  if(/\{(?!z\}|x\}|y\}|key\})/.test(value.url))throw new Error('Поддерживаются только {z}, {x}, {y} и {key}.');
  if(value.url.includes('{key}')&&!value.apiKey)throw new Error('Укажите ключ для шаблона {key}.');
 }
 return value;
}
export function mapUrl(map:UserMap){
 const value=validateMap(map);
 return value.kind==='maptiler'?`https://api.maptiler.com/maps/${encodeURIComponent(value.mapId)}/256/tiles.json?key=${encodeURIComponent(value.apiKey)}`:value.url.replaceAll('{key}',encodeURIComponent(value.apiKey));
}
export function escapeAttribution(value:string){return value.replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!));}
export function serializeMaps(maps:UserMap[]){return JSON.stringify(maps.filter(m=>m.remember).map(validateMap));}
export function deserializeMaps(raw:string|null):UserMap[]{
 try{const data=JSON.parse(raw||'[]');if(!Array.isArray(data)||data.length>20)return [];const ids=new Set<string>();return data.filter(value=>{
  try{if(typeof value.id!=='string'||!value.id.startsWith('custom_')||ids.has(value.id)||value.remember!==true)return false;validateMap(value);ids.add(value.id);return true;}catch{return false;}
 }).map(validateMap);}catch{return [];}
}
