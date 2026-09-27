/** Flight levels and turn geometry shown in the settings; the planner decides the actual levels. */
export const LEGAL_CEILING_M=150;
const GRAVITY=9.80665;

/** Bank of a coordinated turn of `radius` metres at `airspeed` m/s in still air, degrees. */
export function bankDeg(radius:number,airspeed:number){
 return Math.atan(airspeed**2/(GRAVITY*radius))*180/Math.PI;
}

/** Levels the automatic echelons may use: from the survey altitude up in steps, not above the ceiling. */
export function echelonLevels(altitude:number,step:number,ceiling:number){
 if(!(step>0)||altitude>ceiling)return [altitude];
 const levels:number[]=[];
 for(let level=altitude;level<=Math.min(ceiling,300)+1e-9;level+=step)levels.push(Math.round(level*10)/10);
 return levels;
}

/** The level an aircraft flies at unless the planner raises it: its own, else the survey altitude. */
export function aircraftAltitude(aircraft:{altitude_m?:number|null},altitude:number){
 return aircraft.altitude_m??altitude;
}
