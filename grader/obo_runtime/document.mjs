import {registry,defaultInput,run} from './index.mjs';
import {ensure,clone,unique,byteSize,checkShape,parseBoundedJson} from './validation.mjs';
import {RR_TEXT,rrInputText} from './rrText.mjs';
import {TEXT_FORMAT,inputToText} from './textFormats.mjs';
const identifier={type:'string',pattern:'^[A-Za-z0-9_-]{1,100}$'};
const nullableId={anyOf:[identifier,{type:'null'}]};
const object=(properties)=>({type:'object',properties,required:Object.keys(properties),additionalProperties:false});
const text=(maxLength)=>({type:'string',maxLength});
const ids={type:'array',items:identifier,maxItems:8};
const documentSchema=object({
  schemaVersion:{const:2},
  instances:{type:'array',minItems:1,maxItems:8,items:object({
    id:identifier,scenarioId:text(32),modelVersion:{const:1},name:text(256),input:{type:'object'},
    views:{type:'array',maxItems:100,items:object({
      id:identifier,role:text(32),target:{anyOf:[text(100),{type:'null'}]},
      x:{type:'number'},y:{type:'number'},width:{type:'number'},height:{type:'number'},
      label:text(256),color:text(7),hidden:{type:'boolean'},text:text(4000),
      when:object({kind:{anyOf:[text(100),{type:'null'}]},actor:{anyOf:[text(100),{type:'null'}]}}),
      fields:{type:'array',maxItems:50,items:text(100)}
    })},
    decorations:{type:'array',maxItems:300,items:object({id:identifier,source:identifier,target:identifier})},
    labels:{type:'object',propertyNames:identifier,additionalProperties:text(256)}
  })},
  question:{type:'object',properties:{selectionMode:{enum:['single','multiple']},judgeProfile:{enum:['text-v1','obo-v1']},assessmentMode:{enum:['result','process']},gradingInstanceId:nullableId,ioFormat:{enum:[RR_TEXT,TEXT_FORMAT,'json-v1']}},required:['selectionMode','judgeProfile','assessmentMode','gradingInstanceId'],additionalProperties:false},
  explanations:object({common:ids,choices:{type:'object',propertyNames:identifier,additionalProperties:ids}})
});
export const roleLabels={actor:'실행 주체',processor:'처리 장치',resource:'자원',queue:'큐·버퍼',record:'자료구조',memory:'메모리',region:'영역',relation:'관계',timeline:'시간축',metric:'통계',storage:'저장 장치',annotation:'설명'};
export const freshId=(prefix='item')=>`${prefix}_${globalThis.crypto.randomUUID().replaceAll('-','').slice(0,16)}`;
export function semanticEntities(instance) {
  const x=instance.input,f=registry[instance.scenarioId].family;
  let actors=[];let resources=[];
  if(f==='cpu-scheduling')actors=x.jobs.map(p=>p.id);
  else if(f==='lifecycle')actors=Object.keys(x.processes);
  else if(f==='context-switch')actors=Object.keys(x.pcbs);
  else if(f==='process-structure')actors=Object.keys(x.threads);
  else if(f==='memory-pressure')actors=Object.keys(x.quotas);
  else if(f==='kernel-flow')actors=Object.keys(x.contexts);
  else if(Array.isArray(x.actors))actors=x.actors.map(a=>typeof a==='string'?a:a.id);
  if(x.resources)resources=x.resources;
  else if(f==='dining-philosophers')resources=x.actors.map((_,i)=>`fork_${i}`);
  else if(f==='bounded-buffer')resources=['empty','full','mutex'];
  return {actor:actors,resource:resources};
}
export function createView(role,target=null,at=0) {
  return {id:freshId('view'),role,target,x:40+(at%3)*300,y:40+Math.floor(at/3)*220,width:270,height:180,label:roleLabels[role],color:'#4f46e5',hidden:false,text:'',when:{kind:null,actor:null},fields:[]};
}
const placementGap=30,placementLimit=100000;
function checkViewGeometry(view) {
  ensure(view&&[view.x,view.y,view.width,view.height].every(Number.isFinite),'표시 위치와 크기를 확인하세요.');
  ensure(Math.abs(view.x)<=placementLimit&&Math.abs(view.y)<=placementLimit,'배치 범위를 벗어났습니다.');
  ensure(view.width>=100&&view.width<=2000&&view.height>=60&&view.height<=2000,'표시 크기를 확인하세요.');
}
function placementViews(instance,excludedIds) {
  const excluded=new Set(excludedIds);
  return instance.views.filter(view=>!excluded.has(view.id));
}
function placementOverlaps(a,b) {
  return !(a.x+a.width+placementGap<=b.x||b.x+b.width+placementGap<=a.x||a.y+a.height+placementGap<=b.y||b.y+b.height+placementGap<=a.y);
}
/** Place only the new view. Caller exclusions are for known off-canvas views, not hidden/when state. */
export function placeNewView(instance,view,{excludedIds=[]}={}) {
  checkViewGeometry(view);
  const occupied=placementViews(instance,excludedIds);
  occupied.forEach(checkViewGeometry);
  const free=point=>Math.abs(point.x)<=placementLimit&&Math.abs(point.y)<=placementLimit&&occupied.every(old=>!placementOverlaps({...view,x:point.x,y:point.y},old));
  if(free(view))return clone(view);
  const origin=occupied[0]??{x:40,y:40},width=Math.max(900,view.width,...occupied.map(old=>old.width));
  const xs=new Set([origin.x,view.x,-placementLimit,placementLimit]),ys=new Set([origin.y,view.y,-placementLimit,placementLimit]);
  for(const old of occupied){
    xs.add(old.x+old.width+placementGap);xs.add(old.x-view.width-placementGap);
    ys.add(old.y+old.height+placementGap);ys.add(old.y-view.height-placementGap);
  }
  const candidates=[];
  for(const y of ys)for(const x of xs){
    if(Math.abs(x)>placementLimit||Math.abs(y)>placementLimit)continue;
    const local=x>=origin.x&&y>=origin.y&&x+view.width<=origin.x+width;
    candidates.push({x,y,score:local?[0,y-origin.y,x-origin.x]:[1,Math.abs(x-origin.x)+Math.abs(y-origin.y),y,x]});
  }
  candidates.sort((a,b)=>{
    for(let i=0;i<a.score.length;i++){const delta=a.score[i]-b.score[i];if(delta)return delta;}
    return 0;
  });
  const chosen=candidates.find(free);
  ensure(chosen,'현재 배치 범위에서 새 표시를 놓을 공간을 찾지 못했습니다.');
  return {...clone(view),x:chosen.x,y:chosen.y};
}
export function createInstance(scenarioId='ST2') {
  ensure(Object.hasOwn(registry,scenarioId),'시나리오가 없습니다.');
  const instance={id:freshId('sim'),scenarioId,modelVersion:1,name:registry[scenarioId].metadata.name,input:defaultInput(scenarioId),views:[],decorations:[],labels:{}};
  const entities=semanticEntities(instance);
  if(scenarioId==='ST2') {
    // Two processes that pass through all six transitions (I/O wait, preemption, completion); at most one runs at a time.
    instance.input={processes:{P1:'New',P2:'New'},actions:[['P1','create'],['P2','create'],['P1','dispatch'],['P1','io'],['P2','dispatch'],['P1','completeIO'],['P2','preempt'],['P1','dispatch'],['P1','finish'],['P2','dispatch'],['P2','finish']].map(([id,event])=>({id,event}))};
    instance.views=[{...createView('relation'),label:'프로세스 상태',width:800,height:460},createView('annotation')];
    return instance;
  }
  if(scenarioId==='BK') {
    instance.views=[{...createView('record'),label:'안전성 비교',width:900,height:440,fields:['Need','Work','Finish']},createView('annotation')];
    return instance;
  }
  if(scenarioId==='IN') {
    instance.views=[{...createView('storage'),label:'파일 접근 경로',width:900,height:480},createView('annotation')];
    return instance;
  }
  if(scenarioId==='PT') {
    instance.views=[{...createView('record'),label:'프로세스와 스레드',width:900,height:500},createView('annotation')];
    return instance;
  }
  if(scenarioId==='TLB') {
    instance.views=[{...createView('record'),label:'주소 변환 경로',width:900,height:500},createView('annotation')];
    return instance;
  }
  if(scenarioId==='CS') {
    instance.views=[{...createView('record'),label:'CPU와 PCB',width:900,height:500},createView('annotation')];
    return instance;
  }
  if(scenarioId==='PC') {
    instance.views=[{...createView('record'),label:'생산자와 소비자',width:900,height:500},createView('annotation')];
    return instance;
  }
  if(scenarioId==='IPC') {
    instance.views=[{...createView('record'),label:'프로세스 간 통신',width:900,height:500},createView('annotation')];
    return instance;
  }
  if(scenarioId==='P2') {
    // Authoring default only (contracts inputExample stays the minimal shape example): a textbook string that shows hits and replacements.
    instance.input={refs:[7,0,1,2,0,3,0,4,2,3,0,3],frameCount:3,policy:'FIFO',initial:[]};
    instance.views=[{...createView('record'),label:'참조열과 프레임',width:900,height:380},createView('annotation')];
    return instance;
  }
  if(scenarioId==='G2'||scenarioId==='RR'||scenarioId==='PS') {
    // Four jobs whose order and waiting differ by policy (FCFS 4 · SJF 3.25 · SRTF 2.25 · RR 4.5 · PS-P 3.5 average waiting).
    const jobs=[{id:'P1',arrival:0,bursts:[5],priority:3},{id:'P2',arrival:1,bursts:[3],priority:1},{id:'P3',arrival:2,bursts:[1],priority:4},{id:'P4',arrival:3,bursts:[2],priority:2}];
    instance.input={jobs,policy:scenarioId==='G2'?'SJF':scenarioId==='RR'?'RR':'PS-P',quantum:2};
    const timeline={...createView('timeline'),label:scenarioId==='G2'?'정책별 실행 순서':scenarioId==='RR'?'Round Robin 실행':'우선순위 실행',width:900,height:scenarioId==='G2'?540:500};
    instance.views=scenarioId==='G2'?[timeline,createView('annotation')]:[timeline];
    return instance;
  }
  if(scenarioId==='TH') {
    // P cycles 4 pages with 2 frames (working set > quota → repeated misses); Q cycles 2 pages with 2 frames.
    const stream=[];for(let i=0;i<8;i++){stream.push({id:'P',page:[1,2,3,4][i%4]});stream.push({id:'Q',page:[7,8][i%2]});}
    instance.input={stream,quotas:{P:2,Q:2},window:4,frameBudget:4};
    instance.views=[{...createView('memory'),label:'프로세스별 메모리 압박',width:900,height:420},createView('annotation')];
    return instance;
  }
  if(scenarioId==='P3') {
    // Textbook Belady string: FIFO with 3 vs 4 frames gives 9 vs 10 misses. The memory role shows one row per frame count.
    instance.input={refs:[1,2,3,4,1,2,5,1,2,3,4,5],capacities:[3,4],policy:'FIFO'};
    instance.views=[{...createView('memory'),label:'프레임 수별 비교',width:900,height:420},createView('annotation')];
    return instance;
  }
  if(scenarioId==='OPT') {
    // Same textbook string as P2 with the OPT policy; the record compares FIFO/LRU/OPT. The OPT contract has no annotation role.
    instance.input={refs:[7,0,1,2,0,3,0,4,2,3,0,3],frameCount:3,policy:'OPT',initial:[]};
    instance.views=[{...createView('record'),label:'세 정책 비교',width:900,height:420}];
    return instance;
  }
  if(scenarioId==='K-SYSCALL'||scenarioId==='K-INTERRUPT') {
    instance.views=[{...createView('record'),label:scenarioId==='K-SYSCALL'?'시스템 호출과 모드':'인터럽트와 실행 주체',width:900,height:500},createView('annotation')];
    return instance;
  }
  if(scenarioId==='MF') {
    // The last request is rejected under every policy although the free total would hold it (external fragmentation).
    instance.input={total:64,initial:[{start:0,size:64,owner:null}],operations:[{op:'alloc',id:'A',size:10},{op:'alloc',id:'B',size:20},{op:'alloc',id:'C',size:8},{op:'free',id:'B'},{op:'alloc',id:'D',size:12},{op:'alloc',id:'E',size:30}],policy:'best'};
    instance.views=[{...createView('memory'),label:'주소 공간과 빈 구멍',width:900,height:360},createView('annotation')];
    return instance;
  }
  if(scenarioId==='S3') {
    // Single-instance R1/R2: P1 and P2 each hold one and request the other (deadlock); P3 waits but holds nothing.
    instance.input={actors:['P1','P2','P3'],resources:['R1','R2'],available:[1,1],allocated:[[0,0],[0,0],[0,0]],requests:[[0,0],[0,0],[0,0]],
      actions:[{actor:'P1',op:'request',values:[1,0]},{actor:'P2',op:'request',values:[0,1]},{actor:'P1',op:'request',values:[0,1]},{actor:'P2',op:'request',values:[1,0]},{actor:'P3',op:'request',values:[1,0]}]};
    instance.views=[{...createView('relation'),label:'자원 할당 그래프',width:900,height:470},createView('annotation')];
    return instance;
  }
  if(scenarioId==='DP') {
    // Five philosophers, left fork first, two rounds of turns: everyone ends holding one fork and waiting (deadlock).
    const actors=['A','B','C','D','E'],zero=Object.fromEntries(actors.map(id=>[id,0])),one=Object.fromEntries(actors.map(id=>[id,1]));
    instance.input={actors,order:[...actors,...actors],policy:'left',mode:'fixedSchedule',thinkTicks:zero,eatTicks:one,goalMeals:{}};
    instance.views=[{...createView('relation'),label:'원탁과 포크',width:900,height:470},createView('annotation')];
    return instance;
  }
  if(scenarioId==='DS') {
    // Textbook queue from cylinder 53 on 0–199: FCFS 640 · SSTF 236 · SCAN 331 · C-SCAN 382. The DS contract has no annotation role.
    instance.input={requests:[['a',98],['b',183],['c',37],['d',122],['e',14],['f',124],['g',65],['h',67]].map(([id,position])=>({id,position})),start:53,max:199,policy:'SSTF',direction:1};
    instance.views=[{...createView('storage'),label:'헤드 이동 경로',width:900,height:470}];
    return instance;
  }
  for(const role of registry[scenarioId].metadata.elements) {
    if(['S3','BK','DP'].includes(scenarioId)&&['actor','resource'].includes(role))for(const target of entities[role])instance.views.push(createView(role,target,instance.views.length));
    else instance.views.push(createView(role,null,instance.views.length));
  }
  return arrangeViews(instance);
}
export function arrangeViews(instance,{excludedIds=[]}={}) {
  const next=clone(instance);
  // The DP ring layout applies only to per-philosopher/fork cards; the one-card table (2026-11-08 default) uses the row layout.
  if(next.scenarioId==='DP'&&next.views.some(v=>['actor','resource'].includes(v.role))) {
    next.views.forEach((v,i)=>{v.x=40+(i%3)*300;v.y=40+Math.floor(i/3)*220;});
    const actors=next.views.filter(v=>v.role==='actor'),resources=next.views.filter(v=>v.role==='resource');
    const radius=Math.max(330,actors.length*85),center=radius+120;
    actors.forEach((v,i)=>{const angle=2*Math.PI*i/actors.length-Math.PI/2;v.x=center+radius*Math.cos(angle)-90;v.y=center+radius*Math.sin(angle)-60;v.width=180;v.height=120;});
    resources.forEach((v,i)=>{const angle=2*Math.PI*(i+.5)/resources.length-Math.PI/2;v.x=center+radius*.62*Math.cos(angle)-70;v.y=center+radius*.62*Math.sin(angle)-50;v.width=140;v.height=100;});
    next.views.filter(v=>!['actor','resource'].includes(v.role)).forEach((v,i)=>{v.x=center+radius+150;v.y=60+i*220;});
    return next;
  }
  const views=placementViews(next,excludedIds);
  views.forEach(checkViewGeometry);
  let y=40;
  for(let offset=0;offset<views.length;offset+=3){
    const row=views.slice(offset,offset+3);let x=40;
    for(const view of row){view.x=x;view.y=y;x+=view.width+placementGap;}
    y+=Math.max(...row.map(view=>view.height))+placementGap;
  }
  return next;
}
export function createDocument(scenarioId='ST2') {
  const instance=createInstance(scenarioId);
  return {schemaVersion:2,instances:[instance],question:{selectionMode:'single',judgeProfile:'text-v1',assessmentMode:'result',gradingInstanceId:null},explanations:{common:[instance.id],choices:{}}};
}
export function copyInstance(instance) {const next=clone(instance);next.id=freshId('sim');next.name=`${instance.name} 비교`;return next;}
export function replaceInput(instance,input) {
  const next=clone(instance);next.input=clone(input);const entities=semanticEntities(next);
  next.views=next.views.filter(v=>v.target===null||!(v.role in entities)||entities[v.role].includes(v.target));
  for(const view of next.views)if(view.when.actor!==null&&!entities.actor.includes(view.when.actor))view.when.actor=null;
  const ids=new Set(next.views.map(v=>v.id));next.decorations=next.decorations.filter(e=>ids.has(e.source)&&ids.has(e.target));
  return next;
}
export function addActor(instance,actorRole='producer',options={}) {
  const next=clone(instance),x=next.input,id=freshId('P'),f=registry[next.scenarioId].family;
  if(f==='cpu-scheduling')x.jobs.push({id,arrival:0,bursts:[1],priority:0});
  else if(f==='lifecycle')x.processes[id]='New';
  else if(f==='context-switch')x.pcbs[id]=clone(Object.values(x.pcbs)[0]??{pc:0,sp:0});
  else if(f==='process-structure'){const process=Object.keys(x.spaces)[0];ensure(process,'주소 공간을 먼저 추가하세요.');x.threads[id]={process,registers:{pc:0},stack:[]};}
  else if(f==='memory-pressure'){x.quotas[id]=1;x.frameBudget++;}
  else if(f==='kernel-flow')x.contexts[id]={pc:0,sp:0};
  else if(f==='bounded-buffer')x.actors.push({id,role:actorRole});
  else if(f==='dining-philosophers'){x.actors.push(id);x.thinkTicks[id]=0;x.eatTicks[id]=1;}
  else if(['resource-allocation','banker'].includes(f)){x.actors.push(id);x.allocated.push(x.resources.map(()=>0));(x.max??x.requests).push(x.resources.map(()=>0));}
  else if(f==='ipc')x.actors.push(id);
  else throw Error('이 모델의 실행 주체는 입력 설정에서 구성합니다.');
  next.views.push(placeNewView(next,createView('actor',id,next.views.length),options));return next;
}
export function deleteActor(instance,id) {
  const next=clone(instance),x=next.input,f=registry[next.scenarioId].family;
  if(f==='cpu-scheduling')x.jobs=x.jobs.filter(p=>p.id!==id);
  else if(f==='lifecycle'){delete x.processes[id];x.actions=x.actions.filter(a=>a.id!==id);}
  else if(f==='context-switch'){ensure(x.initialId!==id,'초기 실행 대상을 먼저 바꾸세요.');delete x.pcbs[id];x.order=x.order.filter(p=>p!==id);}
  else if(f==='process-structure'){delete x.threads[id];x.operations=x.operations.filter(a=>a.thread!==id);}
  else if(f==='memory-pressure'){delete x.quotas[id];x.stream=x.stream.filter(a=>a.id!==id);}
  else if(f==='kernel-flow'){ensure(x.current!==id&&x.blocked!==id,'현재 실행/대기 대상은 먼저 변경하세요.');delete x.contexts[id];}
  else if(f==='bounded-buffer'){x.actors=x.actors.filter(a=>a.id!==id);x.order=x.order.filter(p=>p!==id);}
  else if(f==='dining-philosophers'){x.actors=x.actors.filter(a=>a!==id);x.order=x.order.filter(p=>p!==id);delete x.thinkTicks[id];delete x.eatTicks[id];delete x.goalMeals[id];}
  else if(['resource-allocation','banker'].includes(f)) {
    const p=x.actors.indexOf(id);ensure(p>=0,'주체가 없습니다.');x.allocated[p].forEach((v,i)=>x.available[i]+=v);x.actors.splice(p,1);x.allocated.splice(p,1);(x.max??x.requests).splice(p,1);
    if(x.actions)x.actions=x.actions.filter(a=>a.actor!==id);if(x.request?.actor===id)x.request=null;
  }else if(f==='ipc'){x.actors=x.actors.filter(a=>a!==id);x.operations=x.operations.filter(a=>a.actor!==id);}
  else throw Error('이 모델의 주체를 직접 삭제할 수 없습니다.');
  delete next.labels[id];return replaceInput(next,x);
}
export function connectResource(instance,actor,resource,kind,delta=1) {
  ensure(instance.scenarioId==='S3','의미 관계 직접 편집은 자원 할당 그래프에서 지원합니다. 다른 모델은 구조 입력을 편집하세요.');
  const next=clone(instance),x=next.input,p=x.actors.indexOf(actor),r=x.resources.indexOf(resource);
  ensure(p>=0&&r>=0,'연결 대상이 없습니다.');ensure(['request','assign'].includes(kind),'관계 종류를 선택하세요.');ensure(delta===1||delta===-1,'연결 수량이 잘못되었습니다.');
  if(kind==='request'){ensure(x.requests[p][r]+delta>=0,'요청 관계가 없습니다.');x.requests[p][r]+=delta;}
  else{ensure(x.available[r]-delta>=0&&x.allocated[p][r]+delta>=0,'자원 수량이 부족합니다.');x.available[r]-=delta;x.allocated[p][r]+=delta;}
  return next;
}
export function validateDocument(document,{execute=true}={}) {
  // Bound imported object graphs as well as text imports; reject reserved keys before use.
  parseBoundedJson(JSON.stringify(document));
  checkShape(documentSchema,document);
  ensure(document&&document.schemaVersion===2,'새 OBO 문서 형식이 아닙니다.');ensure(byteSize(document)<=1048576,'OBO 문서는 1MiB 이하여야 합니다.');
  ensure(Array.isArray(document.instances)&&document.instances.length>=1&&document.instances.length<=8,'시뮬레이션은 1~8개로 구성하세요.');unique(document.instances.map(i=>i.id));
  for(const instance of document.instances) {
    ensure(Object.hasOwn(registry,instance.scenarioId)&&instance.modelVersion===1,'지원하지 않는 모델입니다.');
    checkShape(registry[instance.scenarioId].inputSchema,instance.input);
    ensure(typeof instance.name==='string'&&instance.name.length<=256,'시뮬레이션 이름을 확인하세요.');
    ensure(Array.isArray(instance.views)&&instance.views.length<=100,'표시는 실행별 최대 100개입니다.');unique(instance.views.map(v=>v.id));
    const entities=semanticEntities(instance),ids=new Set(instance.views.map(v=>v.id));
    for(const view of instance.views) {
      ensure(registry[instance.scenarioId].metadata.elements.includes(view.role),'이 모델에서 지원하지 않는 표시 역할입니다.');
      ensure([view.x,view.y,view.width,view.height].every(Number.isFinite),'표시 위치와 크기를 확인하세요.');ensure(view.width>=100&&view.height>=60&&view.width<=2000&&view.height<=2000,'표시 크기를 확인하세요.');
      ensure(Math.abs(view.x)<=100000&&Math.abs(view.y)<=100000,'배치 범위를 벗어났습니다.');
      ensure(typeof view.label==='string'&&view.label.length<=256&&typeof view.text==='string'&&view.text.length<=4000,'표시 문구가 너무 깁니다.');
      ensure(/^#[0-9a-fA-F]{6}$/.test(view.color),'표시 색상을 확인하세요.');
      ensure(view.target===null||!(view.role in entities)||entities[view.role].includes(view.target),'표시가 삭제된 대상을 참조합니다.');
      ensure(typeof view.hidden==='boolean'&&view.when&&Array.isArray(view.fields),'표시 설정을 확인하세요.');
    }
    ensure(Array.isArray(instance.decorations)&&instance.decorations.length<=300,'장식 관계가 너무 많습니다.');
    unique(instance.decorations.map(e=>e.id));
    for(const e of instance.decorations)ensure(ids.has(e.source)&&ids.has(e.target),'장식 선의 끝점이 없습니다.');
    if(execute)run(instance.scenarioId,instance.input,{record:false});
  }
  const q=document.question;ensure(q&&['single','multiple'].includes(q.selectionMode)&&['text-v1','obo-v1'].includes(q.judgeProfile)&&['result','process'].includes(q.assessmentMode),'문제 규칙을 확인하세요.');
  if(q.ioFormat===RR_TEXT){
    const selected=document.instances.find(i=>i.id===q.gradingInstanceId);
    ensure(q.judgeProfile==='obo-v1'&&q.assessmentMode==='result'&&selected?.scenarioId==='RR','구간 출력은 RR 결과 채점에서만 사용할 수 있습니다.');rrInputText(selected.input);
  }
  if(q.ioFormat===TEXT_FORMAT){
    const selected=document.instances.find(i=>i.id===q.gradingInstanceId);
    ensure(q.judgeProfile==='obo-v1'&&q.assessmentMode==='result'&&selected,'텍스트 입출력은 의미 채점의 결과 검사에서만 사용할 수 있습니다.');inputToText(selected.scenarioId,selected.input);
  }
  if(q.judgeProfile==='obo-v1'){
    const selected=document.instances.find(i=>i.id===q.gradingInstanceId);ensure(selected,'채점할 실행을 선택하세요.');ensure(!(selected.scenarioId==='DP'&&selected.input.mode==='findSchedule'&&q.assessmentMode!=='process'),'유효 일정 찾기는 과정형만 지원합니다.');
  }
  ensure(document.explanations&&Array.isArray(document.explanations.common)&&document.explanations.choices&&typeof document.explanations.choices==='object','해설 연결을 확인하세요.');
  const ids=new Set(document.instances.map(i=>i.id));
  for(const list of [document.explanations.common,...Object.values(document.explanations.choices)]){ensure(Array.isArray(list)&&list.every(id=>ids.has(id)),'해설이 없는 실행을 참조합니다.');unique(list);}
  return true;
}
