import contracts from './contracts.mjs';
import {OboValidationError,ensure,clone,checkShape,parseBoundedJson,equal,byteSize,unique} from './validation.mjs';
import {cpuScheduling} from './cpu.mjs';
import {pageReplacement,memoryPressure,addressTranslation,contiguousAllocation} from './memory.mjs';
import {lifecycle,contextSwitch,processStructure,ipc,inodeAccess,diskScheduling,kernelFlow} from './flows.mjs';
import {resourceAllocation,banker,boundedBuffer,philosophers} from './synchronization.mjs';

export {OboValidationError,parseBoundedJson,checkShape};
export const registry=contracts.scenarios;
export const modelList=Object.entries(registry).map(([id,s])=>({...s.metadata,id}));
const engines={'lifecycle':lifecycle,'context-switch':contextSwitch,'process-structure':processStructure,ipc,
  'cpu-scheduling':cpuScheduling,'page-replacement':pageReplacement,'memory-pressure':memoryPressure,
  'address-translation':addressTranslation,'contiguous-allocation':contiguousAllocation,
  'resource-allocation':resourceAllocation,banker,'bounded-buffer':boundedBuffer,'dining-philosophers':philosophers,
  'inode-access':inodeAccess,'disk-scheduling':diskScheduling,'kernel-flow':kernelFlow};
export function defaultInput(id) {ensure(Object.hasOwn(registry,id),'지원하지 않는 시나리오입니다.');return clone(registry[id].inputExample);}
export function run(id,input,{record=true,maxEvents=10000,maxBytes=1048576,preferredSequence=null}={}) {
  ensure(Object.hasOwn(registry,id),'지원하지 않는 시나리오입니다.');
  ensure(Number.isInteger(maxEvents)&&maxEvents>=1&&maxEvents<=10000,'사건 한도가 잘못되었습니다.');
  ensure(Number.isInteger(maxBytes)&&maxBytes>=1&&maxBytes<=4194304,'기록 한도가 잘못되었습니다.');
  const spec=registry[id];ensure(byteSize(input)<=1048576,'입력 크기가 1MiB를 초과했습니다.');checkShape(spec.inputSchema,input);
  let operations=0,count=0,bytes=0;const events=[];
  const ctx={id,tick(){ensure(++operations<=maxEvents,'계산 한도를 넘었습니다. 입력 크기나 사건 수를 줄이세요.');},
    emit(kind,actor,data){ensure(count<maxEvents,'기록할 사건이 너무 많습니다.');const event={step:count++,kind,actor,data};checkShape(spec.eventSchema,event);
      if(record){bytes+=byteSize(event)+1;ensure(bytes<=maxBytes,'실행 기록이 너무 큽니다. 입력 크기를 줄이세요.');events.push(clone(event));}}};
  const result=engines[spec.family](clone(input),ctx,preferredSequence);checkShape(spec.resultSchema,result,'$.result');
  ensure(byteSize(result)<=maxBytes,'결과가 너무 큽니다.');
  if(record)ensure(byteSize({v:1,result,events})<=maxBytes,'실행 기록과 결과의 합이 허용 크기를 초과했습니다.');
  return {id,modelVersion:1,result,events,stats:{events:count,bytes:record?byteSize({v:1,result,events}):byteSize({v:1,result})}};
}
function normalizeResult(id,result) {
  const value=clone(result);
  if(id==='S3'){unique(value.deadlocked,'교착 집합이 중복되었습니다.');value.deadlocked.sort();}
  if(['G2','RR','PS'].includes(id)) {
    const merged=[];
    for(const segment of value.segments) {ensure(segment.end>segment.start,'실행 구간 길이는 양수여야 합니다.');const last=merged.at(-1);
      if(last&&last.actor===segment.actor&&last.end===segment.start)last.end=segment.end;else merged.push(clone(segment));}
    value.segments=merged;
  }return value;
}
function compareTrace(id,input,payload) {
  const spec=registry[id];checkShape(spec.processOutputSchema,payload);
  payload.events.forEach((e,i)=>ensure(e.step===i,'사건 번호가 누락되거나 중복되었습니다.'));
  const config=clone(input);
  if(id==='DP'&&config.mode==='findSchedule')config.order=payload.events.map(e=>e.actor);
  const expected=run(id,config,{preferredSequence:id==='BK'&&payload.result.safe?payload.result.sequence:null});
  ensure(equal(normalizeResult(id,payload.result),normalizeResult(id,expected.result)),'최종 결과가 올바르지 않습니다.');
  ensure(payload.events.length===expected.events.length,'필수 사건이 누락되었거나 추가 사건이 있습니다.');
  for(let i=0;i<expected.events.length;i++)ensure(equal(payload.events[i],expected.events[i]),`사건 ${i}의 전이 또는 관측값이 올바르지 않습니다.`);
  return expected;
}
/** A reported trace is a certificate, not automatic instrumentation of student code. */
export function grade(id,input,mode,text) {
  let expected;
  try {
    ensure(['result','process'].includes(mode),'채점 모드가 잘못되었습니다.');
    ensure(!(id==='DP'&&input.mode==='findSchedule'&&mode==='result'),'유효 일정 찾기는 과정형에서만 사용할 수 있습니다.');
    expected=run(id,input,{record:false});
  }catch(e){return {correct:false,kind:'configuration',message:e.message,traceValid:false};}
  try {
    const payload=parseBoundedJson(text);const spec=registry[id];checkShape(mode==='result'?spec.resultOutputSchema:spec.processOutputSchema,payload);
    if(mode==='process'){compareTrace(id,input,payload);return {correct:true,kind:'correct',message:'결과와 필수 과정을 충족했습니다.',traceValid:true};}
    if(id==='BK'&&payload.result.safe)expected=run(id,input,{record:false,preferredSequence:payload.result.sequence});
    ensure(equal(normalizeResult(id,payload.result),normalizeResult(id,expected.result)),'결과가 올바르지 않습니다.');
    let traceValid=false,traceMessage=null;
    if(Object.hasOwn(payload,'events')){try{compareTrace(id,input,payload);traceValid=true;}catch(e){traceMessage=e.message;}}
    return {correct:true,kind:'correct',message:'결과가 올바릅니다.',traceValid,traceMessage};
  }catch(e){return {correct:false,kind:'wrong',message:e.message,traceValid:false};}
}
