"""Actual coordinator functions, with storage/execution/DB mocked; no services start."""
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import execution_protocol as protocol


class CoordinatorTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'grader.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.scope = {k: getattr(protocol, k) for k in ('MAX_SOURCE', 'MAX_INPUT', 'MAX_OUTPUT',
                     'MAX_TIME', 'MAX_MEMORY_MB', 'MAX_CASES', 'integer', 'decode')}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), self.scope)
        self.runner = Mock()
        self.execution = dict(compileError=False, cases=[dict(verdict='OK', runtimeMs=3, memoryKb=1024, stdout=protocol.encode(b'answer\n'))])
        self.runner.execute.return_value = self.execution
        self.conn = Mock()
        self.scope.update(sys=sys, create_storage_client=Mock(), get_db_conn=lambda: self.conn, SUBMISSION_ID='7',
            PROBLEM_ID='8', LANGUAGE='python', CODE_S3_KEY='code', S3_BUCKET='trusted-code',
            TIME_LIMIT=1, MEMORY_LIMIT=256, TESTCASE_COUNT=1, IS_CONTEST=False,
            begin_grading=Mock(return_value=True), finalize_submission=Mock(), report_attempt_failure=Mock(),
            create_runner=Mock(return_value=self.runner),
            read_object=Mock(side_effect=[b'print(input())', b'input', b'answer\n']))

    def test_expected_answers_never_cross_execution_boundary(self):
        self.scope['main']()
        self.runner.execute.assert_called_once_with('python', b'print(input())', [b'input'], 1, 256)
        self.scope['finalize_submission'].assert_called_once_with(self.conn, 'CORRECT', 1024, 3, 14)
        self.conn.close.assert_called_once()

    def test_verdict_mapping_preserves_persisted_contract(self):
        for verdict, expected in [('TIME_LIMIT_EXCEEDED', 'WRONG'), ('RUNTIME_ERROR', 'ERROR'),
                                  ('OUTPUT_LIMIT_EXCEEDED', 'ERROR')]:
            self.setUp(); self.execution['cases'][0]['verdict'] = verdict
            self.scope['main']()
            self.assertEqual(self.scope['finalize_submission'].call_args.args[1], expected)

    def test_wrong_answer_and_compile_error(self):
        self.execution['cases'][0]['stdout'] = protocol.encode(b'wrong')
        self.scope['main'](); self.assertEqual(self.scope['finalize_submission'].call_args.args[1], 'WRONG')
        self.setUp(); self.execution['compileError'] = True; self.execution['cases'] = []
        self.scope['main'](); self.assertEqual(self.scope['finalize_submission'].call_args.args[1], 'ERROR')

    def test_execution_or_storage_failure_is_retryable_not_judged_error(self):
        for target in (self.runner.execute, self.scope['read_object']):
            target.side_effect = protocol.ExecutionFailure('fixture failure')
            self.assertEqual(self.scope['main'](), 1)
        self.scope['finalize_submission'].assert_not_called()
        self.scope['report_attempt_failure'].assert_called()

    def test_no_host_subprocess_or_compile_fallback_remains(self):
        source = (Path(__file__).resolve().parents[1] / 'grader.py').read_text(encoding='utf-8-sig')
        self.assertNotIn('subprocess', source)
        self.assertNotIn('def compile_code', source)
        self.assertNotIn('def run_testcase', source)


if __name__ == '__main__': unittest.main()
