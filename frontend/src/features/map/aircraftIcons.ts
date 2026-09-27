import gemini from './icons/gemini.svg?raw';
import geoscan801 from './icons/geoscan_801.svg?raw';
import geoscan201 from './icons/geoscan_201.svg?raw';
import { MAP_ICON, recolorIcon, type IconStyle } from './iconSvg';

const ARTWORK:Record<string,string>={gemini,geoscan_801:geoscan801,geoscan_201:geoscan201};
const cache=new Map<string,string>();

// User-created profiles have no artwork of their own and reuse the Gemini silhouette.
export function aircraftIcon(profileId:string,color:string,style:IconStyle=MAP_ICON):string{
 const id=profileId in ARTWORK?profileId:'gemini';
 const key=`${id}|${color}|${style.size}|${style.line}|${style.casing}`;
 let src=cache.get(key);
 if(!src){src=recolorIcon(ARTWORK[id],color,style);cache.set(key,src);}
 return src;
}
