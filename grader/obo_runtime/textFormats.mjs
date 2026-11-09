/** Baekjoon-style text input/output for every rule model (text-v1, 2026-11-08).
 * Input text -> the engine's JSON input; engine run -> expected output lines; learner stdout graded by tokens
 * (spacing and blank lines ignored) or by meaning where several answers are valid. No IDs or values of
 * hidden tests appear in messages. The JSON formats (json-v1, rr-segments-v1) are unchanged. */
import {run,registry,checkShape} from './index.mjs';
import {ensure,byteSize} from './validation.mjs';
export const TEXT_FORMAT='text-v1';
const ID=/^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const BIG=Number.MAX_SAFE_INTEGER;
const fail=message=>{throw new Error(message);};

/* ── reading input text ───────────────────────────────────────────── */
function reader(text){
 ensure(typeof text==='string'&&byteSize(text)<=1048576,'입력은 1MiB 이하의 문자열이어야 합니다.');
 const lines=text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean).map(l=>l.split(/\s+/));
 let at=0;
 const r={
  line(count,label){if(at>=lines.length)fail(`${at+1}번째 줄(${label})이 없습니다.`);const t=lines[at++];if(count!=null&&t.length!==count)fail(`${at}번째 줄(${label})에는 값이 ${count}개 있어야 합니다.`);return t;},
  lineAtLeast(min,label){const t=r.line(null,label);if(t.length<min)fail(`${at}번째 줄(${label})의 값이 부족합니다.`);return t;},
  int(token,label,min=0,max=BIG){if(!/^-?\d+$/.test(token??''))fail(`${at}번째 줄: ${label}은(는) 정수여야 합니다.`);const v=Number(token);if(!Number.isSafeInteger(v))fail(`${at}번째 줄: ${label}이(가) 너무 큽니다.`);if(v<min||v>max)fail(`${at}번째 줄: ${label}은(는) ${min}~${max} 범위여야 합니다.`);return v;},
  id(token,label){if(!ID.test(token??''))fail(`${at}번째 줄: ${label}은(는) 영문자로 시작하는 이름이어야 합니다.`);return token;},
  word(token,allowed,label){if(!allowed.includes(token))fail(`${at}번째 줄: ${label}은(는) ${allowed.join('·')} 중 하나여야 합니다.`);return token;},
  value(token){return /^-?\d+$/.test(token)?Number(token):token==='true'?true:token==='false'?false:token==='-'?null:token;},
  count(label,max=1000){return r.int(r.line(1,label)[0],label,0,max);},
  end(){if(at!==lines.length)fail(`${at+1}번째 줄부터 남는 줄이 있습니다.`);},
  atEnd(){return at>=lines.length;},
  peek(){return at<lines.length?lines[at]:null;},
  pairs(tokens,label){if(tokens.length%2)fail(`${at}번째 줄: ${label} 이름과 값의 수가 맞지 않습니다.`);const o={};for(let i=0;i<tokens.length;i+=2)o[r.id(tokens[i],`${label} 이름`)]=r.value(tokens[i+1]);return o;},
 };
 return r;
}
const word=v=>{const s=v===null?'-':String(v);if(!s||/\s/.test(s))fail(`값 ‘${s}’에 공백이 있어 텍스트 형식으로 쓸 수 없습니다. JSON 형식을 쓰세요.`);return s;};
const join=(...parts)=>parts.flat().map(word).join(' ');
const need=(condition,message)=>{if(!condition)fail(`텍스트 형식으로 쓸 수 없는 입력입니다: ${message}. JSON 형식을 쓰세요.`);};
/** A value written as a token must read back as the same value (2026-11-09): "7", "true", "-" as strings would not. */
const val=v=>{need(!(typeof v==='string'&&(/^-?\d+$/.test(v)||v==='true'||v==='false'||v==='-')),`문자열 값 ‘${v}’은(는) 숫자·참거짓·빈 값과 구분되지 않음`);return v;};
const named=o=>Object.entries(o).flatMap(([k,v])=>{need(ID.test(k),`이름 ${k}은(는) 영문자로 시작해야 함`);return [k,val(v)];});
/** Context switch and kernel contexts: pc first, then any other registers as name value pairs (2026-11-09). */
const withPc=(id,o)=>{need(Number.isSafeInteger(o.pc)&&o.pc>=0,'0 이상의 정수 pc가 필요함');const{pc,...rest}=o;return join(id,pc,named(rest));};
const readPc=(r,t)=>({pc:r.int(t[1],'pc',0,BIG),...r.pairs(t.slice(2),'레지스터')});

