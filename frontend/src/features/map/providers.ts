import OSM from 'ol/source/OSM';
import TileJSON from 'ol/source/TileJSON';
import XYZ from 'ol/source/XYZ';
import { allowedMapUrl, escapeAttribution, mapUrl, type UserMap } from './mapConfig';

export type Basemap = 'osm' | 'satellite';
const key = import.meta.env.VITE_MAPTILER_KEY?.trim();
export const hasMapTilerKey = Boolean(key);

export function createSource(id: Basemap) {
  if (id === 'osm') return new OSM();
  if (!key) throw new Error('Для выбранной подложки нужен ключ MapTiler');
  const mapId = import.meta.env.VITE_MAPTILER_SATELLITE_ID || 'hybrid-v4';
  // Use the rendered Maps API, as with the previous Streets layer, not raw Tiles API.
  const path = `maps/${encodeURIComponent(mapId)}/256`;
  // TileJSON carries provider attribution and supported zoom levels.
  return new TileJSON({ url: `https://api.maptiler.com/${path}/tiles.json?key=${encodeURIComponent(key)}`, crossOrigin: 'anonymous' });
}

export async function createUserSource(map:UserMap,signal:AbortSignal){
 const url=mapUrl(map);
 const attribution=escapeAttribution(map.attribution);
 if(map.kind==='xyz')return new XYZ({url,attributions:attribution,crossOrigin:'anonymous',minZoom:map.minZoom,maxZoom:map.maxZoom,tileSize:256});
 const response=await fetch(url,{signal,credentials:'omit',referrerPolicy:'no-referrer'});
 if(!response.ok)throw new Error('Провайдер отклонил TileJSON. Проверьте ключ и доступ к карте.');
 const text=await response.text();
 if(text.length>1_000_000)throw new Error('Слишком большое описание TileJSON.');
 let data;
 try{data=JSON.parse(text);}catch{throw new Error('Вместо TileJSON получен другой формат.');}
 if(!data||typeof data!=='object'||!Array.isArray(data.tiles)||!data.tiles.length||data.tiles.length>8||data.vector_layers||data.scheme==='tms')throw new Error('Нужен растровый TileJSON с XYZ-тайлами. Векторный стиль и TMS не поддерживаются.');
 const urls=data.tiles.map((tile:unknown)=>{
  if(typeof tile!=='string'||!['{z}','{x}','{y}'].every(token=>tile.includes(token)))throw new Error('В TileJSON нет корректного XYZ-шаблона.');
  allowedMapUrl(tile);return tile;
 });
 const minZoom=Math.max(map.minZoom,Number.isInteger(data.minzoom)?data.minzoom:0);
 const maxZoom=Math.min(map.maxZoom,Number.isInteger(data.maxzoom)?data.maxzoom:24);
 if(minZoom>maxZoom)throw new Error('Выбранные масштабы вне диапазона провайдера.');
 // Remote HTML attribution is never rendered. The user supplies plain attribution text.
 return new XYZ({urls,attributions:attribution,crossOrigin:'anonymous',minZoom,maxZoom,tileSize:256});
}
