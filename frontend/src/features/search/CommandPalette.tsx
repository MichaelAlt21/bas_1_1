import { useEffect, useId, useRef, useState } from 'react';
import { searchCommands, type Command } from './commands';

/** Search over the command catalog: arrows choose, Enter opens, Esc closes and returns focus where it was. */
export default function CommandPalette({onChoose,onClose}:{onChoose:(command:Command)=>void;onClose:()=>void}){
 const [query,setQuery]=useState('');
 const [active,setActive]=useState(0);
 const input=useRef<HTMLInputElement>(null);
 const previous=useRef<Element|null>(document.activeElement);
 const listId=useId();
 const matches=searchCommands(query);
 useEffect(()=>{input.current?.focus();const back=previous.current;return()=>{if(back instanceof HTMLElement&&back.isConnected)back.focus({preventScroll:true});};},[]);
 useEffect(()=>setActive(0),[query]);
 useEffect(()=>{document.getElementById(`${listId}-${active}`)?.scrollIntoView({block:'nearest'});},[active,listId]);
 const choose=(command:Command|undefined)=>{if(!command)return;onClose();onChoose(command);};
 const onKey=(e:React.KeyboardEvent)=>{
  if(e.key==='ArrowDown'){e.preventDefault();setActive(i=>matches.length?(i+1)%matches.length:0);}
  else if(e.key==='ArrowUp'){e.preventDefault();setActive(i=>matches.length?(i-1+matches.length)%matches.length:0);}
  else if(e.key==='Enter'){e.preventDefault();choose(matches[active]?.command);}
  else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();onClose();}
  else if(e.key==='Tab'){e.preventDefault();}
 };
 return <div className="palette-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}>
  <div className="palette" role="dialog" aria-modal="true" aria-label="Поиск по разделам, настройкам и действиям" onKeyDown={onKey}>
   <input ref={input} className="palette-input" role="combobox" aria-expanded={matches.length>0} aria-controls={listId} aria-autocomplete="list"
    aria-activedescendant={matches.length?`${listId}-${active}`:undefined} placeholder="Раздел, настройка или действие: «ветер», «резерв», «матрица»…" value={query} onChange={e=>setQuery(e.target.value)}/>
   {matches.length?<ul id={listId} role="listbox" className="palette-list" aria-label="Результаты поиска">
    {matches.map(({command},i)=><li key={command.id} id={`${listId}-${i}`} role="option" aria-selected={i===active} className={i===active?'active':''}
     onMouseEnter={()=>setActive(i)} onMouseDown={e=>{e.preventDefault();choose(command);}}>
     <span className="palette-title">{command.title}</span>
     <span className="palette-path"><b>{command.category}</b>{command.path.length?` · ${command.path.join(' › ')}`:''}</span>
     {command.note&&<small className="palette-note">{command.note}</small>}
    </li>)}
   </ul>:<p className="palette-empty" role="status">Ничего не найдено по «{query}». Попробуйте другое слово: «площадка», «камера», «срок».</p>}
   <div className="palette-help" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> выбор · <kbd>Enter</kbd> перейти · <kbd>Esc</kbd> закрыть</div>
  </div>
 </div>;
}
