import { createContext } from 'react';
import type { NavTarget } from '../../shared/types';

/** A request to show a place of the interface: tab, section, field and map object. A new revision repeats it. */
export type NavRequest=NavTarget&{revision:number};
export const NavContext=createContext<NavRequest|null>(null);

const HIGHLIGHT_MS=2600;
/** Scroll to the field (or section) of a navigation request, focus it and highlight it for a moment. */
export function reveal(request:NavRequest){
 const root=document;
 const byNav=request.field?root.querySelector<HTMLElement>(`[data-nav="${CSS.escape(request.field)}"]`):null;
 const element=byNav||(request.section?root.querySelector<HTMLElement>(`[data-section="${CSS.escape(request.section)}"]`):null);
 if(!element)return false;
 element.closest('details')?.setAttribute('open','');
 element.scrollIntoView({block:'center',behavior:'smooth'});
 const focusable=element.matches('input,select,textarea,button')?element:element.querySelector<HTMLElement>('input,select,textarea,button,summary');
 focusable?.focus({preventScroll:true});
 element.classList.remove('nav-highlight');void element.offsetWidth;element.classList.add('nav-highlight');
 window.setTimeout(()=>element.classList.remove('nav-highlight'),HIGHLIGHT_MS);
 return Boolean(byNav)||!request.field;
}
