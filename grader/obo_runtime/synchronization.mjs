import {ensure,clone,unique} from './validation.mjs';
const fits=(a,b)=>a.every((v,i)=>v<=b[i]);
function matrixConfig(input) {
  unique(input.actors);unique(input.resources);
  ensure(input.available.length===input.resources.length,'가용 벡터의 자원 수가 다릅니다.');
  for(const matrix of [input.allocated,input.max??input.requests]) {
    ensure(matrix.length===input.actors.length,'행렬의 프로세스 수가 다릅니다.');
    for(const row of matrix)ensure(row.length===input.resources.length,'행렬의 자원 수가 다릅니다.');
  }
}
export function safety(available,allocated,need) {
  const work=clone(available),sequence=[],done=new Set();let changed=true;
  while(changed){changed=false;for(let p=0;p<allocated.length;p++)if(!done.has(p)&&fits(need[p],work)){allocated[p].forEach((v,i)=>work[i]+=v);done.add(p);sequence.push(p);changed=true;}}
  return {safe:done.size===allocated.length,sequence,unfinished:allocated.map((_,i)=>i).filter(i=>!done.has(i))};
}
export function resourceAllocation(input,ctx) {
  matrixConfig(input);const available=clone(input.available),allocations=clone(input.allocated),requests=clone(input.requests);
  const data=()=>({available,allocations,requests});
  for(const op of input.actions) {
    const p=input.actors.indexOf(op.actor);ensure(p>=0,'주체가 없습니다.');ensure(op.values.length===available.length,'요청 벡터 크기가 다릅니다.');
    if(op.op==='release'){ensure(fits(op.values,allocations[p]),'보유량보다 많이 반납할 수 없습니다.');op.values.forEach((v,i)=>{allocations[p][i]-=v;available[i]+=v;});ctx.emit('release',op.actor,data());}
    else{requests[p]=clone(op.values);ctx.emit('request',op.actor,data());if(fits(requests[p],available)){requests[p].forEach((v,i)=>{available[i]-=v;allocations[p][i]+=v;});requests[p]=available.map(()=>0);ctx.emit('grant',op.actor,data());}}
  }
  const result=safety(available,allocations,requests);ctx.emit('detect',null,data());
  return {deadlocked:result.unfinished.filter(p=>allocations[p].some(v=>v>0)).map(p=>input.actors[p]),available,allocations};
}
export function banker(input,ctx,preferredSequence=null) {
  matrixConfig(input);let available=clone(input.available),allocated=clone(input.allocated);
  const need=input.max.map((row,p)=>row.map((v,i)=>{ensure(v>=allocated[p][i],'최대 요구가 현재 할당보다 작습니다.');return v-allocated[p][i];}));
  let work=clone(available),finish=Object.fromEntries(input.actors.map(id=>[id,false])),granted=null;
  const data=()=>({work,finish,need,available});
  let unavailable=false;
  if(input.request){const p=input.actors.indexOf(input.request.actor);ensure(p>=0,'요청 주체가 없습니다.');ensure(input.request.values.length===available.length,'요청 크기가 다릅니다.');ensure(fits(input.request.values,need[p]),'최대 요구를 초과했습니다.');ctx.emit('request',input.request.actor,data());
    if(!fits(input.request.values,available)){unavailable=true;granted=false;ctx.emit('deny',input.request.actor,data());}
    else{input.request.values.forEach((v,i)=>{available[i]-=v;allocated[p][i]+=v;need[p][i]-=v;});work=clone(available);}
  }
  const analysis=safety(available,allocated,need);
  if(input.request&&!unavailable)granted=analysis.safe;
  if(!analysis.safe){ctx.emit('check',null,data());if(input.request&&!unavailable)ctx.emit('deny',input.request.actor,data());return {safe:false,sequence:[],granted};}
  const sequence=preferredSequence??analysis.sequence.map(p=>input.actors[p]);
  unique(sequence,'안전 순서에 중복 프로세스가 있습니다.');ensure(sequence.length===input.actors.length,'안전 순서가 누락되었습니다.');
  for(const id of sequence){const p=input.actors.indexOf(id);ensure(p>=0,'안전 순서에 없는 주체가 있습니다.');ctx.emit('check',id,data());ensure(fits(need[p],work),'현재 가용량으로 완료할 수 없는 순서입니다.');allocated[p].forEach((v,i)=>work[i]+=v);finish[id]=true;ctx.emit('finish',id,data());}
  return {safe:true,sequence,granted};
}
export function boundedBuffer(input,ctx) {
  unique(input.actors.map(a=>a.id));ensure(input.initial.length<=input.capacity,'초기 버퍼가 용량을 초과했습니다.');
  const buffer=clone(input.initial),actors=Object.fromEntries(input.actors.map(a=>[a.id,{role:a.role,pc:0,blocked:false}]));
  const semaphores={empty:input.capacity-buffer.length,full:buffer.length,mutex:null};
  const stage=a=>a.pc===0?(input.policy==='correct'?'token':'mutex'):a.pc===1?(input.policy==='correct'?'mutex':'token'):a.pc===2?'transfer':'release';
  const token=a=>a.role==='producer'?'empty':'full';
  const can=a=>stage(a)==='mutex'?semaphores.mutex===null:stage(a)==='token'?semaphores[token(a)]>0:true;
  const data=stopReason=>({buffer,actors,semaphores,stopReason});
  function reason(){
    if(!Object.values(actors).every(a=>!can(a)))return 'scheduleEnd';
    const owner=semaphores.mutex&&actors[semaphores.mutex];
    return owner&&stage(owner)==='token'&&Object.values(actors).some(a=>a.role!==owner.role&&stage(a)==='mutex')?'deadlocked':'blocked';
  }
  input.order.forEach((id,index)=>{
    ensure(Object.hasOwn(actors,id),'행동할 주체가 없습니다.');const a=actors[id],wasBlocked=a.blocked,kind=stage(a);a.blocked=false;let event;
    if(!can(a)){a.blocked=true;event='wait';}
    else if(kind==='token'){semaphores[token(a)]--;a.pc++;event=wasBlocked?'wake':'acquire';}
    else if(kind==='mutex'){semaphores.mutex=id;a.pc++;event=wasBlocked?'wake':'acquire';}
    else if(kind==='transfer'){ensure(semaphores.mutex===id,'뮤텍스 소유자가 아닙니다.');if(a.role==='producer'){ensure(buffer.length<input.capacity,'버퍼가 가득 찼습니다.');buffer.push(id);event='put';}else{ensure(buffer.length>0,'버퍼가 비었습니다.');buffer.shift();event='take';}a.pc++;}
    else {ensure(semaphores.mutex===id,'뮤텍스 반납자가 다릅니다.');semaphores.mutex=null;semaphores[a.role==='producer'?'full':'empty']++;a.pc=0;event='release';}
    ctx.emit(event,id,data(index===input.order.length-1?reason():'running'));
  });
  return data(reason());
}
export function philosophers(input,ctx) {
  unique(input.actors);const ids=input.actors,n=ids.length;
  for(const values of [input.thinkTicks,input.eatTicks]){ensure(Object.keys(values).length===n&&ids.every(id=>Object.hasOwn(values,id)),'모든 철학자의 시간을 지정하세요.');}
  ensure(Object.keys(input.goalMeals).every(id=>ids.includes(id)),'목표에 없는 철학자가 있습니다.');
  if(input.mode==='findSchedule')ensure(Object.values(input.goalMeals).some(v=>v>0),'양의 식사 목표가 필요합니다.');
  const owners=Array(n).fill(null),states=Object.fromEntries(ids.map(id=>[id,'thinking']));
  const held=Object.fromEntries(ids.map(id=>[id,[]])),think=clone(input.thinkTicks),eat=clone(input.eatTicks),meals=Object.fromEntries(ids.map(id=>[id,0]));
  let reached=false;
  const data=stopReason=>({owners,states,stopReason});
  const forks=id=>{const p=ids.indexOf(id),r=[p,(p+1)%n];return input.policy==='ordered'?r.sort((a,b)=>a-b):r;};
  const nextFork=id=>forks(id).find(f=>!held[id].includes(f));
  const admitted=id=>input.policy!=='admission'||held[id].length>0||ids.filter(p=>held[p].length>0).length<n-1;
  const stopped=()=>ids.every(id=>['hungry','waiting'].includes(states[id])&&held[id].length<2&&(!admitted(id)||owners[nextFork(id)]!==null))?'deadlocked':'scheduleEnd';
  for(let index=0;index<input.order.length;index++) {
    if(reached)break;
    const id=input.order[index];ensure(ids.includes(id),'철학자가 없습니다.');let kind;
    if(states[id]==='thinking') {if(think[id]>0){think[id]--;kind='think';}else{states[id]='hungry';kind='request';}}
    else if(states[id]==='eating') {if(eat[id]>1){eat[id]--;kind='eat';}else{for(const f of held[id])owners[f]=null;held[id]=[];meals[id]++;states[id]='thinking';think[id]=input.thinkTicks[id];eat[id]=input.eatTicks[id];kind='release';}}
    else if(held[id].length===2){states[id]='eating';kind='eat';}
    else {const f=nextFork(id);if(!admitted(id)||owners[f]!==null){states[id]='waiting';kind='request';}else{owners[f]=id;held[id].push(f);states[id]='hungry';kind='acquire';}}
    reached=input.mode==='findSchedule'&&Object.entries(input.goalMeals).every(([p,count])=>meals[p]>=count);
    ctx.emit(kind,id,data(reached?'goalReached':index===input.order.length-1?stopped():'running'));
  }
  const stopReason=reached?'goalReached':stopped();
  if(input.mode==='findSchedule')ensure(reached,'식사 목표에 도달하지 못했습니다.');
  return data(stopReason);
}
