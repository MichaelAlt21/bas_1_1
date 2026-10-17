import type { NavTarget } from '../../shared/types';

/**
 * Catalog of places and actions for the search (Ctrl+K). It is a list of entries, not a search over the page:
 * a new entry is one object here. An entry either navigates (`target`) or runs a harmless `action`
 * (opens a dialog, switches the theme). Calculation, import, export, saving and loading demos only
 * navigate to their button: choosing a similar result never starts them.
 */
export type CommandCategory='Вкладка'|'Раздел'|'Настройка'|'Действие';
export type SafeAction='archive'|'database'|'theme';
export interface Command {id:string;title:string;category:CommandCategory;path:string[];keywords:string[];target?:NavTarget;action?:SafeAction;note?:string}

const tab=(t:NavTarget['tab'],section?:string,field?:string):NavTarget=>({tab:t,...(section?{section}:{}),...(field?{field}:{})});
const SCENARIO='Район',FLEET='Парк',SETTINGS='Параметры';
const BUTTON='Показывает кнопку; действие не выполняется без нажатия.';

export const COMMANDS:Command[]=[
 {id:'tab-scenario',title:'Район',category:'Вкладка',path:[],keywords:['карта','участок','шаг 1'],target:tab('scenario')},
 {id:'tab-fleet',title:'Парк',category:'Вкладка',path:[],keywords:['борта','бпла','шаг 2'],target:tab('fleet','fleet')},
 {id:'tab-settings',title:'Параметры',category:'Вкладка',path:[],keywords:['настройки','шаг 3'],target:tab('settings')},

 {id:'kml',title:'Данные заказчика · KML',category:'Раздел',path:[SCENARIO],keywords:['импорт','задание','kml','зоны ограничений','препятствия','файл'],target:tab('scenario','kml')},
 {id:'areas',title:'Зоны съемки',category:'Раздел',path:[SCENARIO],keywords:['зона','полигон','поле','участок','район съемки','площадь'],target:tab('scenario','areas')},
 {id:'airspace-drawn',title:'Пространство и запреты',category:'Раздел',path:[SCENARIO],keywords:['разрешенное пространство','запретная зона','рамка','граница'],target:tab('scenario','airspace-drawn')},
 {id:'sites',title:'Площадки',category:'Раздел',path:[SCENARIO],keywords:['площадка','старт','посадка','взлет','запасная','зу','зарядные места','база'],target:tab('scenario','sites')},
 {id:'scenario-file',title:'Сценарий · открыть, импорт и экспорт JSON',category:'Раздел',path:[SCENARIO],keywords:['сценарий','json','открыть','загрузить','импорт','экспорт','скачать','сохраненный'],target:tab('scenario','scenario-file')},

 {id:'fleet',title:'Парк БПЛА',category:'Раздел',path:[FLEET],keywords:['борт','аппарат','дрон','коптер','самолет','gemini','геоскан','добавить бпла','заряд','аккумулятор','батарея'],target:tab('fleet','fleet')},
 {id:'aircraft-sensors',title:'Сенсоры бортов',category:'Настройка',path:[FLEET,'Карточка борта'],keywords:['камера','сенсор','нагрузка','подвес','тепловизор','мультиспектральная','установленная'],target:tab('fleet','fleet')},
 {id:'sensors',title:'Профили сенсоров',category:'Раздел',path:[FLEET],keywords:['камера','сенсор','тепловизор','лидар','магнитометр','матрица камеры','фокусное расстояние','профиль','импорт профилей','экспорт профилей','источник'],target:tab('fleet','sensors')},

 {id:'requirements',title:'Требования съемки',category:'Раздел',path:[SETTINGS],keywords:['виды съемки','тип съемки','rgb','ик','тепловая','мультиспектральная','lidar','лидар','геофизика','магнитометрия','gsd','несколько видов'],target:tab('settings','requirements')},
 {id:'survey',title:'Съемка · высота и скорость',category:'Раздел',path:[SETTINGS],keywords:['высота','скорость','перекрытие','gsd','ширина захвата','камера','самолет','парашют','разворот'],target:tab('settings','survey')},
 {id:'altitude',title:'Высота съемки',category:'Настройка',path:[SETTINGS,'Съемка'],keywords:['высота','agl','метры'],target:tab('settings','survey','settings.altitude_m')},
 {id:'levels',title:'Высоты и эшелоны',category:'Раздел',path:[SETTINGS],keywords:['эшелон','разведение','высота','потолок','150 м'],target:tab('settings','levels')},
 {id:'cost',title:'Модель стоимости J',category:'Раздел',path:[SETTINGS],keywords:['стоимость','цена','критерий','матрица q','коэффициенты','альфа','α','у.е.','квадратичный','пары бортов','деньги','тариф налета','цена часа','открытые данные','экономика'],target:tab('settings','cost','cost')},
 {id:'cost-types',title:'Коэффициенты c и q по типам бортов',category:'Настройка',path:[SETTINGS,'Модель стоимости J'],keywords:['тип борта','модель бпла','gemini','801','201','для всех бортов типа','стоимость часа'],target:tab('settings','cost','cost:types')},
 {id:'cost-matrix',title:'Матрица Q · индивидуальные и парные коэффициенты',category:'Настройка',path:[SETTINGS,'Модель стоимости J'],keywords:['матрица','q','пары','диагональ','коэффициент борта'],target:tab('settings','cost','cost:matrix')},
 {id:'sorties',title:'Вылеты и зарядка',category:'Раздел',path:[SETTINGS],keywords:['повторные вылеты','замена акб','аккумулятор','батарея','зарядка','зу','смена нагрузки','смена камеры'],target:tab('settings','sorties')},
 {id:'payload-change',title:'Смена нагрузки между вылетами',category:'Настройка',path:[SETTINGS,'Вылеты и зарядка'],keywords:['смена камеры','сенсор','переустановка','минуты'],target:tab('settings','sorties','settings.payload_change_min')},
 {id:'deadline',title:'Сроки и ограничения',category:'Раздел',path:[SETTINGS],keywords:['срок','время','закат','начало работ','число бортов','лимит'],target:tab('settings','deadline')},
 {id:'airspace',title:'Воздушное пространство',category:'Раздел',path:[SETTINGS],keywords:['рельеф','запас','препятствия','зоны','amsl','agl'],target:tab('settings','airspace')},
 {id:'weather',title:'Погода · ветер',category:'Раздел',path:[SETTINGS],keywords:['ветер','порывы','направление','open-meteo','погода'],target:tab('settings','weather','weather.wind_speed_mps')},
 {id:'reserve',title:'Резерв заряда',category:'Настройка',path:[SETTINGS,'Допущения и посадка'],keywords:['резерв','запас','аккумулятор','батарея','заряд','акб'],target:tab('settings','assumptions','settings.reserve_percent')},
 {id:'assumptions',title:'Допущения и посадка',category:'Раздел',path:[SETTINGS],keywords:['посадка','отступ','длительность','паспорт','допущения'],target:tab('settings','assumptions')},

 {id:'objective',title:'Цель расчета · критерий',category:'Настройка',path:['Панель расчета'],keywords:['критерий','быстрее','налет','стоимость','цель','оптимизация'],target:tab('settings',undefined,'action:objective')},
 {id:'calculate',title:'Рассчитать план',category:'Действие',path:['Панель расчета'],keywords:['расчет','план','запустить','посчитать'],target:tab('settings',undefined,'action:calculate'),note:BUTTON},
 {id:'compare',title:'Сравнить три критерия',category:'Действие',path:['Панель расчета'],keywords:['сравнение','критерии','стоимость','налет','срок'],target:tab('settings',undefined,'action:compare'),note:BUTTON},
 {id:'analyse',title:'Анализ парка',category:'Действие',path:['Панель расчета'],keywords:['сколько бортов','число бортов','срок'],target:tab('settings',undefined,'action:analyse'),note:BUTTON},
 {id:'save',title:'Сохранить сценарий',category:'Действие',path:['Панель расчета'],keywords:['сохранить','снимок'],target:tab('settings',undefined,'action:save'),note:BUTTON},
 {id:'export',title:'Экспорт плана · отчет, KML, GeoJSON',category:'Действие',path:['Результат'],keywords:['экспорт','скачать','отчет','kml','geojson','выгрузка','печать'],target:tab('settings',undefined,'action:export'),note:'Показывает кнопки экспорта рассчитанного плана; без плана — экспорт сценария JSON.'},
 {id:'demos',title:'Примеры сценариев',category:'Действие',path:['Верхняя панель'],keywords:['пример','демо','демонстрация','виды съемки'],target:tab('scenario',undefined,'action:demos'),note:BUTTON},
 {id:'archive',title:'Архив расчетов',category:'Действие',path:['Верхняя панель'],keywords:['архив','сохраненные планы','история','открыть план'],action:'archive'},
 {id:'database',title:'БД компании',category:'Действие',path:['Верхняя панель'],keywords:['база данных','бд','postgresql','sqlite','подключение'],action:'database'},
 {id:'theme',title:'Светлая или темная тема',category:'Действие',path:['Верхняя панель'],keywords:['тема','темная','светлая','оформление'],action:'theme'},
];