/* ── CPU scheduling: segments compared by meaning (merge/split, idle) ─ */
const cpuPolicies={G2:['FCFS','SJF','SRTF'],RR:['RR'],PS:['PS','PS-P']};
function cpu(id){
 const withPriority=id==='PS',head=id==='RR'?'N Q':'N 정책';
 return {
  inputHelp:`첫 줄은 ${id==='RR'?'프로세스 수 N과 시간 할당량 Q':`프로세스 수 N과 정책(${cpuPolicies[id].join('·')})`}입니다. 다음 N줄은 “ID 도착시각${withPriority?' 우선순위':''} CPU실행량 [I/O시간 CPU실행량 …]”입니다.`,
  outputHelp:'실행 구간마다 한 줄에 “ID 시작 종료”를 씁니다. CPU가 쉬는 구간의 ID는 IDLE입니다. 같은 ID의 이어진 구간은 합치거나 나눠 써도 됩니다.',
  toText(x){need(cpuPolicies[id].includes(x.policy),'정책');return [id==='RR'?join(x.jobs.length,x.quantum):join(x.jobs.length,x.policy),
   ...x.jobs.map(j=>{need(!withPriority||Number.isInteger(j.priority),'우선순위');return join(j.id,j.arrival,...(withPriority?[j.priority]:[]),...j.bursts);})].join('\n');},
  parse(text){const r=reader(text),h=r.line(2,head),n=r.int(h[0],'프로세스 수',1,32);
   const quantum=id==='RR'?r.int(h[1],'시간 할당량',1,1000):1,policy=id==='RR'?'RR':r.word(h[1],cpuPolicies[id],'정책');
   const jobs=Array.from({length:n},()=>{const t=r.lineAtLeast(withPriority?4:3,'프로세스'),k=withPriority?3:2;
    const bursts=t.slice(k).map(v=>r.int(v,'실행량',0,BIG));if(bursts.length%2===0)fail('CPU 실행량과 I/O 시간은 CPU로 시작하고 끝나야 합니다.');
    return {id:r.id(t[0],'ID'),arrival:r.int(t[1],'도착시각',0,BIG),bursts,...(withPriority?{priority:r.int(t[2],'우선순위',-BIG,BIG)}:{})};});
   r.end();return {jobs,policy,quantum};},
  expected(run){return segments(run.result.segments).map(s=>`${s.actor??'IDLE'} ${s.start} ${s.end}`);},
  compare(expected,actual,{reference}){
   const mine=actual.map(t=>{if(t.length!==3||!/^\d+$/.test(t[1])||!/^\d+$/.test(t[2])||Number(t[2])<=Number(t[1]))fail('형식');return {actor:t[0]==='IDLE'?null:t[0],start:Number(t[1]),end:Number(t[2])};});
   return JSON.stringify(segments(mine,true))===JSON.stringify(segments(reference.result.segments));
  },
 };
}
/** Fill gaps with idle and merge neighbours with the same actor. */
function segments(list,strict=false){
 const out=[];let time=0;
 for(const s of list){
  if(strict&&s.start!==time)fail('구간');
  if(s.start>time)out.push({actor:null,start:time,end:s.start});
  const last=out.at(-1);if(last&&last.actor===(s.actor??null)&&last.end===s.start)last.end=s.end;else out.push({actor:s.actor??null,start:s.start,end:s.end});
  time=s.end;
 }
 return out;
}

/* ── page replacement family ──────────────────────────────────────── */
const pagePolicies={P2:['FIFO','LRU'],OPT:['FIFO','LRU','OPT']};
function pages(id){return {
 inputHelp:`첫 줄은 참조 수 N, 프레임 수, 정책(${pagePolicies[id].join('·')})입니다. 둘째 줄은 참조열 N개입니다. 처음부터 프레임에 페이지가 있으면 셋째 줄에 “개수 K 페이지…”를 씁니다.`,
 outputHelp:'참조마다 “페이지 Hit|Miss 프레임1 프레임2 …”를 씁니다. 빈 프레임은 -입니다. 마지막 줄은 Miss 수입니다.',
 toText(x){return [join(x.refs.length,x.frameCount,x.policy),join(x.refs),...(x.initial?.length?[join(x.initial.length,x.initial)]:[])].join('\n');},
 parse(text){const r=reader(text),h=r.line(3,'참조 수 프레임 수 정책'),n=r.int(h[0],'참조 수',1,1000),f=r.int(h[1],'프레임 수',1,64),policy=r.word(h[2],pagePolicies[id],'정책');
  const refs=r.line(n,'참조열').map(v=>r.int(v,'페이지',0,BIG));let initial=[];
  if(!r.atEnd()){const c=r.lineAtLeast(1,'초기 프레임'),k=r.int(c[0],'초기 페이지 수',0,f);if(c.length!==k+1)fail('초기 페이지 수와 값 수가 다릅니다.');initial=c.slice(1).map(v=>r.int(v,'페이지',0,BIG));}
  r.end();return {refs,frameCount:f,policy,initial};},
 expected(run,input){return [...run.events.map(e=>join(e.data.page,e.data.hit?'Hit':'Miss',...Array.from({length:input.frameCount},(_,i)=>e.data.frames[i]??null))),String(run.result.faults)];},
};}

