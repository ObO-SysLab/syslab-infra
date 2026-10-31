import {ensure,clone,unique} from './validation.mjs';

function pageState(capacity,initial=[]) {
  unique(initial,'초기 페이지가 중복되었습니다.');ensure(initial.length<=capacity,'초기 페이지가 프레임 수보다 많습니다.');
  return {frames:[...initial,...Array(capacity-initial.length).fill(null)],loaded:new Map(initial.map((x,i)=>[x,i-initial.length])),used:new Map(initial.map((x,i)=>[x,i-initial.length])),faults:0,index:0};
}
function reference(s,page,policy,refs=[],index=s.index) {
  const hit=s.frames.includes(page);let victim=null;
  if(!hit) {
    s.faults++;let slot=s.frames.indexOf(null);
    if(slot<0) {
      const candidates=s.frames.map((value,i)=>({value,i}));
      const score=p=>{if(policy==='OPT'){const next=refs.indexOf(p,index+1);return next<0?Infinity:next;}return (policy==='FIFO'?s.loaded:s.used).get(p);};
      candidates.sort((a,b)=>{const x=score(a.value),y=score(b.value);return x===y?a.i-b.i:policy==='OPT'?(x>y?-1:1):x-y;});
      slot=candidates[0].i;victim=s.frames[slot];
    }
    s.frames[slot]=page;s.loaded.set(page,s.index);
  }
  s.used.set(page,s.index++);return {index,page,hit,victim,frames:clone(s.frames)};
}
export function pageReplacement(input,ctx) {
  if(ctx.id==='P3') {
    unique(input.capacities,'프레임 수 범위가 중복되었습니다.');const faultsByCapacity={};
    for(const capacity of [...input.capacities].sort((a,b)=>a-b)) {
      const s=pageState(capacity);
      input.refs.forEach((page,index)=>ctx.emit('reference',null,{capacity,...reference(s,page,input.policy,input.refs,index)}));
      faultsByCapacity[capacity]=s.faults;
    }
    return {faultsByCapacity};
  }
  const s=pageState(input.frameCount,input.initial);
  input.refs.forEach((page,index)=>ctx.emit('reference',null,reference(s,page,input.policy,input.refs,index)));
  return {frames:s.frames,faults:s.faults};
}
export function memoryPressure(input,ctx) {
  ensure(Object.keys(input.quotas).length>0,'프로세스별 프레임 할당량이 필요합니다.');
  ensure(Object.values(input.quotas).reduce((a,b)=>a+b,0)<=input.frameBudget,'전체 프레임 예산을 초과했습니다.');
  const states=Object.fromEntries(Object.entries(input.quotas).map(([id,n])=>[id,pageState(n)]));
  const histories=Object.fromEntries(Object.keys(states).map(id=>[id,[]]));
  input.stream.forEach(({id,page},index)=>{
    ensure(Object.hasOwn(states,id),'참조하는 프로세스의 할당량이 없습니다.');
    histories[id].push(page);reference(states[id],page,'LRU');
    ctx.emit('reference',id,{index,page,faults:states[id].faults,workingSet:new Set(histories[id].slice(-input.window)).size,frames:states[id].frames});
  });
  return {processes:Object.fromEntries(Object.entries(states).map(([id,s])=>[id,{faults:s.faults,workingSet:new Set(histories[id].slice(-input.window)).size,frames:s.frames}]))};
}
export function addressTranslation(input,ctx) {
  let tlb=clone(input.initialTlb);unique(tlb.map(x=>x.page),'TLB 페이지가 중복되었습니다.');ensure(tlb.length<=input.capacity,'TLB 용량을 초과했습니다.');
  for(const x of tlb)ensure(x.page<input.table.length&&input.table[x.page]!==null&&x.frame===input.table[x.page],'TLB와 페이지 테이블이 일치하지 않습니다.');
  const translations=input.addresses.map((address,index)=>{
    ensure(address<input.pageSize*input.table.length,'주소 범위를 벗어났습니다.');
    const page=Math.floor(address/input.pageSize),offset=address%input.pageSize,hit=tlb.some(x=>x.page===page);
    const data=()=>({index,address,page,offset,tlb});ctx.emit('lookup',null,data());
    if(!hit)ctx.emit('pageTable',null,data());
    const fault=input.table[page]===null;
    if(fault){ctx.emit('fault',null,data());return {hit:false,fault:true,physical:null};}
    if(!hit){if(tlb.length===input.capacity)tlb.shift();tlb.push({page,frame:input.table[page]});}
    ctx.emit('translate',null,data());return {hit,fault:false,physical:input.table[page]*input.pageSize+offset};
  });return {translations};
}
export function contiguousAllocation(input,ctx) {
  const blocks=clone(input.initial),outcomes=[];let position=0;
  ensure(blocks.length>0,'초기 메모리 구간이 필요합니다.');
  unique(blocks.filter(b=>b.owner!==null).map(b=>b.owner),'할당 ID가 중복되었습니다.');
  for(const b of blocks){ensure(b.start===position,'초기 구간에 겹침 또는 빈틈이 있습니다.');position+=b.size;}
  ensure(position===input.total,'구간 크기 합이 전체 공간과 다릅니다.');
  function merge(){for(let i=blocks.length-2;i>=0;i--)if(blocks[i].owner===null&&blocks[i+1].owner===null){blocks[i].size+=blocks[i+1].size;blocks.splice(i+1,1);}}
  merge();
  input.operations.forEach((op,index)=>{
    let success=true;
    if(op.op==='free') {const b=blocks.find(b=>b.owner===op.id);ensure(b,'해제 대상이 없습니다.');b.owner=null;merge();}
    else {
      ensure(!blocks.some(b=>b.owner===op.id),'이미 사용 중인 할당 ID입니다.');
      const free=blocks.filter(b=>b.owner===null&&b.size>=op.size);
      free.sort((a,b)=>input.policy==='first'?a.start-b.start:(input.policy==='best'?a.size-b.size:b.size-a.size)||a.start-b.start);
      const b=free[0];if(!b)success=false;
      else {const rest=b.size-op.size,at=blocks.indexOf(b);b.size=op.size;b.owner=op.id;if(rest)blocks.splice(at+1,0,{start:b.start+op.size,size:rest,owner:null});}
    }
    outcomes.push(success);ctx.emit(!success?'reject':op.op==='free'?'free':'allocate',op.id,{index,success,blocks});
  });return {blocks,outcomes};
}
