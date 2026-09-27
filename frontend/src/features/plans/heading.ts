import type { Mission, Position } from '../../shared/types';

// Clockwise from north in the map's Web Mercator plane, matching the drawn segment.
export function segmentHeading(a:Position,b:Position):number|null{
 const dx=((b[0]-a[0]+540)%360-180)*Math.PI/180;
 const mercator=(lat:number)=>Math.log(Math.tan(Math.PI/4+Math.max(-85,Math.min(85,lat))*Math.PI/360));
 const dy=mercator(b[1])-mercator(a[1]);
 if(Math.hypot(dx,dy)<1e-12)return null;
 return (Math.atan2(dx,dy)+2*Math.PI)%(2*Math.PI);
}

export function missionHeading(mission:Mission,time:number):number{
 let previous:number|null=null;
 let upcoming:number|null=null;
 for(const leg of mission.legs){
  const direction=segmentHeading(leg.coordinates[0],leg.coordinates[leg.coordinates.length-1]);
  if(direction===null)continue;
  if(leg.start<=time)previous=direction;
  else if(upcoming===null)upcoming=direction;
 }
 return previous??upcoming??0;
}
