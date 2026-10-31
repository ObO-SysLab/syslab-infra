"""Run actual Python in the existing Linux jail, then validate/replay its stdout outside it."""
import json
import subprocess
import unittest
from pathlib import Path
from test_linux_sandbox import LinuxSandboxTest
from obo_assessment import public_diagram_record

SOURCE = r'''
import json,sys
graph=json.load(sys.stdin)
for tick in range(2):
    overrides={}
    for n in graph['nodes']:
        t,d=n.get('type'),n['data']
        if t=='state-node': patch={'label':'step '+str(tick)}
        elif t=='text-label': patch={'text':'computed step '+str(tick)}
        elif t=='counter-badge': patch={'value':tick+1}
        elif t=='resource-square': patch={'allocated':min(d.get('instances',1),tick)}
        elif t=='queue-strip': patch={'capacity':3,'items':['item '+str(i) for i in range(tick+1)]}
        elif t=='table-grid': patch={'headers':['step','value'],'rows':[[str(tick),str(tick*tick)]],'activeRow':0}
        elif t=='slot-grid': patch={'slots':[tick]+[None]*max(0,d.get('slotCount',len(d.get('slots',[])))-1)} if d.get('slotCount',len(d.get('slots',[]))) else {'slots':[]}
        elif t=='gantt-lane': patch={'blocks':[{'start':tick,'end':tick+1,'colorKey':'P1'}]}
        elif t=='memory-range': patch={'activeIds':[d['segments'][tick%len(d['segments'])]['id']]} if d.get('segments') else {'activeIds':[]}
        elif t=='process-box': patch={'activeThreadId':d['threads'][tick%len(d['threads'])]['id']} if d.get('threads') else {'activeThreadId':None}
        elif t=='line-chart': patch={'progressIndex':min(tick,max([-1]+[len(s['points'])-1 for s in d.get('series',[])]))}
        else: patch={}
        overrides[n['id']]=patch
    print(json.dumps({'v':1,'overrides':overrides,'highlightNodes':[n['id'] for n in graph['nodes'][:1]],'visibleEdges':[e['id'] for e in graph['edges'][::tick+1]]}))
'''

class DiagramLinuxTest(LinuxSandboxTest):
    def test_all_elements_and_templates_use_real_execution_output(self):
        fixtures=json.loads(Path('/opt/runner/diagram-fixtures.json').read_text())
        cases=[{'id':'template:'+f['id'],'blob':f['blob']} for f in fixtures['fixtures']]
        cases += [{'id':'element:'+t,'blob':{'nodes':[{'id':'node','type':t,'position':{'x':0,'y':0},'data':data}],'edges':[]}} for t,data in fixtures['defaults'].items()]
        outputs=[]
        for case in cases:
            with self.subTest(case=case['id']):
                stdout=self.output(self.run_code(SOURCE,inputs=[json.dumps(case['blob']).encode('utf-8')]))
                check=subprocess.run(['node','--input-type=module','-e',"import fs from 'node:fs';import {parseDiagramTrace} from './obo_runtime/freeDiagram.mjs';const x=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(parseDiagramTrace(x.stdout,x.nodes,x.edges)));"],input=json.dumps({'stdout':stdout,**case['blob']}).encode(),capture_output=True,timeout=10,check=True)
                parsed=json.loads(check.stdout);self.assertEqual([],parsed['errors']);self.assertEqual(2,len(parsed['trace']))
                snapshot={'obo':{'mode':'coding_diff'},'testcases':[{'isSample':True},{'isSample':False}]}
                record=public_diagram_record(snapshot,0,stdout);self.assertEqual(stdout,record['stdout']);self.assertIsNone(public_diagram_record(snapshot,1,stdout))
                outputs.append({'id':case['id'],'stdout':stdout,'record':record,'trace':parsed['trace']})
        print('OBO_DIAGRAM_RESULTS='+json.dumps({'source':SOURCE,'outputs':outputs},ensure_ascii=True),flush=True)

if __name__=='__main__':unittest.main()
