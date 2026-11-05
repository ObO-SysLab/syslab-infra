// Trusted process entry point. Never imports or evaluates submitted source code.
import {grade,run,parseBoundedJson,checkShape,registry} from './index.mjs';
import {validateDocument} from './document.mjs';
import {validateFreeDiagram} from './freeDiagram.mjs';
import {referenceRrText,gradeRrText} from './rrText.mjs';
let size=0;const chunks=[];
try {
  for await(const chunk of process.stdin){size+=chunk.length;if(size>8*1048576)throw Error('요청 크기 초과');chunks.push(chunk);}
  const request=parseBoundedJson(Buffer.concat(chunks).toString('utf8'),{bytes:8*1048576,depth:40});
  if(request.operation==='rrReference'||request.operation==='rrGrade'){
    const grading=request.operation==='rrGrade';
    checkShape({type:'object',required:['operation','input',...(grading?['stdout']:[])],properties:{operation:{enum:['rrReference','rrGrade']},input:{type:'string'},...(grading?{stdout:{type:'string'}}:{})},additionalProperties:false},request);
    process.stdout.write(JSON.stringify({ok:true,...(grading?gradeRrText(request.input,request.stdout):referenceRrText(request.input))}));
  }else if(request.operation==='validateFreeDiagram') {
    validateFreeDiagram(request.document);
    process.stdout.write(JSON.stringify({ok:true}));
  }else if(request.operation==='validateDocument') {
    checkShape({type:'object',required:['operation','document'],properties:{operation:{const:'validateDocument'},document:{type:'object'}},additionalProperties:false},request);
    validateDocument(request.document);
    process.stdout.write(JSON.stringify({ok:true}));
  }else if(request.operation==='grade'){
    checkShape({type:'object',required:['operation','scenarioId','input','mode','stdout'],properties:{operation:{const:'grade'},scenarioId:{type:'string'},input:{type:'object'},mode:{enum:['result','process']},stdout:{type:'string'}},additionalProperties:false},request);
    const verdict=grade(request.scenarioId,request.input,request.mode,request.stdout);
    let reported=null;
    try {
      const payload=parseBoundedJson(request.stdout),spec=registry[request.scenarioId];
      checkShape(Object.hasOwn(payload,'events')?spec.processOutputSchema:spec.resultOutputSchema,payload);
      reported=payload;
    }catch{
      // A valid final answer remains visible even when its optional process record is malformed.
      if(verdict.correct && request.mode==='result'){
        try {
          const payload=parseBoundedJson(request.stdout);
          const resultOnly={v:payload.v,result:payload.result};
          checkShape(registry[request.scenarioId].resultOutputSchema,resultOnly);
          reported=resultOnly;
        }catch{/* Invalid records never enter the visualization projection. */}
      }
    }
    process.stdout.write(JSON.stringify({ok:true,...verdict,reported}));
  }else if(request.operation==='reference'){
    checkShape({type:'object',required:['operation','scenarioId','input'],properties:{operation:{const:'reference'},scenarioId:{type:'string'},input:{type:'object'}},additionalProperties:false},request);
    process.stdout.write(JSON.stringify({ok:true,...run(request.scenarioId,request.input)}));
  }else throw Error('지원하지 않는 검사 작업입니다.');
}catch(error){process.stdout.write(JSON.stringify({ok:false,message:error.message}));process.exitCode=2;}
