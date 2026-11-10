import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from obo_checker import invoke, grade_output, verify_release, OboCheckerError, RUNTIME


class OboCheckerTest(unittest.TestCase):
    def test_release_and_all_model_process_certificates(self):
        verify_release()
        source = (RUNTIME / 'contracts.mjs').read_text(encoding='utf-8')
        contracts = json.loads(source.split('export default ', 1)[1].strip().removesuffix(';'))
        for scenario_id, spec in contracts['scenarios'].items():
            with self.subTest(scenario=scenario_id):
                reference = invoke({'operation': 'reference', 'scenarioId': scenario_id, 'input': spec['inputExample']})
                output = json.dumps({'v': 1, 'result': reference['result'], 'events': reference['events']})
                verdict = grade_output(scenario_id, spec['inputExample'], 'process', output)
                self.assertTrue(verdict['correct'])
                self.assertTrue(verdict['traceValid'])

    def test_wrong_output_is_not_configuration_failure(self):
        result = grade_output('ST2', {'processes': {'P': 'New'}, 'actions': []}, 'result', 'not JSON')
        self.assertFalse(result['correct'])
        self.assertEqual('wrong', result['kind'])

    def test_configuration_and_process_failures_are_not_wrong_answers(self):
        with self.assertRaises(OboCheckerError):
            grade_output('unknown', {}, 'result', '{}')
        with self.assertRaises(OboCheckerError):
            invoke({'operation': 'reference', 'scenarioId': 'ST2', 'input': {}}, node='missing-obo-node-executable')

    def test_stdout_is_data_and_not_executed(self):
        result = grade_output('ST2', {'processes': {'P': 'New'}, 'actions': []}, 'result',
                              'process.exit(0); require("node:fs").writeFileSync("bad", "bad")')
        self.assertEqual('wrong', result['kind'])


if __name__ == '__main__':
    unittest.main()
