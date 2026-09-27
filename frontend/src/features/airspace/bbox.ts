import type { Polygon } from 'geojson';
import type { Position } from '../../shared/types';

export type BBox=[number,number,number,number];

/** West, south, east, north of the polygons and points, grown by `marginKm` on every side. */
export function bboxOf(polygons:Polygon[],points:Position[]=[],marginKm=0):BBox|null{
 const all=[...polygons.flatMap(p=>p.coordinates[0]),...points];
 if(!all.length)return null;
 const lats=all.map(p=>p[1]);
 const south=Math.min(...lats),north=Math.max(...lats);
 const dLat=marginKm/110.54,dLon=marginKm/(111.32*Math.max(Math.cos((Math.max(Math.abs(south),Math.abs(north)))*Math.PI/180),.1));
 return [Math.max(-180,Math.min(...all.map(p=>p[0]))-dLon),Math.max(-90,south-dLat),Math.min(180,Math.max(...all.map(p=>p[0]))+dLon),Math.min(90,north+dLat)];
}

export function bboxPolygon([w,s,e,n]:BBox):Polygon{
 return {type:'Polygon',coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]};
}

export function contains([w,s,e,n]:BBox,[x,y]:Position){
 return x>=w&&x<=e&&y>=s&&y<=n;
}

/** Extent in kilometres (east-west, north-south). */
export function extentKm([w,s,e,n]:BBox):[number,number]{
 return [(e-w)*111.32*Math.cos((s+n)/2*Math.PI/180),(n-s)*110.54];
}
