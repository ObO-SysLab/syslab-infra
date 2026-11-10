import {ensure,clone,unique} from './validation.mjs';

export function cpuScheduling(input,ctx) {
  unique(input.jobs.map(p=>p.id));
  const jobs=input.jobs.map((p,index)=>({...clone(p),index,priority:p.priority??0,phase:0,left:p.bursts[0],state:'new',wake:null,first:null,done:null,waiting:0}));
  for(const p of jobs)ensure(p.bursts.length%2===1,'CPU와 I/O 실행량은 CPU로 시작하고 끝나야 합니다.');
  let current=null,expired=null,ready=[],slice=0,time=0;
  const segments=[];
  const data=()=>({time,current:current?.id??null,ready:ready.map(p=>p.id),remaining:Object.fromEntries(jobs.map(p=>[p.id,p.left])),states:Object.fromEntries(jobs.map(p=>[p.id,p.state]))});
  const emit=(kind,id=null)=>ctx.emit(kind,id,data());
  const choose=()=>{
    if(['SJF','SRTF'].includes(input.policy))ready.sort((a,b)=>a.left-b.left||a.index-b.index);
    if(input.policy.startsWith('PS'))ready.sort((a,b)=>a.priority-b.priority||a.index-b.index);
    current=ready.shift()??null;slice=0;if(current){current.state='running';current.first??=time;emit('dispatch',current.id);}
  };
  while(jobs.some(p=>p.state!=='done')) {
    ctx.tick();
    for(const p of jobs)if((p.state==='new'&&p.arrival===time)||(p.state==='blocked'&&p.wake===time)) {
      const kind=p.state==='new'?'arrive':'unblock';p.state='ready';ready.push(p);emit(kind,p.id);
    }
    if(expired){ready.push(expired);emit('expire',expired.id);expired=null;}
    if(current&&((input.policy==='SRTF'&&ready.some(p=>p.left<current.left))||(input.policy==='PS-P'&&ready.some(p=>p.priority<current.priority)))) {
      const id=current.id;current.state='ready';ready.push(current);current=null;emit('preempt',id);
    }
    if(!current)choose();
    const actor=current?.id??null,last=segments.at(-1);
    if(last&&last.actor===actor&&last.end===time)last.end=time+1;else segments.push({actor,start:time,end:time+1});
    for(const p of ready)p.waiting++;
    time++;
    if(!current){emit('idle');continue;}
    current.left--;slice++;emit('run',current.id);
    if(current.left===0){
      const p=current;current=null;
      if(p.phase===p.bursts.length-1){p.state='done';p.done=time;emit('finish',p.id);}
      else{p.wake=time+p.bursts[p.phase+1];p.phase+=2;p.left=p.bursts[p.phase];p.state='blocked';emit('block',p.id);}
    }else if(input.policy==='RR'&&slice===input.quantum){expired=current;current.state='ready';current=null;}
  }
  return {segments,metrics:Object.fromEntries(jobs.map(p=>[p.id,{waiting:p.waiting,response:p.first-p.arrival,turnaround:p.done-p.arrival}]))};
}
