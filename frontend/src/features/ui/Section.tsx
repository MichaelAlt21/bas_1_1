import type { ReactNode } from 'react';

/** Collapsible sidebar block; the summary line carries the current values so a closed block still informs. */
export default function Section({title,meta,defaultOpen=true,children}:{title:string;meta?:ReactNode;defaultOpen?:boolean;children:ReactNode}){
 return <details className="panel-section" open={defaultOpen}>
  <summary><span className="panel-title">{title}</span>{meta!==undefined&&meta!==null&&meta!==''&&<span className="panel-meta">{meta}</span>}</summary>
  <div className="panel-body">{children}</div>
 </details>;
}
