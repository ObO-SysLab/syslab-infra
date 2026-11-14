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

    def test_verdict_and_first_wrong_line_without_the_expected_text(self):
        # Read like compare_output; only the position is published.
        snapshot={'obo':{'mode':'coding_diff'},'testcases':[{'isSample':True}]}
        wrong=public_diagram_record(snapshot,0,'a 1\nb 9\nc 3\n','a 1\nb 2\nc 3',False)
        self.assertEqual({'kind':'diagram','caseIndex':1,'stdout':'a 1\nb 9\nc 3\n','correct':False,'firstWrong':1},wrong)
        self.assertNotIn('b 2',str(wrong))
        self.assertEqual(2,public_diagram_record(snapshot,0,'a 1\nb 2','a 1\nb 2\nc 3',False)['firstWrong'])
        self.assertEqual(1,public_diagram_record(snapshot,0,'a 1\nx','a 1',False)['firstWrong'])
        right=public_diagram_record(snapshot,0,'  a 1  \n\n','a 1',True)
        self.assertEqual(True,right['correct']);self.assertNotIn('firstWrong',right)
        # A wrong verdict for reasons outside the lines (same text) still says nothing about a position.
        self.assertIsNone(public_diagram_record(snapshot,0,'a 1','a 1',False)['firstWrong'])

if __name__=='__main__':unittest.main()