/* ── models with a final or per-step answer (token comparison) ────── */
const formats={
 G2:cpu('G2'),RR:cpu('RR'),PS:cpu('PS'),P2:pages('P2'),OPT:pages('OPT'),
 ST2:{
  inputHelp:'첫 줄은 프로세스 수 P, 다음 P줄은 “ID 처음상태”(New·Ready·Running·Waiting·Terminated)입니다. 다음 줄은 사건 수 A, 다음 A줄은 “ID 사건”(create·dispatch·io·completeIO·preempt·finish)입니다.',
  outputHelp:'사건마다 “ID 이전상태 다음상태”를 씁니다.',
  toText(x){const ids=Object.keys(x.processes);return [String(ids.length),...ids.map(i=>join(i,x.processes[i])),String(x.actions.length),...x.actions.map(a=>join(a.id,a.event))].join('\n');},
  parse(text){const r=reader(text),states=['New','Ready','Running','Waiting','Terminated'],events=['create','dispatch','io','completeIO','preempt','finish'];
   const processes={};for(let i=r.count('프로세스 수',32);i>0;i--){const t=r.line(2,'ID 상태');processes[r.id(t[0],'ID')]=r.word(t[1],states,'상태');}
   const actions=Array.from({length:r.count('사건 수')},()=>{const t=r.line(2,'ID 사건');return {id:r.id(t[0],'ID'),event:r.word(t[1],events,'사건')};});r.end();return {processes,actions};},
  expected(run){return run.events.map(e=>join(e.actor,e.data.before,e.data.after));},
 },
 CS:{
  inputHelp:'첫 줄은 프로세스 수 N, 다음 N줄은 “ID pc [레지스터이름 값 …]”입니다. 다음 줄은 처음 실행할 ID, 다음 줄은 교환 수 M, 마지막 줄은 교환할 순서 M개입니다.',
  outputHelp:'교환마다 “저장한ID 복원한ID 복원한pc”를 씁니다.',
  toText(x){const ids=Object.keys(x.pcbs);return [String(ids.length),...ids.map(i=>withPc(i,x.pcbs[i])),x.initialId,String(x.order.length),join(x.order)].join('\n');},
  parse(text){const r=reader(text),pcbs={};for(let i=r.count('프로세스 수',32);i>0;i--){const t=r.lineAtLeast(2,'ID pc');pcbs[r.id(t[0],'ID')]=readPc(r,t);}
   const initialId=r.id(r.line(1,'처음 ID')[0],'처음 ID'),m=r.count('교환 수');const order=m?r.line(m,'교환 순서').map(v=>r.id(v,'ID')):[];r.end();return {pcbs,initialId,order};},
  expected(run){const out=[];let saved=null;for(const e of run.events){if(e.kind==='save')saved=e.actor;if(e.kind==='select')out.push(join(saved,e.actor,e.data.registers.pc));}return out;},
 },
 P3:{
  inputHelp:'첫 줄은 참조 수 N과 정책(FIFO·LRU·OPT), 둘째 줄은 참조열 N개, 셋째 줄은 비교할 프레임 수 개수 C와 프레임 수 C개입니다.',
  outputHelp:'프레임 수마다 “프레임수 Miss수”를 입력 순서대로 씁니다.',
  toText(x){return [join(x.refs.length,x.policy),join(x.refs),join(x.capacities.length,x.capacities)].join('\n');},
  parse(text){const r=reader(text),h=r.line(2,'참조 수 정책'),n=r.int(h[0],'참조 수',1,1000),policy=r.word(h[1],['FIFO','LRU','OPT'],'정책');
   const refs=r.line(n,'참조열').map(v=>r.int(v,'페이지',0,BIG)),c=r.lineAtLeast(2,'프레임 수 목록'),k=r.int(c[0],'개수',0,BIG);if(c.length!==k+1)fail('프레임 수 개수와 값 수가 다릅니다.');
   r.end();return {refs,capacities:c.slice(1).map(v=>r.int(v,'프레임 수',1,256)),policy};},
  expected(run,input){return input.capacities.map(c=>join(c,run.result.faultsByCapacity[c]));},
 },
 TH:{
  inputHelp:'첫 줄은 프로세스 수 P, 작업집합 창 크기, 전체 프레임 예산입니다. 다음 P줄은 “ID 할당프레임수”, 다음 줄은 참조 수 N, 다음 N줄은 “ID 페이지”입니다.',
  outputHelp:'프로세스마다 입력 순서대로 “ID Miss수 작업집합크기”를 씁니다.',
  toText(x){const ids=Object.keys(x.quotas);return [join(ids.length,x.window,x.frameBudget),...ids.map(i=>join(i,x.quotas[i])),String(x.stream.length),...x.stream.map(s=>join(s.id,s.page))].join('\n');},
  parse(text){const r=reader(text),h=r.line(3,'프로세스 수 창 예산'),p=r.int(h[0],'프로세스 수',1,32),quotas={};
   for(let i=0;i<p;i++){const t=r.line(2,'ID 할당');quotas[r.id(t[0],'ID')]=r.int(t[1],'할당',0,BIG);}
   const stream=Array.from({length:r.count('참조 수')},()=>{const t=r.line(2,'ID 페이지');return {id:r.id(t[0],'ID'),page:r.int(t[1],'페이지',0,BIG)};});
   r.end();return {stream,quotas,window:r.int(h[1],'창 크기',1,1000),frameBudget:r.int(h[2],'프레임 예산',0,1024)};},
  expected(run,input){return Object.keys(input.quotas).map(i=>{const p=run.result.processes[i];return join(i,p.faults,p.workingSet);});},
 },
 TLB:{
  inputHelp:'첫 줄은 페이지 크기, TLB 칸 수, 페이지 수 P입니다. 둘째 줄은 페이지 0부터의 프레임 번호 P개(메모리에 없으면 -), 셋째 줄은 주소 수 A, 넷째 줄은 가상 주소 A개입니다. 처음 TLB에 내용이 있으면 마지막 줄에 “tlb K 페이지 프레임 …”을 씁니다.',
  outputHelp:'주소마다 “Hit|Miss 물리주소”를 씁니다. Hit/Miss는 TLB 조회 결과이고, 페이지가 메모리에 없으면 물리주소 자리에 -를 씁니다.',
  toText(x){const tlb=x.initialTlb??[];return [join(x.pageSize,x.capacity,x.table.length),join(x.table),String(x.addresses.length),...(x.addresses.length?[join(x.addresses)]:[]),...(tlb.length?[join('tlb',tlb.length,tlb.flatMap(e=>[e.page,e.frame]))]:[])].join('\n');},
  parse(text){const r=reader(text),h=r.line(3,'페이지 크기 TLB 칸 수 페이지 수'),p=r.int(h[2],'페이지 수',1,4096);
   const table=r.line(p,'페이지 표').map(v=>v==='-'?null:r.int(v,'프레임',0,BIG)),a=r.count('주소 수');const addresses=a?r.line(a,'주소').map(v=>r.int(v,'주소',0,BIG)):[];
   let initialTlb=[];if(!r.atEnd()){const t=r.lineAtLeast(2,'처음 TLB');r.word(t[0],['tlb'],'마지막 줄');const k=r.int(t[1],'처음 TLB 칸 수',0,256);if(t.length!==2+2*k)fail('처음 TLB의 페이지·프레임 수가 맞지 않습니다.');
    initialTlb=Array.from({length:k},(_,i)=>({page:r.int(t[2+2*i],'페이지',0,BIG),frame:r.int(t[3+2*i],'프레임',0,BIG)}));}
   r.end();return {addresses,pageSize:r.int(h[0],'페이지 크기',1,BIG),table,capacity:r.int(h[1],'TLB 칸 수',0,256),initialTlb};},
  expected(run){return run.result.translations.map(t=>join(t.hit?'Hit':'Miss',t.fault?'-':t.physical));},
 },
 MF:{
  inputHelp:'첫 줄은 전체 크기와 배치 정책(first·best·worst), 둘째 줄은 작업 수 K, 다음 K줄은 “alloc ID 크기” 또는 “free ID”입니다. 처음에는 전체가 비어 있고, 처음부터 차 있는 구간이 있으면 마지막에 “initial B”와 B줄 “시작 크기 소유자”(빈 구간은 -)를 씁니다.',
  outputHelp:'작업마다 할당이면 “ID 시작주소”(실패하면 “ID FAIL”), 해제면 “ID FREE”를 씁니다.',
  toText(x){const empty=x.initial.length===1&&x.initial[0].owner===null&&x.initial[0].start===0&&x.initial[0].size===x.total;
   return [join(x.total,x.policy),String(x.operations.length),...x.operations.map(o=>o.op==='alloc'?join('alloc',o.id,o.size):join('free',o.id)),
    ...(empty?[]:[join('initial',x.initial.length),...x.initial.map(b=>join(b.start,b.size,b.owner))])].join('\n');},
  parse(text){const r=reader(text),h=r.line(2,'전체 크기 정책'),total=r.int(h[0],'전체 크기',1,BIG),policy=r.word(h[1],['first','best','worst'],'정책');
   const operations=Array.from({length:r.count('작업 수')},()=>{const t=r.lineAtLeast(2,'작업'),op=r.word(t[0],['alloc','free'],'작업');
    if(op==='alloc'){if(t.length!==3)fail('alloc 줄은 “alloc ID 크기”입니다.');return {op,id:r.id(t[1],'ID'),size:r.int(t[2],'크기',1,BIG)};}
    if(t.length!==2)fail('free 줄은 “free ID”입니다.');return {op,id:r.id(t[1],'ID')};});
   let initial=[{start:0,size:total,owner:null}];
   if(!r.atEnd()){const h2=r.line(2,'처음 메모리');r.word(h2[0],['initial'],'처음 메모리 줄');initial=Array.from({length:r.int(h2[1],'구간 수',0,1000)},()=>{const b=r.line(3,'시작 크기 소유자');return {start:r.int(b[0],'시작',0,BIG),size:r.int(b[1],'크기',1,BIG),owner:b[2]==='-'?null:r.id(b[2],'소유자')};});}
   r.end();return {total,initial,operations,policy};},
  expected(run,input){return run.events.map(e=>{const o=input.operations[e.data.index];if(o.op==='free')return join(o.id,'FREE');if(!e.data.success)return join(o.id,'FAIL');return join(o.id,e.data.blocks.find(b=>b.owner===o.id).start);});},
 },
 S3:{
  inputHelp:'첫 줄은 프로세스 수 P와 자원 종류 수 R, 둘째 줄은 프로세스 ID P개, 셋째 줄은 자원 ID R개, 넷째 줄은 자원별 개수 R개, 다섯째 줄은 동작 수 A, 다음 A줄은 “request|release ID 개수1 … 개수R”입니다. 처음부터 할당·대기 요청이 있으면 마지막에 “allocated”와 P줄, “requests”와 P줄을 씁니다.',
  outputHelp:'마지막에 교착이면 “DEADLOCK ID…”(순서 무관), 아니면 “NONE” 한 줄을 씁니다.',
  toText(x){const used=m=>m.flat().some(v=>v!==0),rows=(word,m)=>used(m)?[word,...m.map(r=>join(r))]:[];
   return [join(x.actors.length,x.resources.length),join(x.actors),join(x.resources),join(x.available),String(x.actions.length),...x.actions.map(a=>join(a.op,a.actor,a.values)),
    ...rows('allocated',x.allocated),...rows('requests',x.requests)].join('\n');},
  parse(text){const r=reader(text),h=r.line(2,'P R'),p=r.int(h[0],'P',1,32),k=r.int(h[1],'R',1,16);
   const actors=r.line(p,'프로세스').map(v=>r.id(v,'ID')),resources=r.line(k,'자원').map(v=>r.id(v,'자원')),available=r.line(k,'개수').map(v=>r.int(v,'개수',0,BIG));
   const actions=Array.from({length:r.count('동작 수')},()=>{const t=r.line(k+2,'동작');return {op:r.word(t[0],['request','release'],'동작'),actor:r.id(t[1],'ID'),values:t.slice(2).map(v=>r.int(v,'개수',0,BIG))};});
   const zero=()=>actors.map(()=>resources.map(()=>0)),matrices={allocated:zero(),requests:zero()};
   while(!r.atEnd()){const w=r.word(r.line(1,'추가 묶음')[0],['allocated','requests'],'추가 묶음');matrices[w]=actors.map(()=>r.line(k,w).map(v=>r.int(v,'개수',0,BIG)));}
   r.end();return {actors,resources,available,...matrices,actions};},
  expected(run){return [run.result.deadlocked.length?join('DEADLOCK',run.result.deadlocked):'NONE'];},
  compare(expected,actual){const norm=l=>l.map(t=>t[0]==='DEADLOCK'?['DEADLOCK',...[...t.slice(1)].sort()]:t);return JSON.stringify(norm(expected))===JSON.stringify(norm(actual));},
 },
 PC:{
  inputHelp:'첫 줄은 버퍼 크기와 정책(correct·lock-first), 둘째 줄은 주체 수 A, 다음 A줄은 “ID producer|consumer”, 다음 줄은 행동 수 M, 다음 줄은 행동할 ID 순서 M개입니다. 처음 버퍼에 항목이 있으면 마지막 줄에 “initial K 항목…”을 씁니다.',
  outputHelp:'행동마다 “ID 결과”(wait·acquire·put·take·release 등 그 순간 한 일)를 쓰고, 마지막 줄에 버퍼에 남은 항목 수를 씁니다.',
  toText(x){const first=x.initial??[];return [join(x.capacity,x.policy),String(x.actors.length),...x.actors.map(a=>join(a.id,a.role)),String(x.order.length),...(x.order.length?[join(x.order)]:[]),
   ...(first.length?[join('initial',first.length,first.map(val))]:[])].join('\n');},
  parse(text){const r=reader(text),h=r.line(2,'크기 정책'),capacity=r.int(h[0],'버퍼 크기',1,64),policy=r.word(h[1],['correct','lock-first'],'정책');
   const actors=Array.from({length:r.count('주체 수',32)},()=>{const t=r.line(2,'ID 역할');return {id:r.id(t[0],'ID'),role:r.word(t[1],['producer','consumer'],'역할')};});
   const m=r.count('행동 수');const order=m?r.line(m,'행동 순서').map(v=>r.id(v,'ID')):[];let initial=[];
   if(!r.atEnd()){const t=r.lineAtLeast(2,'처음 버퍼');r.word(t[0],['initial'],'마지막 줄');const k=r.int(t[1],'처음 항목 수',0,capacity);if(t.length!==2+k)fail('처음 항목 수와 값 수가 다릅니다.');initial=t.slice(2).map(v=>r.value(v));}
   r.end();return {capacity,initial,actors,order,policy};},
  expected(run){return [...run.events.map(e=>join(e.actor,e.kind)),String(run.result.buffer.length)];},
 },
 BK:{
  inputHelp:'첫 줄은 프로세스 수 P와 자원 종류 수 R, 둘째 줄은 프로세스 ID, 셋째 줄은 자원 ID, 넷째 줄은 가용 개수 R개, 다음 P줄은 현재 할당, 다음 P줄은 최대 요구, 마지막 줄은 “request ID 개수1 … 개수R” 또는 “none”입니다.',
  outputHelp:'요청이 있으면 먼저 “GRANTED” 또는 “DENIED”를 씁니다. 그다음 안전하면 “SAFE ID…”(안전한 순서라면 어느 순서든 정답), 아니면 “UNSAFE”를 씁니다.',
  toText(x){return [join(x.actors.length,x.resources.length),join(x.actors),join(x.resources),join(x.available),...x.allocated.map(r=>join(r)),...x.max.map(r=>join(r)),x.request?join('request',x.request.actor,x.request.values):'none'].join('\n');},
  parse(text){const r=reader(text),h=r.line(2,'P R'),p=r.int(h[0],'P',1,32),k=r.int(h[1],'R',1,16);
   const actors=r.line(p,'프로세스').map(v=>r.id(v,'ID')),resources=r.line(k,'자원').map(v=>r.id(v,'자원')),available=r.line(k,'가용').map(v=>r.int(v,'가용',0,BIG));
   const rows=label=>actors.map(()=>r.line(k,label).map(v=>r.int(v,label,0,1000))),allocated=rows('할당'),max=rows('최대');
   const last=r.lineAtLeast(1,'요청');let request=null;if(last[0]!=='none'){if(last[0]!=='request')fail('마지막 줄은 “request ID 개수…” 또는 “none”입니다.');if(last.length!==k+2)fail('요청 개수가 자원 수와 다릅니다.');request={actor:r.id(last[1],'ID'),values:last.slice(2).map(v=>r.int(v,'요청',0,BIG))};}
   r.end();return {actors,resources,available,allocated,max,request};},
  expected(run,input){const out=[];if(input.request)out.push(run.result.granted?'GRANTED':'DENIED');out.push(run.result.safe?join('SAFE',run.result.sequence):'UNSAFE');return out;},
  compare(expected,actual,{input,reference}){
   const tail=a=>a.at(-1),head=a=>a.length>1?a[0]:null;
   if(actual.length!==expected.length||JSON.stringify(head(actual))!==JSON.stringify(head(expected)))return false;
   if(tail(expected)[0]!=='SAFE')return JSON.stringify(tail(actual))===JSON.stringify(tail(expected));
   const seq=tail(actual);if(seq[0]!=='SAFE'||seq.length!==input.actors.length+1)return false;
   const order=seq.slice(1);if(new Set(order).size!==order.length||order.some(a=>!input.actors.includes(a)))return false;
   const alloc=input.allocated.map(r=>[...r]);let work=[...input.available];
   if(input.request&&reference.result.granted){const i=input.actors.indexOf(input.request.actor);input.request.values.forEach((v,j)=>{alloc[i][j]+=v;work[j]-=v;});}
   for(const a of order){const i=input.actors.indexOf(a);if(input.max[i].some((m,j)=>m-alloc[i][j]>work[j]))return false;work=work.map((w,j)=>w+alloc[i][j]);}
   return true;
  },
 },
 DP:{
  inputHelp:'첫 줄은 철학자 수 N, 정책(left·ordered·admission), 방식(fixedSchedule·findSchedule)입니다. 둘째 줄은 ID N개, 셋째 줄은 생각 시간 N개, 넷째 줄은 식사 시간 N개입니다. 식사 목표가 있으면(findSchedule은 필수) 다음 줄에 “goal 목표1 … 목표N”을 씁니다. 그다음 줄은 행동 수 M, 마지막 줄은 행동 순서 M개입니다.',
  outputHelp:'fixedSchedule이면 철학자마다 입력 순서대로 “ID 상태”를 쓰고, 마지막 줄에 멈춘 이유를 씁니다. findSchedule이면 모두가 식사 목표에 닿는 순간까지의 행동 순서를 한 줄에 씁니다(목표에 닿는 순서면 모두 정답).',
  toText(x){const goals=x.goalMeals??{},any=Object.keys(goals).length>0;
   return [join(x.actors.length,x.policy,x.mode),join(x.actors),join(x.actors.map(a=>x.thinkTicks[a])),join(x.actors.map(a=>x.eatTicks[a])),
    ...(any||x.mode==='findSchedule'?[join('goal',x.actors.map(a=>goals[a]??0))]:[]),String(x.order.length),...(x.order.length?[join(x.order)]:[])].join('\n');},
  parse(text){const r=reader(text),h=r.line(3,'N 정책 방식'),n=r.int(h[0],'철학자 수',2,16),actors=r.line(n,'ID').map(v=>r.id(v,'ID'));
   const ticks=label=>Object.fromEntries(r.line(n,label).map((v,i)=>[actors[i],r.int(v,label,0,100)])),thinkTicks=ticks('생각 시간'),eatTicks=ticks('식사 시간');
   let goalMeals={};if(r.peek()?.[0]==='goal'){const g=r.line(n+1,'goal 목표');goalMeals=Object.fromEntries(actors.map((a,i)=>[a,r.int(g[i+1],'목표 식사',0,1000)]));}
   const m=r.count('행동 수',10000);const order=m?r.line(m,'행동 순서').map(v=>r.id(v,'ID')):[];r.end();
   return {actors,order,policy:r.word(h[1],['left','ordered','admission'],'정책'),mode:r.word(h[2],['fixedSchedule','findSchedule'],'방식'),thinkTicks,eatTicks,goalMeals};},
  expected(run,input){return input.mode==='findSchedule'?[join(run.events.map(e=>e.actor))]:[...input.actors.map(a=>join(a,run.result.states[a])),word(run.result.stopReason)];},
  // findSchedule (2026-11-09): any order is right if replaying it reaches every meal goal exactly at its last action.
  compare(expected,actual,{input}){
   if(input.mode!=='findSchedule')return JSON.stringify(expected)===JSON.stringify(actual);
   if(actual.length!==1||!actual[0].every(a=>input.actors.includes(a)))return false;
   try{return run('DP',{...input,order:actual[0]}).events.length===actual[0].length;}catch{return false;}
  },
 },
 IN:{
  inputHelp:'첫 줄은 블록 크기입니다. 이어서 “개수” 줄 다음에 그 수만큼: 파일 이름 줄 “이름 inode”, inode 줄 “inode 크기 직접블록수 블록… 간접블록|-”, 간접 블록 줄 “블록 개수 블록…”, 데이터 블록 줄 “블록 값”을 차례로 씁니다. 마지막으로 접근 수 A와 A줄 “이름 오프셋”입니다.',
  outputHelp:'접근마다 “inode 논리블록 실제블록 블록안위치”를 씁니다. 파일 끝을 넘으면 “inode EOF”입니다.',
  toText(x){const f=x.fs,names=Object.keys(f.directory),inodes=Object.keys(f.inodes),ind=Object.keys(f.indirect),blocks=Object.keys(f.blocks);
   return [String(f.blockSize),String(names.length),...names.map(n=>join(n,f.directory[n])),String(inodes.length),...inodes.map(i=>join(i,f.inodes[i].size,f.inodes[i].direct.length,f.inodes[i].direct,f.inodes[i].indirect??'-')),
    String(ind.length),...ind.map(b=>join(b,f.indirect[b].length,f.indirect[b])),String(blocks.length),...blocks.map(b=>join(b,val(f.blocks[b]))),String(x.accesses.length),...x.accesses.map(a=>join(a.name,a.offset))].join('\n');},
  parse(text){const r=reader(text),blockSize=r.int(r.line(1,'블록 크기')[0],'블록 크기',1,65536),directory={},inodes={},indirect={},blocks={};
   for(let i=r.count('파일 이름 수');i>0;i--){const t=r.line(2,'이름 inode');directory[r.id(t[0],'이름')]=r.id(t[1],'inode');}
   for(let i=r.count('inode 수');i>0;i--){const t=r.lineAtLeast(4,'inode'),d=r.int(t[2],'직접 블록 수',0,64);if(t.length!==d+4)fail('inode 줄의 블록 수가 맞지 않습니다.');inodes[r.id(t[0],'inode')]={size:r.int(t[1],'크기',1,BIG),direct:t.slice(3,3+d).map(v=>r.id(v,'블록')),indirect:t[3+d]==='-'?null:r.id(t[3+d],'간접 블록')};}
   for(let i=r.count('간접 블록 수');i>0;i--){const t=r.lineAtLeast(2,'간접 블록'),c=r.int(t[1],'개수',0,BIG);if(t.length!==c+2)fail('간접 블록 줄의 개수가 맞지 않습니다.');indirect[r.id(t[0],'블록')]=t.slice(2).map(v=>r.id(v,'블록'));}
   for(let i=r.count('데이터 블록 수');i>0;i--){const t=r.line(2,'블록 값');blocks[r.id(t[0],'블록')]=r.value(t[1]);}
   const accesses=Array.from({length:r.count('접근 수')},()=>{const t=r.line(2,'이름 오프셋');return {name:r.id(t[0],'이름'),offset:r.int(t[1],'오프셋',0,BIG)};});
   r.end();return {fs:{blockSize,directory,inodes,indirect,blocks},accesses};},
  expected(run){return run.result.accesses.map(a=>a.eof?join(a.inode,'EOF'):join(a.inode,a.logical,a.block,a.offset));},
 },
 DS:{
  inputHelp:'첫 줄은 요청 수 N, 시작 실린더, 최대 실린더, 정책(FCFS·SSTF·SCAN·CSCAN), 방향(1 또는 -1)입니다. 다음 N줄은 “ID 실린더”입니다.',
  outputHelp:'첫 줄에 처리한 요청 ID를 순서대로, 둘째 줄에 헤드의 총 이동 거리를 씁니다.',
  toText(x){return [join(x.requests.length,x.start,x.max,x.policy,x.direction),...x.requests.map(q=>join(q.id,q.position))].join('\n');},
  parse(text){const r=reader(text),h=r.line(5,'N 시작 최대 정책 방향'),n=r.int(h[0],'요청 수',1,1000),max=r.int(h[2],'최대 실린더',1,BIG);
   const requests=Array.from({length:n},()=>{const t=r.line(2,'ID 실린더');return {id:r.id(t[0],'ID'),position:r.int(t[1],'실린더',0,BIG)};});
   r.end();return {requests,start:r.int(h[1],'시작',0,BIG),max,policy:r.word(h[3],['FCFS','SSTF','SCAN','CSCAN'],'정책'),direction:Number(r.word(h[4],['1','-1'],'방향'))};},
  expected(run){return [join(run.result.services),String(run.result.distance)];},
 },
 PT:{
  inputHelp:'첫 줄은 프로세스 수 P, 다음 P줄은 “프로세스 변수수 이름 값 …”입니다. 다음 줄은 스레드 수 T, 다음 T줄은 “스레드 프로세스 레지스터수 이름 값 … [스택수 값 …]”입니다. 다음 줄은 동작 수 K, 다음 K줄은 “read 스레드 shared|private 이름” 또는 “write 스레드 shared|private 이름 값”입니다.',
  outputHelp:'읽기마다 “스레드 이름 값”을 씁니다.',
  toText(x){const ps=Object.keys(x.spaces),ts=Object.keys(x.threads),kv=o=>[Object.keys(o).length,...named(o)],stack=s=>s.length?[s.length,...s.map(val)]:[];
   return [String(ps.length),...ps.map(p=>join(p,kv(x.spaces[p]))),String(ts.length),...ts.map(t=>join(t,x.threads[t].process,kv(x.threads[t].registers),stack(x.threads[t].stack))),String(x.operations.length),
    ...x.operations.map(o=>o.op==='write'?join('write',o.thread,o.scope,o.key,val(o.value??null)):join('read',o.thread,o.scope,o.key))].join('\n');},
  parse(text){const r=reader(text),kv=(t,from,label)=>{const c=r.int(t[from],label,0,64);if(t.length!==from+1+2*c)fail(`${label}의 이름·값 수가 맞지 않습니다.`);return Object.fromEntries(Array.from({length:c},(_,i)=>[r.id(t[from+1+2*i],'이름'),r.value(t[from+2+2*i])]));};
   const spaces={};for(let i=r.count('프로세스 수',32);i>0;i--){const t=r.lineAtLeast(2,'프로세스');spaces[r.id(t[0],'프로세스')]=kv(t,1,'변수');}
   const threads={};for(let i=r.count('스레드 수',32);i>0;i--){const t=r.lineAtLeast(3,'스레드'),c=r.int(t[2],'레지스터',0,64),e=3+2*c;if(t.length<e)fail('레지스터의 이름·값 수가 맞지 않습니다.');
    let stack=[];if(t.length>e){const s=r.int(t[e],'스택 수',0,256);if(t.length!==e+1+s)fail('스택 수와 값 수가 다릅니다.');stack=t.slice(e+1).map(v=>r.value(v));}
    threads[r.id(t[0],'스레드')]={process:r.id(t[1],'프로세스'),registers:r.pairs(t.slice(3,e),'레지스터'),stack};}
   const operations=Array.from({length:r.count('동작 수')},()=>{const t=r.lineAtLeast(4,'동작'),op=r.word(t[0],['read','write'],'동작'),o={op,thread:r.id(t[1],'스레드'),scope:r.word(t[2],['shared','private'],'범위'),key:r.id(t[3],'이름')};
    if(op==='write'){if(t.length!==5)fail('write 줄은 “write 스레드 범위 이름 값”입니다.');o.value=r.value(t[4]);}else if(t.length!==4)fail('read 줄은 “read 스레드 범위 이름”입니다.');return o;});
   r.end();return {spaces,threads,operations};},
  expected(run){return run.events.filter(e=>e.kind==='read').map(e=>join(e.actor,e.data.key,e.data.value));},
 },
 IPC:{
  inputHelp:'첫 줄은 방식(pipe·message·shared)과 채널 크기, 둘째 줄은 주체 수 A, 셋째 줄은 주체 ID A개, 넷째 줄은 동작 수 K, 다음 K줄은 “ID send 값”, “ID receive”, “ID write 이름 값”, “ID read 이름” 중 하나입니다. 처음 채널에 값이 있으면 “initial K 값…”, 처음 공유 메모리에 값이 있으면 “shared K 이름 값 …”을 마지막에 씁니다.',
  outputHelp:'동작마다 “ID 결과 값”을 씁니다. 결과는 그 순간 일어난 일(send·receive·block·resume·read·write 등)이고, 값이 없으면 -입니다.',
  toText(x){const first=x.initial??[],shared=x.shared??{};
   return [join(x.mode,x.capacity),String(x.actors.length),join(x.actors),String(x.operations.length),...x.operations.map(o=>o.op==='send'?join(o.actor,'send',val(o.value??null)):o.op==='write'?join(o.actor,'write',o.key,val(o.value??null)):o.op==='read'?join(o.actor,'read',o.key):join(o.actor,'receive')),
    ...(first.length?[join('initial',first.length,first.map(val))]:[]),...(Object.keys(shared).length?[join('shared',Object.keys(shared).length,named(shared))]:[])].join('\n');},
  parse(text){const r=reader(text),h=r.line(2,'방식 크기'),mode=r.word(h[0],['pipe','message','shared'],'방식'),capacity=r.int(h[1],'채널 크기',0,64),a=r.count('주체 수',32);
   const actors=r.line(a,'주체').map(v=>r.id(v,'ID')),operations=Array.from({length:r.count('동작 수')},()=>{const t=r.lineAtLeast(2,'동작'),actor=r.id(t[0],'ID'),op=r.word(t[1],['send','receive','write','read'],'동작');
    const n={send:3,receive:2,write:4,read:3}[op];if(t.length!==n)fail(`${op} 줄의 값 수가 맞지 않습니다.`);
    return op==='send'?{actor,op,value:r.value(t[2])}:op==='write'?{actor,op,key:r.id(t[2],'이름'),value:r.value(t[3])}:op==='read'?{actor,op,key:r.id(t[2],'이름')}:{actor,op};});
   let initial=[],shared={};
   while(!r.atEnd()){const t=r.lineAtLeast(2,'추가 줄'),w=r.word(t[0],['initial','shared'],'추가 줄'),k=r.int(t[1],'값 수',0,256);
    if(w==='initial'){if(t.length!==2+k)fail('처음 채널 값 수가 맞지 않습니다.');initial=t.slice(2).map(v=>r.value(v));}
    else{if(t.length!==2+2*k)fail('공유 메모리의 이름·값 수가 맞지 않습니다.');shared=r.pairs(t.slice(2),'공유 메모리');}}
   r.end();return {actors,mode,capacity,initial,shared,operations};},
  expected(run){return run.events.map(e=>join(e.actor,e.kind,e.data.value??null));},
 },
};
function kernel(id){const scenarios=id==='K-SYSCALL'?['syscall','error','blocking']:['io','timer'];return {
 inputHelp:`첫 줄은 상황(${scenarios.join('·')}), 실행 중 ID, 대기 중 ID, 시점, 반환값입니다. 둘째 줄은 주체 수 N, 다음 N줄은 “ID pc [이름 값 …]”입니다.`,
 outputHelp:'첫 줄에 “모드 실행중ID”(없으면 -), 다음에 주체마다 입력 순서대로 “ID 상태 pc”, 마지막 줄에 반환값(없으면 -)을 씁니다.',
 toText(x){const ids=Object.keys(x.contexts);return [join(x.scenario,x.current,x.blocked,x.at,x.returnValue),String(ids.length),...ids.map(i=>withPc(i,x.contexts[i]))].join('\n');},
 parse(text){const r=reader(text),h=r.line(5,'상황 실행 대기 시점 반환값'),contexts={};for(let i=r.count('주체 수',16);i>0;i--){const t=r.lineAtLeast(2,'ID pc');contexts[r.id(t[0],'ID')]=readPc(r,t);}
  r.end();return {scenario:r.word(h[0],scenarios,'상황'),current:r.id(h[1],'실행 중 ID'),blocked:r.id(h[2],'대기 중 ID'),contexts,at:r.int(h[3],'시점',0,BIG),returnValue:r.int(h[4],'반환값',-BIG,BIG)};},
 expected(run,input){return [join(run.result.mode,run.result.current??null),...Object.keys(input.contexts).map(i=>join(i,run.result.states[i],run.result.contexts[i].pc)),word(run.result.returnValue??null)];},
};}
formats['K-SYSCALL']=kernel('K-SYSCALL');formats['K-INTERRUPT']=kernel('K-INTERRUPT');
export const textFormats=formats;

