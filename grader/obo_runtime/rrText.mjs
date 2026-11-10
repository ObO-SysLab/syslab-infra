/** Student-facing RR intervals. No presentation IDs, reference substitution, or code tracing. */
import {run} from './index.mjs';
import {ensure,byteSize,equal} from './validation.mjs';
export const RR_TEXT='rr-segments-v1';
const idPattern=/^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const reserved=new Set(['IDLE','__proto__','prototype','constructor']);
function lines(text,max){
  ensure(typeof text==='string'&&byteSize(text)<=1048576,'입출력은 1MiB 이하의 문자열이어야 합니다.');
  const result=text.trim().split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  ensure(result.length>0&&result.length<=max,`비어 있지 않은 ${max}줄 이하의 기록이 필요합니다.`);
  return result.map(line=>line.split(/[ \t]+/));
}
function integer(token,min,max,label){
  ensure(typeof token==='string'&&/^[0-9]+$/.test(token),`${label}: 정수를 입력하세요.`);
  const value=Number(token);ensure(Number.isSafeInteger(value)&&value>=min&&value<=max,`${label}: ${min}~${max} 범위여야 합니다.`);return value;
}
export function parseRrInput(text){
  const rows=lines(text,33);ensure(rows[0].length===2,'첫 줄은 프로세스 수와 시간 할당량입니다.');
  const n=integer(rows[0][0],1,32,'프로세스 수'),quantum=integer(rows[0][1],1,2000,'시간 할당량');
  ensure(rows.length===n+1,'프로세스 수와 입력 행 수가 다릅니다.');
  const ids=new Set();
  const jobs=rows.slice(1).map((row,index)=>{
    ensure(row.length===3,`${index+2}줄: ID 도착시각 CPU실행량을 입력하세요.`);
    const [id,a,b]=row;ensure(idPattern.test(id)&&!reserved.has(id)&&!ids.has(id),'프로세스 ID가 잘못되었거나 중복되었습니다.');ids.add(id);
    return {id,arrival:integer(a,0,2000,'도착시각'),bursts:[integer(b,1,2000,'CPU실행량')]};
  });
  ensure(Math.max(...jobs.map(j=>j.arrival))+jobs.reduce((sum,j)=>sum+j.bursts[0],0)<=2000,'최대 도착시각과 총 CPU실행량의 합은 2000 이하여야 합니다.');
  return {jobs,policy:'RR',quantum};
}
export function rrInputText(input){
  ensure(input?.policy==='RR'&&Array.isArray(input.jobs)&&input.jobs.every(j=>Array.isArray(j.bursts)&&j.bursts.length===1),'구간 출력 문제는 I/O 없는 RR 모델만 지원합니다.');
  const text=[`${input.jobs.length} ${input.quantum}`,...input.jobs.map(j=>`${j.id} ${j.arrival} ${j.bursts[0]}`)].join('\n');parseRrInput(text);return text;
}
export function rrOutputText(segments){return segments.map(s=>`${s.actor??'IDLE'} ${s.start} ${s.end}`).join('\n');}
export function parseRrOutput(text,input){
  const ids=new Set(input.jobs.map(j=>j.id));let end=0;
  return lines(text,4096).map((row,index)=>{
    ensure(row.length===3,`${index+1}줄: ID 시작 종료 형식이어야 합니다.`);
    const [id,a,b]=row;ensure(id==='IDLE'||ids.has(id),`${index+1}줄: 입력에 없는 프로세스 ID입니다.`);
    const start=integer(a,0,2000,'시작'),next=integer(b,1,2000,'종료');
    ensure(next>start,'종료는 시작보다 커야 합니다.');ensure(start===end,'구간은 0부터 빈틈이나 겹침 없이 이어져야 합니다. 유휴는 IDLE로 출력하세요.');end=next;
    return {actor:id==='IDLE'?null:id,start,end:next};
  });
}
export function normalizeSegments(segments){
  const result=[];for(const segment of segments){const last=result.at(-1);if(last&&last.actor===segment.actor&&last.end===segment.start)last.end=segment.end;else result.push({...segment});}return result;
}
export function referenceRrText(text){const input=parseRrInput(text);return {input,reference:run('RR',input,{record:false})};}
export function gradeRrText(inputText,stdout){
  const {input,reference}=referenceRrText(inputText);
  try{
    const segments=parseRrOutput(stdout,input);
    const correct=equal(normalizeSegments(segments),normalizeSegments(reference.result.segments));
    return {correct,kind:correct?'correct':'wrong',traceValid:false,
      message:correct?'RR 실행 구간이 올바릅니다.':'출력한 실행 구간이 RR 순서 또는 실행 시간과 다릅니다.',
      reported:{v:1,result:{segments}}};
  }catch(error){return {correct:false,kind:'wrong',traceValid:false,message:error.message,reported:null};}
}
