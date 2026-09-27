export type ThemeMode='light'|'dark';
export const THEME_KEY='kumo.theme';

/** The saved choice wins; without one the page follows the system setting. */
export function initialTheme(stored:string|null,prefersDark:boolean):ThemeMode{
 return stored==='light'||stored==='dark'?stored:prefersDark?'dark':'light';
}

// Storage can be unavailable (private mode, blocked site data): the theme then lasts for the session.
export function readTheme():ThemeMode{
 let stored:string|null=null;
 try{stored=localStorage.getItem(THEME_KEY);}catch{/* no storage */}
 const prefersDark=typeof matchMedia==='function'&&matchMedia('(prefers-color-scheme: dark)').matches;
 return initialTheme(stored,prefersDark);
}

export function saveTheme(mode:ThemeMode){
 try{localStorage.setItem(THEME_KEY,mode);}catch{/* no storage */}
}

export function applyTheme(mode:ThemeMode){
 document.documentElement.dataset.theme=mode;
}
