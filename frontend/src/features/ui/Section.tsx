import { useContext, useEffect, useRef, type ReactNode } from 'react';
import { NavContext } from './navigation';

/** Collapsible sidebar block; the summary line carries the current values so a closed block still informs. A navigation request to its id opens it. */
export default function Section({id,title,meta,defaultOpen=true,children}:{id?:string;title:string;meta?:ReactNode;defaultOpen?:boolean;children:ReactNode}){
 const request=useContext(NavContext);
 const element=useRef<HTMLDetailsElement>(null);
 useEffect(()=>{if(id&&request?.section===id&&element.current)element.current.open=true;},[request?.revision]);
 return <details ref={element} className="panel-section" open={defaultOpen} data-section={id}>
  <summary><span className="panel-title">{title}</span>{meta!==undefined&&meta!==null&&meta!==''&&<span className="panel-meta">{meta}</span>}</summary>
  <div className="panel-body">{children}</div>
 </details>;
}
