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
