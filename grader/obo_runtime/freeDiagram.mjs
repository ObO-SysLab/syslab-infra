// Shared authoring boundary. Validation never repairs, truncates or mutates saved work.
import {parseBoundedJson} from './validation.mjs';
export const diagramFields = {
  'state-node':['label','removed'], 'text-label':['text'], 'gantt-lane':['blocks','activeBlockIndex'],
  'slot-grid':['slots','faultSlotIndex','hitSlotIndex','activeSlotIndex','pointerSlotIndex'],
  'resource-square':['instances','allocated'], 'counter-badge':['value','delta'],
  'queue-strip':['capacity','items','direction'], 'table-grid':['headers','rows','activeRow','width'],
  'memory-range':['start','size','segments','activeIds'], 'process-box':['sections','threads','activeThreadId'],
  'line-chart':['xLabel','yLabel','width','height','xMin','xMax','yMin','yMax','xTicks','yTicks','series','progressIndex','activePointIndex'],
};
// Presentation is authored in the editor; learner output keeps its existing fields.
const manualDiagramFields = { ...diagramFields, 'queue-strip': [...diagramFields['queue-strip'], 'displayStyle','showCount','showDirection'], 'process-box': [...diagramFields['process-box'],
 'hiddenSectionNames','hiddenThreadIds','threadFields','threadLayout','sectionLayout','showSharedLinks','width'] };
