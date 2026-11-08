"""No services: inspect actual grader result hooks using mocked database boundaries."""
import ast
import unittest
from pathlib import Path
from unittest.mock import MagicMock


class ScoreConsistencyTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'grader.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        self.scope = dict(SUBMISSION_ID='101', PROBLEM_ID='2')
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[]), str(path), 'exec'), self.scope)
        self.conn = MagicMock()

    def test_wrong_result_does_not_register_a_solver(self):
        self.scope['update_solved_count'](self.conn, False)
        self.conn.cursor.assert_not_called()

    def test_correct_result_registers_from_stored_submission_identity(self):
        self.scope['update_solved_count'](self.conn, True)
        sql, params = self.conn.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertIn('SELECT prob_id, submitter_id FROM solve_submission', sql)
        self.assertIn('ON CONFLICT (prob_id, user_id) DO NOTHING', sql)
        self.assertEqual(params, ('101', '2', '2'))

    def test_late_score_hook_never_writes_redis(self):
        redis = MagicMock()
        self.scope['redis_module'] = redis
        self.scope['update_scoreboard'](1, 7, 200)
        self.scope['update_scoreboard'](1, 7, 100)
        redis.assert_not_called()
        self.assertEqual(redis.mock_calls, [])


if __name__ == '__main__': unittest.main()
