export interface IconStyle { size:number; line:number; casing:number }

// Line weights are in the artwork's path units (potrace output scaled by 0.1 into the viewBox).
// At map size the supplied line-art is thinner than a pixel, so every line is widened
// in the mission color and, for the map, cased in a dark outline to stay legible on any basemap.
export const MAP_ICON:IconStyle={size:48,line:450,casing:1000};
export const UI_ICON:IconStyle={size:22,line:700,casing:0};
const CASING_COLOR='#08131c';
const FALLBACK_COLOR='#94a3b8';

export function recolorIcon(svg:string,color:string,{size,line,casing}:IconStyle):string{
 const viewBox=svg.match(/viewBox="([^"]+)"/)?.[1].trim().split(/[\s,]+/).map(Number);
 const group=svg.match(/<g\b([^>]*)>([\s\S]*)<\/g>/);
 const transform=group?.[1].match(/transform="([^"]*)"/)?.[1];
 if(!viewBox||viewBox.length!==4||viewBox.some(v=>!Number.isFinite(v))||!group||!transform)throw new Error('Unsupported aircraft icon artwork');
 // Mission colors come from saved plans; never let one inject markup into the SVG.
 const fill=/^#[0-9a-f]{6}$/i.test(color)?color:FALLBACK_COLOR;
 const margin=Math.max(line,casing)*0.06;
 const [x,y,w,h]=[viewBox[0]-margin,viewBox[1]-margin,viewBox[2]+2*margin,viewBox[3]+2*margin];
 const use=(paint:string,width:number)=>`<use href="#a" fill="${paint}" stroke="${paint}" stroke-width="${width}" stroke-linejoin="round" stroke-linecap="round"/>`;
 const markup=`<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${Math.round(size*h/w)}" viewBox="${x} ${y} ${w} ${h}"><defs><g id="a" transform="${transform}">${group[2]}</g></defs>${casing?use(CASING_COLOR,casing):''}${use(fill,line)}</svg>`;
 return 'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(markup);
}
