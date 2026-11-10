import sys
import unittest
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution_protocol import *


def request():
    return make_request('a' * 32, 'python', b'print(input())', [b'hello'], 1, 256, 2000)


def result(req=None):
    return dict(version=1, requestId=(req or request())['requestId'], compileError=False,
        cases=[dict(index=0, verdict='OK', runtimeMs=1, memoryKb=1024, stdout=encode(b'hello\n'))])


class ProtocolTest(unittest.TestCase):
    def test_round_trip_without_answers_or_credentials(self):
        req = request()
        self.assertEqual(decode(req['source'], MAX_SOURCE), b'print(input())')
        self.assertEqual(parse(serialize(req, MAX_BUNDLE), MAX_BUNDLE), req)
        self.assertEqual(validate_result(result(), req)['cases'][0]['verdict'], 'OK')
        for forbidden in ('expected', 'DB_PASSWORD', 'AWS_SECRET_ACCESS_KEY', 'answer'):
            bad = deepcopy(req); bad[forbidden] = 'fake-secret'
            with self.assertRaises(ExecutionFailure): validate_request(bad)

    def test_request_limits_reject_before_execution(self):
        for key, values in {'timeLimit': [0, 31, True, 1.5], 'memoryLimitMb': [0, 1025, '256'],
                            'inputs': [[], [''] * 101], 'language': ['shell'], 'requestId': ['../escape']}.items():
            for value in values:
                bad = request(); bad[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ExecutionFailure): validate_request(bad)
        with self.assertRaises(ExecutionFailure):
            make_request('a' * 32, 'python', b'x' * (MAX_SOURCE + 1), [b''], 1, 256, 2000)
        with self.assertRaises(ExecutionFailure):
            make_request('a' * 32, 'python', b'x', [b'x' * (MAX_INPUT + 1)], 1, 256, 2000)

    def test_aggregate_budget_and_payload_limits(self):
        with self.assertRaises(ExecutionFailure):
            make_request('a' * 32, 'python', b'x', [b''] * 100, 30, 256, 2000)
        with self.assertRaises(ExecutionFailure): serialize({'x': 'a' * 100}, 50)
        with self.assertRaises(ExecutionFailure): parse(b'x' * 100, 50)

    def test_stale_truncated_reordered_or_forged_results_rejected(self):
        variations = []
        for key, value in [('requestId', 'b' * 32), ('cases', []), ('compileError', 'false')]:
            bad = result(); bad[key] = value; variations.append(bad)
        for key, value in [('index', 1), ('verdict', 'CORRECT'), ('runtimeMs', -1),
                           ('memoryKb', 2**40), ('stdout', 'not base64')]:
            bad = result(); bad['cases'][0][key] = value; variations.append(bad)
        for bad in variations:
            with self.assertRaises(ExecutionFailure): validate_result(bad, request())

    def test_compile_failure_has_no_case_results(self):
        compiled = dict(version=1, requestId='a' * 32, compileError=True, cases=[])
        validate_result(compiled, request())
        compiled['cases'] = result()['cases']
        with self.assertRaises(ExecutionFailure): validate_result(compiled, request())

    def test_output_bound_and_invalid_json(self):
        bad = result(); bad['cases'][0]['stdout'] = encode(b'x' * (MAX_OUTPUT + 1))
        with self.assertRaises(ExecutionFailure): validate_result(bad, request())
        for data in (b'not json', b'\xff'):
            with self.assertRaises(ExecutionFailure): parse(data, 100)


if __name__ == '__main__': unittest.main()