const events={'gantt-lane':['activeBlockIndex'],'slot-grid':['faultSlotIndex','hitSlotIndex','activeSlotIndex','pointerSlotIndex'],'counter-badge':['delta']};
const fail=(p,m)=>{throw Error(`${p}: ${m}`);};
const object=(v,p)=>{if(!v||typeof v!=='object'||Array.isArray(v))fail(p,'객체가 필요합니다.');return v;};
const list=(v,p,max=4096)=>{if(!Array.isArray(v)||v.length>max)fail(p,`목록이 필요합니다 (최대 ${max}개).`);return v;};
const num=(v,p,min=-Number.MAX_SAFE_INTEGER,max=Number.MAX_SAFE_INTEGER,integer=false)=>{
 if(typeof v!=='number'||!Number.isFinite(v)||v<min||v>max||integer&&!Number.isSafeInteger(v))fail(p,'숫자와 범위를 확인하세요.');return v;
};
const str=(v,p)=>{if(typeof v!=='string')fail(p,'문자열이 필요합니다.');return v;};
const id=(v,p)=>{str(v,p);if(!v.trim()||v.length>200||['__proto__','prototype','constructor'].includes(v))fail(p,'식별자를 확인하세요.');return v;};
const refs=(v,ids,p)=>{const a=list(v,p);const seen=new Set();for(const x of a){if(typeof x!=='string'||!ids.has(x)||seen.has(x))fail(p,'없거나 중복된 참조입니다.');seen.add(x);}return a;};
function safe(v,p,depth=0){
 if(depth>32)fail(p,'중첩 깊이를 줄이세요.');
 if(typeof v==='number')num(v,p);
 if(v&&typeof v==='object')for(const [k,x]of Object.entries(v)){if(['__proto__','prototype','constructor'].includes(k))fail(p,'예약된 키입니다.');safe(x,`${p}.${k}`,depth+1);}
}
const optionalIndex=(d,k,n,p)=>{if(d[k]!=null)num(d[k],`${p}.${k}`,0,n-1,true);};
const scalar=(v,p)=>{if(v!==null&&!['string','number','boolean'].includes(typeof v))fail(p,'셀에는 문자·숫자·불리언·null만 사용할 수 있습니다.');};
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
export function validateDiagramElement(type,d,p='data'){
 object(d,p);
 if(d.label!==undefined)str(d.label,`${p}.label`);
 switch(type){
 case 'state-node': if(d.removed!==undefined&&typeof d.removed!=='boolean')fail(`${p}.removed`,'삭제 표시 여부를 확인하세요.'); break;
 case 'text-label':
  if(d.text!==undefined)str(d.text,`${p}.text`);
  if(d.caption!==undefined&&str(d.caption,`${p}.caption`).length>80)fail(`${p}.caption`,'설명은 80자 이하여야 합니다.');
  break;
 case 'resource-square':{const n=num(d.instances??1,`${p}.instances`,0,10000,true);num(d.allocated??0,`${p}.allocated`,0,n,true);break;}
 case 'counter-badge':
  for(const k of ['value','min','max','delta'])if(d[k]!=null)num(d[k],`${p}.${k}`);
  if(d.displayStyle!==undefined&&!['badge','semaphore'].includes(d.displayStyle))fail(`${p}.displayStyle`,'카운터 표시 방식을 확인하세요.');
  if(d.caption!==undefined&&str(d.caption,`${p}.caption`).length>80)fail(`${p}.caption`,'설명은 80자 이하여야 합니다.');
  for(const k of ['showTokens','showInPlayback'])if(d[k]!==undefined&&typeof d[k]!=='boolean')fail(`${p}.${k}`,'표시 여부를 확인하세요.');
  break;
 case 'queue-strip':{
  const a=list(d.items??[],`${p}.items`,64);const n=num(d.capacity??a.length,`${p}.capacity`,0,64,true);
  if(a.length>n)fail(p,'항목 수가 버퍼 용량보다 많습니다.');a.forEach((x,i)=>str(x,`${p}.items[${i}]`));
  if(d.direction!==undefined&&!['ltr','rtl'].includes(d.direction))fail(p,'버퍼 방향을 확인하세요.');
  if(d.displayStyle!==undefined&&!['strip','buffer'].includes(d.displayStyle))fail(`${p}.displayStyle`,'버퍼 표시 방식을 확인하세요.');
  for(const k of ['showCount','showDirection'])if(d[k]!==undefined&&typeof d[k]!=='boolean')fail(`${p}.${k}`,'표시 여부를 확인하세요.');
  break;
 }
 case 'table-grid':{
  const h=list(d.headers??[],`${p}.headers`),r=list(d.rows??[],`${p}.rows`);h.forEach((x,i)=>str(x,`${p}.headers[${i}]`));
  const n=h.length||(Array.isArray(r[0])?r[0].length:0);
  r.forEach((row,i)=>{list(row,`${p}.rows[${i}]`);if(row.length!==n)fail(p,'표의 행과 열 개수를 맞추세요.');row.forEach(x=>scalar(x,p));});optionalIndex(d,'activeRow',r.length,p);if(d.width!==undefined)num(d.width,`${p}.width`,160,1200);break;
 }
 case 'slot-grid':{
  const a=list(d.slots??[],`${p}.slots`,64),n=num(d.slotCount??a.length,`${p}.slotCount`,0,64,true);if(a.length>n)fail(p,'칸 개수보다 값이 많습니다.');a.forEach(x=>{if(x!==null)num(x,`${p}.slots`);});for(const k of events[type])optionalIndex(d,k,n,p);break;
 }
 case 'gantt-lane':{
  const a=list(d.blocks??[],`${p}.blocks`);a.forEach((b,i)=>{object(b,`${p}.blocks[${i}]`);num(b.start,p,0);num(b.end,p,b.start);if(b.end<=b.start)fail(p,'구간 종료는 시작보다 커야 합니다.');});optionalIndex(d,'activeBlockIndex',a.length,p);if(d.axisEnd!=null)num(d.axisEnd,`${p}.axisEnd`,0);break;
 }
 case 'memory-range':{
  const start=num(d.start??0,`${p}.start`,0,Number.MAX_SAFE_INTEGER,true),size=num(d.size??16,`${p}.size`,1,Number.MAX_SAFE_INTEGER,true);
  if(!Number.isSafeInteger(start+size))fail(p,'주소 범위를 초과했습니다.');
  const segments=list(d.segments??[],`${p}.segments`),ids=new Set();let cursor=start;
  for(const s of segments){object(s,p);id(s.id,p);if(ids.has(s.id))fail(p,'구간 ID가 중복되었습니다.');ids.add(s.id);num(s.start,p,0,Number.MAX_SAFE_INTEGER,true);if(s.start!==cursor)fail(p,'구간 주소가 연속적이지 않습니다.');cursor+=num(s.size,p,1,Number.MAX_SAFE_INTEGER,true);}
  if(segments.length&&cursor!==start+size)fail(p,'전체 크기와 구간 길이 합계를 맞추세요.');refs(d.activeIds??[],ids,`${p}.activeIds`);break;
 }
 case 'process-box':{
  if(d.width!==undefined)num(d.width,`${p}.width`,280,800,true);
  for(const k of ['hiddenSectionNames','hiddenThreadIds','threadFields'])if(d[k]!==undefined){const a=list(d[k],`${p}.${k}`);a.forEach(v=>str(v,`${p}.${k}`));if(new Set(a).size!==a.length)fail(p,'표시 항목이 중복되었습니다.');}
  if(d.threadFields?.some(v=>!['pc','register','stack'].includes(v)))fail(p,'스레드 표시 항목을 확인하세요.');
  for(const k of ['threadLayout','sectionLayout'])if(d[k]!==undefined&&!['row','column'].includes(d[k]))fail(p,'가로 또는 세로 배치를 선택하세요.');
  if(d.showSharedLinks!==undefined&&typeof d.showSharedLinks!=='boolean')fail(p,'공유 관계선 표시를 확인하세요.');
  const threads=list(d.threads??[],`${p}.threads`),ids=new Set();for(const t of threads){object(t,p);id(t.id,p);if(ids.has(t.id))fail(p,'스레드 ID가 중복되었습니다.');ids.add(t.id);str(t.label,p);for(const k of ['pc','register','stack'])if(t[k]!==undefined)str(t[k],p);}
  for(const s of list(d.sections??[],`${p}.sections`)){object(s,p);str(s.name,p);str(s.value,p);}
  if(d.activeThreadId!=null&&!ids.has(d.activeThreadId))fail(p,'현재 스레드가 목록에 없습니다.');break;
 }
 case 'line-chart':{
  const series=list(d.series??[],`${p}.series`);let last=-1;const points=[];
  for(const s of series){object(s,p);const ps=list(s.points,p);last=Math.max(last,ps.length-1);for(const q of ps){object(q,p);num(q.x,p);num(q.y,p);points.push(q);}}
  for(const axis of ['x','y']){const min=d[axis+'Min'],max=d[axis+'Max'];if(min!=null)num(min,p);if(max!=null)num(max,p);if(min!=null&&max!=null&&min>=max)fail(p,'축 최대는 최소보다 커야 합니다.');
   for(const q of points)if(min!=null&&q[axis]<min||max!=null&&q[axis]>max)fail(p,'좌표가 축 범위를 벗어납니다.');
   if(d[axis+'Ticks']!=null)for(const n of list(d[axis+'Ticks'],p)){num(n,p);if(min!=null&&n<min||max!=null&&n>max)fail(p,'눈금이 축 범위를 벗어납니다.');}
  }
  for(const k of ['progressIndex','activePointIndex'])if(d[k]!=null)num(d[k],`${p}.${k}`,-1,last,true);break;
 }
 default: if(type!==undefined)fail(p,'지원하지 않는 요소 종류입니다.');
 }
}
export function validateDiagramBlob(blob,p='diagram',trace=false){
 object(blob,p);const nodes=list(blob.nodes,`${p}.nodes`,1000),edges=list(blob.edges,`${p}.edges`,4000);
 const nodeMap=new Map(),edgeIds=new Set();
 for(const n of nodes){object(n,p);id(n.id,p);if(nodeMap.has(n.id))fail(p,'요소 ID가 중복되었습니다.');nodeMap.set(n.id,n);object(n.position,p);num(n.position.x,p);num(n.position.y,p);validateDiagramElement(n.type,n.data,`${p}.nodes[${n.id}]`);}
 for(const e of edges){object(e,p);id(e.id,p);if(edgeIds.has(e.id))fail(p,'연결 ID가 중복되었습니다.');edgeIds.add(e.id);if(!nodeMap.has(e.source)||!nodeMap.has(e.target))fail(p,'연결 대상이 없습니다.');if(e.data!=null)object(e.data,p);}
 const frames=list(trace?blob.referenceTrace:blob.frames,`${p}.${trace?'referenceTrace':'frames'}`,1000);
 if(nodes.length*frames.length>250000)fail(p,'요소·장면 조합이 너무 많습니다.');
 const values=new Map(nodes.map(n=>[n.id,{...n.data}])),frameIds=new Set();
 frames.forEach((f,i)=>{
  const fp=`${p}.장면 ${i+1}`;object(f,fp);
  if(!trace){id(f.id,fp);if(frameIds.has(f.id))fail(fp,'장면 ID가 중복되었습니다.');frameIds.add(f.id);str(f.label,fp);refs(f.highlightEdges,edgeIds,fp);if(f.cursorTime!=null)num(f.cursorTime,fp,0);}
  refs(f.highlightNodes,new Set(nodeMap.keys()),fp);if(f.visibleEdges!==undefined)refs(f.visibleEdges,edgeIds,fp);
  const overrides=object((trace?f.overrides:f.nodeDataOverrides)??{},fp);
  for(const [key,patch] of Object.entries(overrides)){
   if(!nodeMap.has(key))fail(fp,'장면의 요소 참조가 없습니다.');object(patch,fp);
   for(const field of Object.keys(patch))if(!((trace?diagramFields:manualDiagramFields)[nodeMap.get(key).type]??[]).includes(field))fail(fp,`지원하지 않는 장면 필드 ${field}`);
  }
  for(const n of nodes){const d={...values.get(n.id)};for(const k of events[n.type]??[])d[k]=null;Object.assign(d,overrides[n.id]??{});validateDiagramElement(n.type,d,`${fp}.${n.id}`);values.set(n.id,d);}
 });
}
export function validateFreeDiagram(document){
 object(document,'OBO');safe(document,'OBO');
 if(new TextEncoder().encode(JSON.stringify(document)).length>1048576)fail('OBO','문서는 1MiB 이하여야 합니다.');
 if(document.schemaVersion!==undefined)fail('OBO','자유 그림의 schemaVersion을 확인하세요.');
 if(document.mode===undefined)validateDiagramBlob(document);
 else if(document.mode==='single')validateDiagramBlob(document.single,'single');
 else if(document.mode==='per_choice'){object(document.perChoice,'perChoice');for(const [key,blob]of Object.entries(document.perChoice)){id(key,'perChoice');validateDiagramBlob(blob,`perChoice.${key}`);}}
 else if(document.mode==='coding_diff'){
  validateDiagramBlob(document.codingDiff,'codingDiff',true);
  const schema=document.codingDiff;
  if(schema.sourceTraceText!==undefined){
   const parsed=parseDiagramTrace(schema.sourceTraceText,schema.nodes,schema.edges);
   if(parsed.errors.length)fail('codingDiff',parsed.errors[0].message);
   if(JSON.stringify(canonical(parsed.trace))!==JSON.stringify(canonical(schema.referenceTrace)))fail('codingDiff','출력 원문과 재생 기록이 다릅니다. 다시 편집기를 열어 확인하세요.');
  }
  // Scenes authored for a coding problem (2026-11-08): same checks as manual scenes, and the record must come from them.
  if(schema.frames!==undefined){
   validateDiagramBlob({nodes:schema.nodes,edges:schema.edges,frames:schema.frames},'codingDiff');
   schema.frames.forEach((f,i)=>{if(f.excludeFromOutput!==undefined&&typeof f.excludeFromOutput!=='boolean')fail(`codingDiff.장면 ${i+1}`,'학생 출력 포함 여부를 확인하세요.');});
   if(JSON.stringify(canonical(framesToTrace(schema.nodes,schema.frames)))!==JSON.stringify(canonical(schema.referenceTrace)))fail('codingDiff','장면과 재생 기록이 다릅니다. 다시 편집기를 열어 확인하세요.');
  }
  if(schema.outputFormat!==undefined)validateOutputFormat(schema.outputFormat,schema.nodes);
 }
 else fail('OBO','지원하지 않는 문서 모드입니다.');
 return document;
}

