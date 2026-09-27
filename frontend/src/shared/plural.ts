/** Russian noun form for a count: plural(3, 'участок', 'участка', 'участков') → '3 участка'. */
export function plural(n:number,one:string,few:string,many:string){
 const tens=n%100,units=n%10;
 const word=tens>=11&&tens<=14?many:units===1?one:units>=2&&units<=4?few:many;
 return `${n} ${word}`;
}
export const plots=(n:number)=>plural(n,'участок','участка','участков');