/** Word groups that mean the same thing to the operator. */
const SYNONYMS:string[][]=[
 ['аккумулятор','акб','батарея','заряд'],['база данных','бд'],['камера','сенсор','нагрузка'],['стоимость','цена','затраты'],
 ['площадка','старт'],['экспорт','выгрузка','скачать'],['ветер','погода'],['ик','тепловизор','тепловая'],['лидар','lidar'],['бпла','борт','дрон','аппарат'],
];
export const normalize=(text:string)=>text.toLowerCase().replace(/ё/g,'е').replace(/[«»"',·()]/g,' ').trim();
const words=(text:string)=>normalize(text).split(/[\s/:.\-–—]+/).filter(Boolean);

/** How well one query word matches one word of an entry: prefix, then a shared stem (Russian endings), then substring. */
function wordScore(query:string,word:string){
 if(word===query)return 1;
 if(word.startsWith(query))return .9;
 const stem=query.length>=5?query.slice(0,query.length-2):query.length>=4?query.slice(0,query.length-1):'';
 if(stem&&word.startsWith(stem))return .75;
 if(query.length>=3&&word.includes(query))return .5;
 return 0;
}
function expand(token:string){
 const group=SYNONYMS.find(g=>g.some(w=>w===token||words(w).includes(token)||(token.length>=4&&w.startsWith(token))));
 return group?[token,...group.flatMap(words).filter(w=>w!==token)]:[token];
}
function fieldScore(token:string,fieldWords:string[]){
 let best=0;
 // Synonyms count only as whole words or prefixes: «цена» must not match inside «сценарий».
 for(const [n,variant] of expand(token).entries())for(const w of fieldWords){const score=wordScore(variant,w);if(!n||score>=.75)best=Math.max(best,score*(n?.8:1));}
 return best;
}

export interface Match {command:Command;score:number}
/** Every query word has to match the title, path or keywords; titles weigh most. Results are sorted by score, then by catalog order. */
export function searchCommands(query:string,commands:Command[]=COMMANDS,limit=12):Match[]{
 const tokens=words(query);
 if(!tokens.length)return commands.filter(c=>c.category!=='Настройка').slice(0,limit).map(command=>({command,score:0}));
 const found:Match[]=[];
 commands.forEach(command=>{
  const title=words(command.title),path=command.path.flatMap(words),keys=command.keywords.flatMap(words);
  let total=0;
  for(const token of tokens){
   const score=Math.max(3*fieldScore(token,title),2*fieldScore(token,keys),fieldScore(token,path));
   if(!score)return;
   total+=score;
  }
  if(normalize(command.title).startsWith(normalize(query)))total+=1;
  found.push({command,score:total});
 });
 return found.map((m,i)=>({m,i})).sort((a,b)=>b.m.score-a.m.score||a.i-b.i).slice(0,limit).map(x=>x.m);
}
