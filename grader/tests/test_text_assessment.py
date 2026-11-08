"""Real shared checker; Baekjoon-style text answers (text-v1, 2026-11-08) and public-record boundaries."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from obo_assessment import semantic_verdict,public_record
from obo_checker import OboCheckerError

P2_INPUT='7 3 FIFO\n7 0 1 2 0 3 0'
P2_OUTPUT='\n'.join(['7 Miss 7 - -','0 Miss 7 0 -','1 Miss 7 0 1','2 Miss 2 0 1','0 Hit 2 0 1','3 Miss 2 3 1','0 Miss 2 3 0','6'])

class TextAssessmentTest(unittest.TestCase):
    def snapshot(self,scenario='P2',sample=P2_INPUT,hidden='5 2 LRU\n1 2 1 3 1',mode='result'):
        return {'judgeProfile':'obo-v1','modelVersion':1,'obo':{'simulation':{
            'question':{'gradingInstanceId':'m','assessmentMode':mode,'ioFormat':'text-v1'},
            'instances':[{'id':'m','scenarioId':scenario}]}},
            'testcases':[{'input':sample,'output':'unused','isSample':True},
                         {'input':hidden,'output':'unused','isSample':False}]}
    def test_reference_lines_pass_with_any_spacing_and_publish_a_line_record(self):
        snapshot=self.snapshot();v=semantic_verdict(snapshot,0,'  '+P2_OUTPUT.replace(' ','   ')+'\n\n')
        self.assertTrue(v['correct'],v);self.assertNotIn('ok',v)
        record=public_record(snapshot,0,v)
        self.assertEqual(record['kind'],'text-lines')
        self.assertEqual(record['expected'],P2_OUTPUT)
        self.assertEqual(record['reported']['text'],P2_OUTPUT.split('\n'))
        self.assertEqual(record['inputText'],P2_INPUT)
        self.assertEqual(record['input']['frameCount'],3)
        self.assertIn('events',record['reference'])
    def test_one_wrong_token_is_wrong_without_leaking_the_expected_value(self):
        snapshot=self.snapshot();v=semantic_verdict(snapshot,0,P2_OUTPUT.replace('0 Hit','0 Miss'))
        self.assertFalse(v['correct']);self.assertEqual(v['kind'],'wrong')
        self.assertNotIn('Hit',v['message'])
        self.assertEqual(public_record(snapshot,0,v)['reported']['text'][4],'0 Miss 2 0 1')
    def test_hidden_case_is_graded_but_not_published(self):
        snapshot=self.snapshot();v=semantic_verdict(snapshot,1,'1 Miss 1 -\n2 Miss 1 2\n1 Hit 1 2\n3 Miss 1 3\n1 Hit 1 3\n3')
        self.assertTrue(v['correct'],v);self.assertIsNone(public_record(snapshot,1,v))
    def test_meaning_based_models_accept_other_valid_answers(self):
        rr=self.snapshot('RR','2 2\nP1 0 3\nP2 6 2')
        self.assertTrue(semantic_verdict(rr,0,'P1 0 1\nP1 1 3\nIDLE 3 6\nP2 6 8')['correct'])
        self.assertFalse(semantic_verdict(rr,0,'P1 0 3\nP2 6 8')['correct'])
        s3=self.snapshot('S3','2 2\nP1 P2\nR1 R2\n1 1\n4\nrequest P1 1 0\nrequest P2 0 1\nrequest P1 0 1\nrequest P2 1 0')
        self.assertTrue(semantic_verdict(s3,0,'DEADLOCK P2 P1')['correct'])
    def test_malformed_output_is_wrong_not_an_infrastructure_failure(self):
        v=semantic_verdict(self.snapshot(),0,'');self.assertFalse(v['correct'])
        v=semantic_verdict(self.snapshot(),0,'x'*100);self.assertFalse(v['correct'])
    def test_text_format_only_grades_results(self):
        with self.assertRaises(OboCheckerError):semantic_verdict(self.snapshot(mode='process'),0,P2_OUTPUT)

if __name__=='__main__':unittest.main()
