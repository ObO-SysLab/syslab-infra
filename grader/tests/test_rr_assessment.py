"""Real shared checker; textual RR answers and public-record boundaries."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from obo_assessment import semantic_verdict,public_record

class RrAssessmentTest(unittest.TestCase):
    def snapshot(self):
        return {'judgeProfile':'obo-v1','modelVersion':1,'obo':{'simulation':{
            'question':{'gradingInstanceId':'rr','assessmentMode':'result','ioFormat':'rr-segments-v1'},
            'instances':[{'id':'rr','scenarioId':'RR'}]}},
            'testcases':[{'input':'2 2\nP1 0 3\nP2 1 1','output':'unused','isSample':True},
                         {'input':'1 2\nSECRET_HIDDEN 7 2','output':'unused','isSample':False}]}
    def test_correct_intervals_do_not_require_metrics_or_invent_events(self):
        snapshot=self.snapshot();v=semantic_verdict(snapshot,0,'P1 0 2\nP2 2 3\nP1 3 4')
        self.assertTrue(v['correct']);record=public_record(snapshot,0,v)
        self.assertEqual(record['kind'],'rr-schedule')
        self.assertEqual(set(record['reported']['result']),{'segments'})
        self.assertNotIn('events',record['reported'])
        self.assertIn('events',record['reference'])
    def test_wrong_intervals_remain_wrong_and_are_displayable(self):
        snapshot=self.snapshot();v=semantic_verdict(snapshot,0,'P1 0 4')
        self.assertFalse(v['correct'])
        self.assertEqual(public_record(snapshot,0,v)['reported']['result']['segments'],[{'actor':'P1','start':0,'end':4}])
    def test_hidden_case_is_graded_but_not_published(self):
        snapshot=self.snapshot();v=semantic_verdict(snapshot,1,'IDLE 0 7\nSECRET_HIDDEN 7 9')
        self.assertTrue(v['correct']);self.assertIsNone(public_record(snapshot,1,v))
    def test_malformed_output_is_wrong_not_an_infrastructure_failure(self):
        v=semantic_verdict(self.snapshot(),0,'P1 2 1');self.assertFalse(v['correct']);self.assertIsNone(v['reported'])

if __name__=='__main__':unittest.main()
