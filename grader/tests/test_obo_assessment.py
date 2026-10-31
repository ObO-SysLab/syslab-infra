import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from obo_assessment import public_record, semantic_verdict


class PublicAssessmentTest(unittest.TestCase):
    def snapshot(self, public=True):
        return {'judgeProfile': 'obo-v1', 'modelVersion': 1,
                'obo': {'simulation': {'question': {'gradingInstanceId': 'a', 'assessmentMode': 'result'},
                                      'instances': [{'id': 'a', 'scenarioId': 'ST2'}]}},
                'testcases': [{'isSample': public, 'input': json.dumps({'processes': {'P': 'New'}, 'actions': []}), 'output': ''}]}

    def test_hidden_case_never_builds_reference_or_public_record(self):
        for flag in (False, None, 'true', 1):
            with patch('obo_assessment.invoke') as checker:
                self.assertIsNone(public_record(self.snapshot(flag), 0, {}))
                checker.assert_not_called()

    def test_result_only_output_does_not_invent_student_events(self):
        snapshot = self.snapshot()
        verdict = semantic_verdict(snapshot, 0, '{"v":1,"result":{"states":{"P":"New"}}}')
        self.assertTrue(verdict['correct'])
        record = public_record(snapshot, 0, verdict)
        self.assertNotIn('events', record['reported'])
        self.assertEqual('ST2', record['scenarioId'])

    def test_correct_result_cannot_publish_invalid_optional_trace(self):
        snapshot = self.snapshot()
        verdict = semantic_verdict(snapshot, 0, '{"v":1,"result":{"states":{"P":"New"}},"events":[]}')
        self.assertTrue(verdict['correct'])
        self.assertFalse(verdict['traceValid'])
        self.assertNotIn('events', public_record(snapshot, 0, verdict)['reported'])


if __name__ == '__main__':
    unittest.main()