/* ── public API ───────────────────────────────────────────────────── */
const format=id=>{ensure(Object.hasOwn(formats,id),'텍스트 형식을 지원하지 않는 모델입니다.');return formats[id];};
export function inputToText(id,input){return format(id).toText(input);}
export function parseTextInput(id,text){const input=format(id).parse(text);checkShape(registry[id].inputSchema,input);return input;}
export function textReference(id,text){
 const input=parseTextInput(id,text),reference=run(id,input,{record:true});
 return {input,reference,expected:format(id).expected(reference,input).join('\n')};
}
export function outputTokens(text){
 ensure(typeof text==='string'&&byteSize(text)<=1048576,'출력은 1MiB 이하의 문자열이어야 합니다.');
 return text.split(/\r?\n/).map(l=>l.trim()).filter(Boolean).slice(0,10001).map(l=>l.split(/\s+/));
}
export function gradeText(id,inputText,stdout){
 const {input,reference,expected}=textReference(id,inputText),f=format(id);
 let actual;try{actual=outputTokens(stdout);}catch(error){return {correct:false,kind:'wrong',traceValid:false,message:error.message,reported:null};}
 const want=outputTokens(expected);let correct=false;
 try{correct=f.compare?f.compare(want,actual,{input,reference}):JSON.stringify(want)===JSON.stringify(actual);}catch{correct=false;}
 return {correct,kind:correct?'correct':'wrong',traceValid:false,
  message:correct?'출력이 올바릅니다.':actual.length!==want.length?'출력 줄 수가 기준과 다릅니다.':'출력 내용이 기준과 다릅니다. 순서와 값을 확인하세요.',
  reported:{v:1,text:actual.map(t=>t.join(' '))}};
}
export const textHelp=id=>({input:format(id).inputHelp,output:format(id).outputHelp});