/** One output line per scene. Parsing failures are explicit and never become grading verdicts. */
export function parseDiagramTrace(text,nodes,edges){
 const trace=[],errors=[];
 if(typeof text!=='string'||new TextEncoder().encode(text).length>1048576)return {trace,errors:[{line:1,message:'출력 기록은 1MiB 이하의 문자열이어야 합니다.'}]};
 const ids=new Set(nodes.map(n=>n.id)),byId=new Map(nodes.map(n=>[n.id,n])),labels=new Map();
 for(const n of nodes){const label=n.data?.label;if(typeof label==='string'&&label&&!/\s/.test(label))labels.set(label,labels.has(label)?null:n.id);}
 const state={'text-label':'text','gantt-lane':'blocks','slot-grid':'slots','counter-badge':'value'};
 text.split('\n').forEach((raw,i)=>{
  const line=raw.trim();if(!line)return;
  try {
   if(trace.length>=1000)fail('출력','1000장면 이하로 줄이세요.');
   let step;
   if(line.startsWith('{')){
    const input=parseBoundedJson(line);object(input,'출력');
    if(input.v!==1||Object.keys(input).some(k=>!['v','overrides','highlightNodes','visibleEdges'].includes(k)))fail('출력','v:1 JSON 장면 형식을 확인하세요.');
    step={overrides:input.overrides??{},highlightNodes:input.highlightNodes??[],...(input.visibleEdges!==undefined?{visibleEdges:input.visibleEdges}:{})};
   }else{
    step={overrides:{},highlightNodes:[]};let previous=null;
    for(const token of line.split(/\s+/)){
     const key=ids.has(token)?token:labels.get(token);
     if(key){step.highlightNodes.push(key);previous=key;continue;}
     if(labels.has(token)&&key===null)fail('출력',`이름 ${token}이 중복됩니다. 요소 ID를 사용하세요.`);
     const field=state[byId.get(previous)?.type];if(!field)fail('출력',`인식할 수 없는 값 ${token}. 새 요소의 값은 JSON 한 줄 형식을 사용하세요.`);
     let value;try{value=parseBoundedJson(token);}catch{if(/^[\[{"]/.test(token))fail('출력','잘못된 JSON 값입니다.');value=token;}
     if(step.overrides[previous]?.[field]!==undefined)fail('출력','한 필드에 여러 값이 있습니다. 공백 있는 문자열은 JSON 한 줄 형식을 사용하세요.');
     step.overrides[previous]={[field]:value};
    }
   }
   safe(step,`출력 ${i+1}줄`);trace.push(step);
  }catch(error){errors.push({line:i+1,message:`${i+1}줄: ${error.message}`});}
 });
 if(!errors.length)try{validateDiagramBlob({nodes,edges,referenceTrace:trace},'출력',true);}catch(error){errors.push({line:1,message:error.message});}
 return {trace,errors};
}

/* ── Coding problems built from scenes and Baekjoon-style output (2026-11-08) ───────────────────────────
 * Grading stays a line comparison (text-v1). These only turn scenes into the reference record / expected
 * output and turn a learner's plain lines back into the same drawing for replay. */
const ganttGroupOf=n=>String(n.data?.ganttGroup||'기본 간트');
/** Included scenes become one record each. State values set in explanation-only scenes carry into the next included scene; their events do not. */
export function framesToTrace(nodes,frames){
 const types=new Map(nodes.map(n=>[n.id,n.type])),trace=[];let carried={};
 for(const f of frames??[]){
  const own={};
  for(const [id,patch] of Object.entries(f.nodeDataOverrides??{})){const allowed=diagramFields[types.get(id)]??[];const kept=Object.fromEntries(Object.entries(patch??{}).filter(([k])=>allowed.includes(k)));if(Object.keys(kept).length)own[id]=kept;}
  if(f.excludeFromOutput===true){for(const [id,patch] of Object.entries(own)){const state=Object.fromEntries(Object.entries(patch).filter(([k])=>!(events[types.get(id)]??[]).includes(k)));if(Object.keys(state).length)carried[id]={...carried[id],...state};}continue;}
  const overrides={};for(const id of new Set([...Object.keys(carried),...Object.keys(own)]))overrides[id]={...carried[id],...own[id]};carried={};
  trace.push({overrides,highlightNodes:[...(f.highlightNodes??[])],...(f.visibleEdges!==undefined?{visibleEdges:[...f.visibleEdges]}:{})});
 }
 return trace;
}
export const simpleOutputTypes=['slot-grid','queue-strip','counter-badge','resource-square','text-label','table-grid','state-node','gantt-lane','line-chart'];
const formatKeys=['kind','target','blank','lineIndexTarget','markChanged','markFoundFrom'];
export function validateOutputFormat(f,nodes){
 object(f,'outputFormat');
 for(const k of Object.keys(f))if(!formatKeys.includes(k))fail('outputFormat',`알 수 없는 항목 ${k}`);
 if(f.kind!=='lines-v1')fail('outputFormat','지원하지 않는 출력 형식입니다.');
 const byId=new Map(nodes.map(n=>[n.id,n])),target=byId.get(f.target);
 if(!target||!simpleOutputTypes.includes(target.type))fail('outputFormat','출력 줄이 채울 요소를 다시 고르세요.');
 if(f.blank!==undefined&&(typeof f.blank!=='string'||!/^\S{1,4}$/.test(f.blank)))fail('outputFormat','빈 칸 기호는 공백 없는 1~4글자여야 합니다.');
 for(const k of ['lineIndexTarget','markFoundFrom'])if(f[k]!==undefined&&(target.type!=='slot-grid'||byId.get(f[k])?.type!=='slot-grid'))fail('outputFormat','칸 묶음 옵션의 기준 요소를 다시 고르세요.');
 if(f.markChanged!==undefined&&(typeof f.markChanged!=='boolean'||target.type!=='slot-grid'))fail('outputFormat','바뀐 칸 표시 옵션을 확인하세요.');
 return f;
}
const numeric=t=>/^-?\d+(\.\d+)?$/.test(t);
const slotTotal=d=>Number.isInteger(d?.slotCount)?d.slotCount:Array.isArray(d?.slots)?d.slots.length:0;
const stateLabels=nodes=>{const m=new Map();for(const n of nodes)if(n.type==='state-node'){const l=n.data?.label;if(typeof l==='string'&&l&&!/\s/.test(l))m.set(l,m.has(l)?null:n.id);}return m;};
/** Plain lines (one scene per line) -> the record steps the existing player and checks understand. */
export function parseSimpleOutput(text,nodes,edges,f){
 const trace=[],errors=[];
 try{validateOutputFormat(f,nodes);}catch(error){return {trace,errors:[{line:1,message:error.message}]};}
 if(typeof text!=='string'||new TextEncoder().encode(text).length>1048576)return {trace,errors:[{line:1,message:'출력은 1MiB 이하여야 합니다.'}]};
 const byId=new Map(nodes.map(n=>[n.id,n])),target=byId.get(f.target),d0=target.data??{},blank=f.blank??'-';
 const lines=text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
 if(lines.length>1000)return {trace,errors:[{line:1001,message:'1000줄 이하로 줄이세요.'}]};
 const labels=stateLabels(nodes),lanes=nodes.filter(n=>n.type==='gantt-lane'&&ganttGroupOf(n)===ganttGroupOf(target));
 let previous=Array.isArray(d0.slots)?[...d0.slots]:[];const rows=Array.isArray(d0.rows)?d0.rows.map(r=>[...r]):[],points=[],blocks=new Map(lanes.map(l=>[l.id,[]]));
 lines.forEach((line,i)=>{
  const at=`${i+1}줄`;
  try{
   const tokens=line.split(/\s+/),o={},highlightNodes=[];
   const one=()=>{if(tokens.length!==1||!numeric(tokens[0]))fail(at,'숫자 하나만 쓰세요.');return Number(tokens[0]);};
   switch(target.type){
    case 'slot-grid':{
     const n=slotTotal(d0);if(tokens.length>n)fail(at,`칸은 ${n}개입니다.`);
     const slots=Array.from({length:n},(_,k)=>{const t=tokens[k];if(t===undefined||t===blank)return null;if(!numeric(t))fail(at,`칸 값은 숫자나 ${blank}만 쓸 수 있습니다.`);return Number(t);});
     o[target.id]={slots};
     if(f.lineIndexTarget&&i<slotTotal(byId.get(f.lineIndexTarget).data))o[f.lineIndexTarget]={activeSlotIndex:i};
     if(f.markChanged){const changed=slots.findIndex((v,k)=>v!==(previous[k]??null));
      if(changed>=0)o[target.id].faultSlotIndex=changed;
      else if(f.markFoundFrom){const want=byId.get(f.markFoundFrom).data?.slots?.[i];const k=want==null?-1:slots.indexOf(want);if(k>=0)o[target.id].hitSlotIndex=k;}}
     previous=slots;break;}
    case 'queue-strip':o[target.id]={items:tokens.length===1&&tokens[0]===blank?[]:tokens};break;
    case 'counter-badge':o[target.id]={value:one()};break;
    case 'resource-square':o[target.id]={allocated:one()};break;
    case 'text-label':o[target.id]={text:line};break;
    case 'table-grid':{
     const width=(Array.isArray(d0.headers)&&d0.headers.length)||rows[0]?.length||tokens.length;
     if(tokens.length!==width)fail(at,`표의 한 행은 ${width}칸입니다.`);
     while(rows.length<=i)rows.push(Array(width).fill(''));
     rows[i]=tokens.map(t=>numeric(t)?Number(t):t);o[target.id]={rows:rows.map(r=>[...r]),activeRow:i};break;}
    case 'state-node':
     for(const t of tokens){const key=byId.get(t)?.type==='state-node'?t:labels.get(t);if(!key)fail(at,`상태 ${t}을(를) 찾을 수 없습니다.`);highlightNodes.push(key);}
     break;
    case 'gantt-lane':{
     if(tokens.length!==3)fail(at,'ID 시작 종료 세 값을 쓰세요.');
     const [who,a,b]=tokens,lane=lanes.find(l=>String(l.data?.trackId??'')===who);if(!lane)fail(at,`${who} 줄을 찾을 수 없습니다.`);
     if(!numeric(a)||!numeric(b)||Number(b)<=Number(a)||Number(a)<0)fail(at,'시작과 종료를 확인하세요. 종료가 시작보다 커야 합니다.');
     if(i===0)for(const l of lanes)o[l.id]={blocks:[]};
     const colorKey=Array.isArray(lane.data?.blocks)?lane.data.blocks[0]?.colorKey:undefined,list=blocks.get(lane.id);
     list.push({start:Number(a),end:Number(b),...(colorKey?{colorKey}:{})});
     o[lane.id]={blocks:list.map(x=>({...x})),activeBlockIndex:list.length-1};break;}
    case 'line-chart':{
     if(tokens.length!==2||!numeric(tokens[0])||!numeric(tokens[1]))fail(at,'x y 두 숫자를 쓰세요.');
     points.push({x:Number(tokens[0]),y:Number(tokens[1])});
     const s0=Array.isArray(d0.series)&&d0.series[0]?d0.series[0]:{name:'출력',color:'#0f766e'};
     o[target.id]={series:[{...s0,points:points.map(p=>({...p}))}],progressIndex:points.length-1,activePointIndex:points.length-1};break;}
   }
   const step={overrides:o,highlightNodes};safe(step,`출력 ${at}`);trace.push(step);
  }catch(error){errors.push({line:i+1,message:error.message.startsWith(at)?error.message:`${at}: ${error.message}`});}
 });
 if(!errors.length)try{validateDiagramBlob({nodes,edges,referenceTrace:trace},'출력',true);}catch(error){errors.push({line:1,message:error.message});}
 return {trace,errors};
}
function sceneStates(nodes,frames){
 const values=new Map(nodes.map(n=>[n.id,{...(n.data??{})}])),out=[];
 for(const f of frames){const o=f.nodeDataOverrides??{};for(const n of nodes){const d={...values.get(n.id)};for(const k of events[n.type]??[])d[k]=null;Object.assign(d,o[n.id]??{});values.set(n.id,d);}out.push(new Map(values));}
 return out;
}
/** Scenes -> the expected plain output (one line per included scene) and why a scene cannot be written as one line. */
export function formatSimpleOutput(nodes,frames,f){
 const issues=[],lines=[];
 try{validateOutputFormat(f,nodes);}catch(error){return {lines,text:'',issues:[error.message]};}
 const byId=new Map(nodes.map(n=>[n.id,n])),target=byId.get(f.target),blank=f.blank??'-',states=sceneStates(nodes,frames??[]);
 const lanes=nodes.filter(n=>n.type==='gantt-lane'&&ganttGroupOf(n)===ganttGroupOf(target));
 const word=(v,scene)=>{const s=String(v??'');if(!s||/\s/.test(s))issues.push(`장면 ${scene+1}: 값 ‘${s}’에 띄어쓰기가 있거나 비어 있어 한 줄로 나눌 수 없습니다.`);return s;};
 (frames??[]).forEach((fr,i)=>{
  if(fr.excludeFromOutput===true)return;
  const d=states[i].get(target.id)??{};let line=null;
  switch(target.type){
   case 'slot-grid':{const n=slotTotal(d);line=Array.from({length:n},(_,k)=>d.slots?.[k]==null?blank:String(d.slots[k])).join(' ');break;}
   case 'queue-strip':line=Array.isArray(d.items)&&d.items.length?d.items.map(v=>word(v,i)).join(' '):blank;break;
   case 'counter-badge':line=String(d.value??0);break;
   case 'resource-square':line=String(d.allocated??0);break;
   case 'text-label':line=String(d.text??d.label??'');if(!line.trim())issues.push(`장면 ${i+1}: 문구가 비어 있습니다.`);break;
   case 'table-grid':if(!Number.isInteger(d.activeRow))issues.push(`장면 ${i+1}: 강조 행이 없어 출력 줄을 만들 수 없습니다.`);else line=(d.rows?.[d.activeRow]??[]).map(v=>word(v,i)).join(' ');break;
   case 'state-node':{const ids=(fr.highlightNodes??[]).filter(id=>byId.get(id)?.type==='state-node');
    if(!ids.length)issues.push(`장면 ${i+1}: 강조한 상태가 없습니다.`);else line=ids.map(id=>{const l=byId.get(id).data?.label;return typeof l==='string'&&l&&!/\s/.test(l)?l:id;}).join(' ');break;}
   case 'gantt-lane':{const active=lanes.map(l=>({l,d:states[i].get(l.id)})).filter(x=>Number.isInteger(x.d?.activeBlockIndex));
    if(active.length!==1)issues.push(`장면 ${i+1}: 강조한 간트 구간이 ${active.length?'여러 개라':'없어'} 한 줄로 쓸 수 없습니다.`);
    else{const b=active[0].d.blocks?.[active[0].d.activeBlockIndex];line=`${word(active[0].l.data?.trackId,i)} ${b?.start} ${b?.end}`;}break;}
   case 'line-chart':{const k=Number.isInteger(d.activePointIndex)&&d.activePointIndex>=0?d.activePointIndex:d.progressIndex;const p=d.series?.[0]?.points?.[k];
    if(!p)issues.push(`장면 ${i+1}: 현재 점이 없어 출력 줄을 만들 수 없습니다.`);else line=`${p.x} ${p.y}`;break;}
  }
  if(line!==null)lines.push({scene:i,text:line});
 });
 return {lines,text:lines.map(l=>l.text).join('\n'),issues:[...new Set(issues)]};
}
