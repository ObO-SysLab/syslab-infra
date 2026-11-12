"""Lambda coordinator: job admission, per-message configuration and SQS failure semantics."""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# grader itself is covered by test_coordinator; keep its DB/Redis/AWS clients out of this test.
fake_grader = types.ModuleType('grader')
fake_grader.configure = Mock()
fake_grader.main = Mock(return_value=0)
_previous = sys.modules.get('grader')
sys.modules['grader'] = fake_grader
import lambda_coordinator as coordinator  # noqa: E402
# Do not leak the stub into other test modules in the same discovery run.
if _previous is None:
    del sys.modules['grader']
else:
    sys.modules['grader'] = _previous

DATABASE = {'DB_HOST': 'db.internal', 'DB_PORT': '5432', 'DB_NAME': 'syslab', 'DB_USER': 'grader', 'DB_PASSWORD': 'pw'}


def job(**changes):
    value = dict(submissionId='101', s3Key='submissions/101/Main.py', language='python', problemId='7',
                 timeLimit=1, memoryLimit=256, testcaseCount=3, attemptNo=1)
    value.update(changes)
    return value


def event(*bodies):
    return {'Records': [{'messageId': f'm{i}', 'body': json.dumps(b)} for i, b in enumerate(bodies)]}


class LambdaCoordinatorTest(unittest.TestCase):
    def setUp(self):
        fake_grader.configure.reset_mock()
        self.grade = Mock(return_value=0)

    def run_handler(self, *bodies):
        return coordinator.handler(event(*bodies), None, grade=self.grade, settings=lambda: DATABASE)

    def test_job_becomes_the_same_settings_the_ecs_task_received(self):
        values = coordinator.job_settings(job(contestContext={'contestId': 4, 'contestProblemId': 9, 'points': 100}),
                                          {'S3_BUCKET': 'syslab-code', 'UNRELATED': 'x'}, DATABASE)
        self.assertEqual(values['SUBMISSION_ID'], '101')
        self.assertEqual(values['CODE_S3_KEY'], 'submissions/101/Main.py')
        self.assertEqual((values['TIME_LIMIT'], values['MEMORY_LIMIT'], values['TESTCASE_COUNT']), ('1', '256', '3'))
        self.assertEqual(values['GRADING_ATTEMPT'], '1')
        self.assertEqual((values['CONTEST_ID'], values['CONTEST_PROBLEM_ID'], values['CONTEST_POINTS']), ('4', '9', '100'))
        self.assertEqual(values['S3_BUCKET'], 'syslab-code')
        self.assertNotIn('UNRELATED', values)
        self.assertEqual(values['DB_PASSWORD'], 'pw')

    def test_admission_matches_the_fargate_trigger(self):
        bad = [job(language='rust'), job(timeLimit=0), job(timeLimit=31), job(memoryLimit=8), job(testcaseCount=101),
               job(timeLimit='1'), job(testcaseCount=100, timeLimit=5), job(attemptNo=4),
               job(contestContext={'contestId': 4}), {k: v for k, v in job().items() if k != 's3Key'}]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                coordinator.job_settings(body, {}, DATABASE)

    def test_each_message_is_configured_then_graded(self):
        result = self.run_handler(job(submissionId='1'), job(submissionId='2'))
        self.assertEqual(result, {'batchItemFailures': []})
        self.assertEqual([c.args[0]['SUBMISSION_ID'] for c in fake_grader.configure.call_args_list], ['1', '2'])
        self.assertEqual(self.grade.call_count, 2)

    def test_grading_failure_is_left_to_db_recovery_not_sqs(self):
        self.grade.return_value = 1
        self.assertEqual(self.run_handler(job()), {'batchItemFailures': []})

    def test_unstartable_messages_return_to_sqs(self):
        self.grade.side_effect = OSError('database unreachable')
        result = coordinator.handler({'Records': [{'messageId': 'bad', 'body': '{'},
                                                  {'messageId': 'db', 'body': json.dumps(job())}]},
                                     None, grade=self.grade, settings=lambda: DATABASE)
        self.assertEqual(result, {'batchItemFailures': [{'itemIdentifier': 'bad'}, {'itemIdentifier': 'db'}]})

    def test_secret_is_read_once_and_must_be_complete(self):
        coordinator._database = None
        load = Mock(return_value=json.dumps({**DATABASE, 'DB_PORT': 5432}))
        import os
        os.environ['DB_SECRET_ARN'] = 'arn:fixture'
        try:
            self.assertEqual(coordinator.database_settings(load)['DB_PORT'], '5432')
            coordinator.database_settings(load)
            load.assert_called_once_with('arn:fixture')
            coordinator._database = None
            with self.assertRaises(ValueError):
                coordinator.database_settings(Mock(return_value=json.dumps({'DB_HOST': 'x'})))
        finally:
            coordinator._database = None
            del os.environ['DB_SECRET_ARN']


    def test_warm_up_runs_the_checker_once_and_never_raises(self):
        calls = []
        ticks = iter([0.0, 6.5])
        state = coordinator.warm_up(invoke=lambda request, timeout: calls.append((request, timeout)), clock=lambda: next(ticks))
        self.assertEqual(state, 'ready')
        self.assertEqual(calls, [({'operation': 'warmup'}, 30)])

        class OboCheckerError(Exception):
            pass
        def rejected(request, timeout):
            raise OboCheckerError('Checker rejected configuration')
        state = coordinator.warm_up(invoke=rejected, clock=lambda: 0.0)
        self.assertEqual(state, 'OboCheckerError: Checker rejected configuration')


if __name__ == '__main__':
    unittest.main()
