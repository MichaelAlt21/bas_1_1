import { useState } from 'react';
import { api } from '../../shared/api';
import type { DatabaseConfig, DatabaseReport, DatabaseStatus } from '../../shared/databaseTypes';

const defaults:DatabaseConfig={host:'',port:5432,database:'',username:'',password_env:'KUMO_DB_PASSWORD',sslmode:'verify-full',sslrootcert:''};
export default function DatabaseSettings({onClose}:{onClose:()=>void}){
 const [token,setToken]=useState(''),[password,setPassword]=useState(''),[config,setConfig]=useState(defaults);
 const [status,setStatus]=useState<DatabaseStatus|null>(null),[report,setReport]=useState<DatabaseReport|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[allowPrepare,setAllowPrepare]=useState(false);
 const load=async()=>{setBusy(true);setError('');try{const value=await api.databaseStatus(token);setStatus(value);if(value.saved)setConfig(value.saved);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
 const change=(next:DatabaseConfig)=>{setConfig(next);setReport(null);setAllowPrepare(false);setError('');};
 const act=async(action:'check'|'prepare'|'save'|'local')=>{
  setBusy(true);setError('');setReport(null);
  try{const value=await api.databaseAction(action,token,{config,...(password&&(action==='check'||action==='prepare')?{test_password:password}:{})});setReport(value);if(action==='save'||action==='local'){setPassword('');setStatus(await api.databaseStatus(token));}}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 };
 return <section className="connection-panel database-panel" aria-label="База данных компании"><div className="row between"><h2>База данных компании</h2><button disabled={busy} onClick={onClose} aria-label="Закрыть настройки БД">×</button></div>
 <p className="muted">PostgreSQL хранит сценарии, пользовательские типы и планы всей команды. Изменение подключения применяется после перезапуска API.</p>
 <fieldset disabled={busy}><label>Административный токен сервера<input type="password" autoComplete="off" value={token} onChange={e=>{setToken(e.target.value);setStatus(null);setReport(null);}}/></label>
 <p className="muted">Токен находится на сервере в backend/.runtime/admin-token либо задается через KUMO_ADMIN_TOKEN. Его можно получить локальной командой из инструкции. В браузере токен не сохраняется.</p>
 <button disabled={!token} onClick={()=>void load()}>Загрузить настройки БД</button></fieldset>
 {status&&<><div className="connection-summary"><strong>Активное хранилище</strong><p>{status.active.driver==='sqlite'?`SQLite · ${status.active.database}`:`PostgreSQL · ${status.active.host}:${status.active.port||5432} / ${status.active.database} / ${status.active.schema}`}</p><small>{status.saved?`Сохранена конфигурация: ${status.saved.host}:${status.saved.port} / ${status.saved.database}. Проверяйте активное хранилище после перезапуска.`:'Сохраненной конфигурации PostgreSQL нет.'}</small></div>
 {status.overridden_by_env&&<p className="note">Подключение задано переменной BAS_DATABASE_URL на сервере. Она имеет приоритет: сохранение настроек и возврат к SQLite доступны после ее удаления и перезапуска.</p>}
 <form onSubmit={e=>{e.preventDefault();void act('check');}}><fieldset disabled={busy}>
 <div className="row"><label>Сервер PostgreSQL<input required maxLength={253} placeholder="postgres.company.example" value={config.host} onChange={e=>change({...config,host:e.target.value})}/></label><label>Порт<input required type="number" min={1} max={65535} value={config.port} onChange={e=>change({...config,port:e.target.valueAsNumber})}/></label></div>
 <label>Имя базы данных<input required maxLength={100} value={config.database} onChange={e=>change({...config,database:e.target.value})}/></label>
 <label>Пользователь БД<input required maxLength={100} autoComplete="off" value={config.username} onChange={e=>change({...config,username:e.target.value})}/></label>
 <label>Пароль для проверки (не сохраняется)<input type="password" autoComplete="new-password" value={password} onChange={e=>{setPassword(e.target.value);setReport(null);setAllowPrepare(false);}}/></label>
 <label>Переменная сервера с рабочим паролем<input required value={config.password_env} onChange={e=>change({...config,password_env:e.target.value})}/></label>
 <p className="note">Для сохранения подключения администратор должен задать рабочий пароль в этой переменной окружения сервера. В файле настроек хранится только ее имя. Пароль формы используется лишь при проверке и подготовке схемы.</p>
 <label>TLS<select value={config.sslmode} onChange={e=>change({...config,sslmode:e.target.value as DatabaseConfig['sslmode']})}><option value="verify-full">Проверять сертификат и имя сервера</option><option value="require">Требовать шифрование (без проверки имени)</option></select></label>
 <label>Путь к корневому сертификату на сервере (если нужен)<input value={config.sslrootcert} onChange={e=>change({...config,sslrootcert:e.target.value})}/></label>
 <p className="muted">Приложение использует отдельную схему kumosystems. Таблицы компании вне нее не изменяются. Проверка соединения выполняет чтение метаданных.</p>
 <button type="submit">Проверить соединение и схему</button>
 <label className="check-row"><input type="checkbox" checked={allowPrepare} onChange={e=>setAllowPrepare(e.target.checked)}/> Создать отсутствующую схему kumosystems и таблицы приложения в указанной БД</label>
 <button type="button" disabled={!allowPrepare||!report?.connected||Boolean(report?.incompatible_tables?.length)} onClick={()=>void act('prepare')}>Подготовить схему</button>
 <button className="primary full" type="button" disabled={!report?.tables_ready||!report.writable||status.overridden_by_env} onClick={()=>void act('save')}>Сохранить для следующего запуска</button>
 </fieldset></form>
 <p className="note">Переключение не переносит данные: в новой БД будут доступны ее собственные записи. Локальная SQLite останется на месте. Для переноса сценариев доступны экспорт и импорт JSON; массовая миграция планов пока не реализована.</p>
 <button disabled={busy||status.overridden_by_env||(!status.saved&&status.active.driver==='sqlite')} onClick={()=>void act('local')}>Использовать SQLite после перезапуска</button>
 </>}
 {error&&<p role="alert" className="error">{error}</p>}
 {report&&<div role="status" className="connection-summary">{report.message||<><strong>Соединение установлено</strong><p>{report.tables_ready?'Таблицы совместимы.':`Не хватает таблиц: ${report.missing_tables?.join(', ')||'нет'}.`}</p>{Boolean(report.incompatible_tables?.length)&&<p className="error">Несовместимы: {report.incompatible_tables?.join(', ')}. Автоматически не изменяются.</p>}<p>{report.writable?'Права SELECT и INSERT доступны.':'Готовность записи не подтверждена.'}</p></>}</div>}
 {busy&&<p role="status" className="muted">Проверка сервера…</p>}
 </section>;
}
