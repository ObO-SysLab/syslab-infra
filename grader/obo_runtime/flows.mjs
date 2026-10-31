import {ensure,clone,unique} from './validation.mjs';

export function lifecycle(input,ctx) {
  const states=clone(input.processes);ensure(Object.keys(states).length>0,'프로세스가 필요합니다.');
  ensure(Object.values(states).filter(s=>s==='Running').length<=1,'실행 중인 프로세스는 하나여야 합니다.');
  const transitions={'New:create':'Ready','Ready:dispatch':'Running','Running:io':'Waiting','Waiting:completeIO':'Ready','Running:preempt':'Ready','Running:finish':'Terminated'};
  for(const {id,event}of input.actions){ensure(Object.hasOwn(states,id),'프로세스가 없습니다.');const before=states[id],after=transitions[`${before}:${event}`];
    ensure(after,'허용하지 않는 상태 전이입니다.');ensure(after!=='Running'||!Object.values(states).includes('Running'),'CPU가 사용 중입니다.');states[id]=after;ctx.emit(event,id,{before,after});}
  return {states};
}
export function contextSwitch(input,ctx) {
  const pcbs=clone(input.pcbs);ensure(Object.hasOwn(pcbs,input.initialId),'초기 PCB가 없습니다.');
  let current=input.initialId,registers=clone(pcbs[current]);const data=()=>({current,registers,pcbs});
  for(const id of input.order){ensure(Object.hasOwn(pcbs,id),'교환할 PCB가 없습니다.');pcbs[current]=clone(registers);ctx.emit('save',current,data());registers=clone(pcbs[id]);ctx.emit('restore',id,data());current=id;ctx.emit('select',id,data());}
  return data();
}
export function processStructure(input,ctx) {
  const spaces=clone(input.spaces),threads=clone(input.threads),reads=[];
  ensure(Object.keys(threads).length>0,'스레드가 필요합니다.');
  for(const t of Object.values(threads))ensure(Object.hasOwn(spaces,t.process),'스레드의 주소 공간이 없습니다.');
  input.operations.forEach((op,index)=>{
    ensure(Object.hasOwn(threads,op.thread),'스레드가 없습니다.');const t=threads[op.thread],target=op.scope==='shared'?spaces[t.process]:t.registers;
    ensure(Object.hasOwn(target,op.key),'읽거나 쓸 필드가 없습니다.');
    if(op.op==='write'){ensure(Object.hasOwn(op,'value'),'쓸 값이 없습니다.');target[op.key]=op.value;}else reads.push(target[op.key]);
    ctx.emit(op.op,op.thread,{index,scope:op.scope,key:op.key,value:target[op.key]});
  });return {spaces,threads,reads};
}
export function ipc(input,ctx) {
  unique(input.actors);ensure(input.initial.length<=input.capacity,'초기 채널이 용량을 초과했습니다.');
  const channel=clone(input.initial),shared=clone(input.shared),reads=[],blocked=new Set();
  input.operations.forEach((op,index)=>{
    ensure(input.actors.includes(op.actor),'통신 주체가 없습니다.');const sharedMode=input.mode==='shared';
    ensure((sharedMode?['read','write']:['send','receive']).includes(op.op),'통신 모드와 맞지 않는 동작입니다.');
    let value=null,waiting=false;
    if(sharedMode){ensure(Object.hasOwn(op,'key'),'필드가 필요합니다.');if(op.op==='write'){ensure(Object.hasOwn(op,'value'),'쓸 값이 필요합니다.');shared[op.key]=op.value;}else{ensure(Object.hasOwn(shared,op.key),'읽을 필드가 없습니다.');reads.push(shared[op.key]);}value=shared[op.key];}
    else if(op.op==='send'){ensure(Object.hasOwn(op,'value'),'보낼 값이 필요합니다.');if(channel.length===input.capacity)waiting=true;else{value=op.value;channel.push(value);}}
    else if(!channel.length)waiting=true;else{value=channel.shift();reads.push(value);}
    const kind=waiting?'block':blocked.has(op.actor)?'resume':op.op;if(waiting)blocked.add(op.actor);else blocked.delete(op.actor);
    ctx.emit(kind,op.actor,{index,channel,shared,value,blocked:waiting});
  });return {channel,shared,reads,blocked:[...blocked]};
}
export function inodeAccess(input,ctx) {
  const {fs}=input;
  for(const id of Object.values(fs.directory))ensure(Object.hasOwn(fs.inodes,id),'디렉터리가 없는 inode를 가리킵니다.');
  for(const node of Object.values(fs.inodes)) {
    if(node.indirect!==null)ensure(Object.hasOwn(fs.indirect,node.indirect),'간접 블록이 없습니다.');
    const pointers=[...node.direct,...(node.indirect===null?[]:fs.indirect[node.indirect])];
    ensure(node.size<=pointers.length*fs.blockSize,'파일 크기보다 블록이 부족합니다.');
    for(const block of pointers)ensure(Object.hasOwn(fs.blocks,block),'데이터 블록이 없습니다.');
  }
  const accesses=input.accesses.map(({name,offset},index)=>{
    ensure(Object.hasOwn(fs.directory,name),'파일 이름이 없습니다.');ctx.emit('lookup',null,{index,target:name,result:null});
    const id=fs.directory[name],node=fs.inodes[id];ctx.emit('inode',null,{index,target:id,result:null});
    if(offset>=node.size){const result={inode:id,logical:null,block:null,offset:null,eof:true};ctx.emit('eof',null,{index,target:id,result});return result;}
    const logical=Math.floor(offset/fs.blockSize);let block;
    if(logical<node.direct.length)block=node.direct[logical];
    else{ctx.emit('indirect',null,{index,target:node.indirect,result:null});block=fs.indirect[node.indirect][logical-node.direct.length];}
    const result={inode:id,logical,block,offset:offset%fs.blockSize,eof:false};ctx.emit('data',null,{index,target:block,result});return result;
  });return {accesses};
}
export function diskScheduling(input,ctx) {
  unique(input.requests.map(x=>x.id));ensure([input.start,...input.requests.map(x=>x.position)].every(x=>x<=input.max),'실린더 범위를 벗어났습니다.');
  let head=input.start,distance=0;const moves=[],services=[],pending=input.requests.map((r,i)=>({...r,index:i}));
  function travel(to,service=null,kind='move') {const from=head;distance+=Math.abs(to-from);head=to;moves.push({from,to,service});
    if(from!==to||service===null)ctx.emit(kind,null,{from,to,service:null,distance});
    if(service!==null){services.push(service);ctx.emit('service',null,{from:to,to,service,distance});}}
  if(input.policy==='FCFS')pending.forEach(r=>travel(r.position,r.id));
  else if(input.policy==='SSTF')while(pending.length){pending.sort((a,b)=>Math.abs(a.position-head)-Math.abs(b.position-head)||a.position-b.position||a.index-b.index);const r=pending.shift();travel(r.position,r.id);}
  else {
    const d=input.direction,forward=pending.filter(r=>d===1?r.position>=head:r.position<=head).sort((a,b)=>d*(a.position-b.position)||a.index-b.index);
    const behind=pending.filter(r=>d===1?r.position<head:r.position>head).sort((a,b)=>(input.policy==='SCAN'?-d:d)*(a.position-b.position)||a.index-b.index);
    forward.forEach(r=>travel(r.position,r.id));
    if(behind.length){travel(d===1?input.max:0);if(input.policy==='CSCAN')travel(d===1?0:input.max,null,'wrap');behind.forEach(r=>travel(r.position,r.id));}
  }
  return {services,moves,distance};
}
export function kernelFlow(input,ctx) {
  ensure(input.current!==input.blocked,'실행 대상과 대기 대상은 달라야 합니다.');
  const contexts=clone(input.contexts);ensure(Object.hasOwn(contexts,input.current)&&Object.hasOwn(contexts,input.blocked),'문맥이 없습니다.');
  let current=input.current,mode='user',returnValue=null;
  const states=Object.fromEntries(Object.keys(contexts).map(id=>[id,id===current?'Running':id===input.blocked?'Waiting':'Ready']));
  const data=()=>({mode,current,contexts,states,returnValue});
  mode='kernel';ctx.emit(['io','timer'].includes(input.scenario)?'interrupt':'enter',current,data());ctx.emit('save',current,data());
  if(input.scenario==='io'){ctx.emit('handle',current,data());ensure(states[input.blocked]==='Waiting','대기 중인 대상이 아닙니다.');states[input.blocked]='Ready';ctx.emit('unblock',input.blocked,data());}
  else if(input.scenario==='timer'){
    ctx.emit('handle',current,data());const previous=current;states[previous]='Ready';current=Object.keys(states).find(id=>id!==previous&&states[id]==='Ready')??previous;
    states[current]='Running';ctx.emit('select',current,data());
  }else{
    ctx.emit('service',current,data());
    if(input.scenario==='blocking'){const previous=current;states[previous]='Waiting';current=null;ctx.emit('block',previous,data());return data();}
    returnValue=input.scenario==='error'?-1:input.returnValue;ctx.emit('return',current,data());
  }
  mode='user';ctx.emit('restore',current,data());return data();
}
