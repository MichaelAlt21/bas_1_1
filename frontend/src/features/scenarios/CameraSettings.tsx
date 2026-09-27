import type {AircraftProfile, Draft, FixedWingSettings} from '../../shared/types';
import { bankDeg } from './levels';
export const DEFAULT_FIXED_WING:FixedWingSettings={airspeed_mps:22,turn_radius_m:150,climb_rate_mps:2.5,parachute_sink_mps:5,launch_heading_deg:null,lead_in_m:50,parachute_release_m:null};
export default function CameraSettings({draft,catalog,onChange}:{draft:Draft;catalog:AircraftProfile[];onChange:(draft:Draft)=>void}){
 const settings=draft.settings;
 const mode=settings.coverage_mode||'manual';
 const fixed={...DEFAULT_FIXED_WING,...settings.fixed_wing};
 const hasPlane=draft.fleet.some(a=>catalog.some(p=>p.id===a.profile_id&&p.airframe==='fixed_wing'));
 const cameraCapable=draft.survey_type==='rgb'||draft.survey_type==='multispectral';
 const number=(label:string,value:number,min:number,max:number,onChange:(value:number)=>void,step=1)=><label>{label}<input type="number" value={value} min={min} max={max} step={step} onChange={e=>onChange(e.target.valueAsNumber||0)}/></label>;
 const update=(patch:Partial<typeof settings>)=>onChange({...draft,settings:{...settings,...patch}});
 return <>
  <label>Расчет покрытия<select value={mode} onChange={e=>{
   const coverage_mode=e.target.value as 'manual'|'camera';
   const defaultCamera=(a:typeof draft.fleet[number])=>a.payload==='rgb'?(a.profile_id==='geoscan_201'?'sony_rx1rm2_v1':a.profile_id==='gemini'?'geoscan_pf1b_v1':null):a.payload==='multispectral'?(a.profile_id==='gemini'||a.profile_id==='geoscan_201'?'geoscan_pollux_v1':null):null;
   onChange({...draft,settings:{...settings,coverage_mode,max_gsd_cm:coverage_mode==='manual'?null:settings.max_gsd_cm},fleet:draft.fleet.map(a=>({...a,camera_id:a.camera_id||defaultCamera(a)}))});
  }}><option value="manual">Ручная ширина захвата</option>{cameraCapable&&<option value="camera">По камерам · RGB и мультиспектральная, надир</option>}</select></label>
  {!cameraCapable&&<p className="note">Для выбранного типа съемки подтвержденного профиля камеры пока нет: доступна только ручная ширина захвата.</p>}
  <div className="row">{number('Высота съемки AGL, м',settings.altitude_m,hasPlane?100:20,300,v=>update({altitude_m:v}))}{number('Скорость коптеров по умолчанию, м/с',settings.airspeed_mps,2,15,v=>update({airspeed_mps:v}))}</div>
  <div className="row">{mode==='manual'&&number('Ширина захвата, м',settings.swath_width_m,10,500,v=>update({swath_width_m:v}))}{number('Боковое перекрытие, %',settings.side_overlap_percent,0,80,v=>update({side_overlap_percent:v}))}</div>
  {mode==='camera'?<>
   {number('Продольное перекрытие, %',settings.front_overlap_percent??70,0,90,v=>update({front_overlap_percent:v}))}
   <label>Максимальный GSD, см/пиксель (необязательно)<input type="number" min={.1} max={100} step={.1} placeholder="Без ограничения" value={settings.max_gsd_cm??''} onChange={e=>update({max_gsd_cm:e.target.value===''?null:e.target.valueAsNumber||0})}/></label>
   <p className="note">Выберите камеры в карточках парка. Высота определяет GSD и захват; превышение заданного GSD отклонит расчет. Для 801 точная геометрия камеры пока не подтверждена — доступен ручной режим.</p>
  </>:<p className="note">Ручная ширина применяется ко всему парку. GSD и частота снимков в этом режиме не проверяются.</p>}
  {hasPlane&&<details className="profile-form" open><summary>Самолет · допущения демонстрации</summary>
   <p className="muted">Значения ниже настраивают модель, их не следует считать паспортными. Для запуска и дуг нужно свободное пространство за пределами зоны съемки.</p>
   {number('Скорость самолета по умолчанию, м/с',fixed.airspeed_mps,18,36,v=>update({fixed_wing:{...fixed,airspeed_mps:v}}))}
   {number('Радиус разворота в штиль, м',fixed.turn_radius_m,50,1000,v=>update({fixed_wing:{...fixed,turn_radius_m:v}}))}
   <p className="hint">Крен ≈ {bankDeg(fixed.turn_radius_m,fixed.airspeed_mps).toFixed(0)}° при {fixed.airspeed_mps} м/с. Развороты с постоянным креном: в ветре траектория вытягивается по ветру. Из шести вариантов пути, включая петли, берется быстрейший; галсы облетаются в выгодном порядке, не обязательно подряд.</p>
   {number('Прямой заход на галс, м',fixed.lead_in_m??50,0,500,v=>update({fixed_wing:{...fixed,lead_in_m:v}}))}
   {number('Скорость набора высоты, м/с',fixed.climb_rate_mps,.5,8,v=>update({fixed_wing:{...fixed,climb_rate_mps:v}}),.5)}
   <p className="hint">Набор прямо по курсу запуска, а если там не хватает места — по спирали над площадкой.</p>
   {number('Скорость спуска на парашюте, м/с',fixed.parachute_sink_mps,2,10,v=>update({fixed_wing:{...fixed,parachute_sink_mps:v}}),.5)}
   <label>Высота выпуска парашюта, м (пусто — с высоты полета)<input type="number" min={20} max={300} placeholder="С высоты полета" value={fixed.parachute_release_m??''} onChange={e=>update({fixed_wing:{...fixed,parachute_release_m:e.target.value===''?null:Math.max(20,Math.min(300,e.target.valueAsNumber||20))}})}/></label>
   <p className="hint">Ниже высоты полета самолет сначала снижается кругами над точкой выпуска: меньше снос парашюта ветром.</p>
   <label>Курс запуска, ° (пусто — против ветра)<input type="number" min={0} max={359} value={fixed.launch_heading_deg??''} onChange={e=>update({fixed_wing:{...fixed,launch_heading_deg:e.target.value===''?null:e.target.valueAsNumber||0}})}/></label>
  </details>}
 </>;
}
