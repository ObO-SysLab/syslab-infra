import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from obo_assessment import public_diagram_record

class DiagramOutputTest(unittest.TestCase):
    def test_no_obo_or_other_mode_does_not_change_regular_grading(self):
        for snapshot in (None,{}, {'obo':None},{'obo':{'mode':'simulation'}}):
            self.assertIsNone(public_diagram_record(snapshot,0,'output'))

    def test_only_explicit_sample_is_exposed(self):
        for flag in (False,None,0,1,'true'):
            self.assertIsNone(public_diagram_record({'obo':{'mode':'coding_diff'},'testcases':[{'isSample':flag}]},0,'secret'))
        record=public_diagram_record({'obo':{'mode':'coding_diff'},'testcases':[{'isSample':True}]},0,'queue 3')
        self.assertEqual({'kind':'diagram','caseIndex':1,'stdout':'queue 3'},record)

    def test_large_output_is_omitted_without_truncating_or_inventing_trace(self):
        record=public_diagram_record({'obo':{'mode':'coding_diff'},'testcases':[{'isSample':True}]},0,'x'*1048577)
        self.assertIn('omitted',record)
        self.assertNotIn('stdout',record)

if __name__=='__main__':unittest.main()
