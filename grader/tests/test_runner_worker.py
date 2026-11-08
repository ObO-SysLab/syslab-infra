import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runner'))
from worker import execute_request, s3_url
from execution_protocol import ExecutionFailure, make_request, decode, MAX_OUTPUT


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.sandbox = Mock()
        self.sandbox.execute.return_value = dict(verdict='OK', runtimeMs=1, memoryKb=5000, output=b'1\n')
        self.sandbox.binary.return_value = b'ELF fixture'
        self.factory = Mock(return_value=self.sandbox)

    def request(self, language='python'):
        return make_request('a' * 32, language, b'source fixture', [b'case one', b'case two'], 2, 128, 1100)

    def run_request(self, request):
        return execute_request(request, self.factory, now=lambda: 1000)

    def test_python_uses_isolated_mode_and_resets_between_cases(self):
        result = self.run_request(self.request())
        self.assertEqual(self.sandbox.reset.call_count, 2)
        calls = self.sandbox.execute.call_args_list
        self.assertEqual(calls[0].args, (['/usr/bin/python3', '-I', '-B', '/job/code.py'], b'case one', 2, 128))
        self.assertEqual(calls[1].args[1], b'case two')
        self.assertEqual(decode(result['cases'][0]['stdout'], MAX_OUTPUT), b'1\n')

    def test_c_and_cpp_compilation_are_also_sandboxed(self):
        for language, compiler in [('c', '/usr/bin/gcc'), ('cpp', '/usr/bin/g++')]:
            self.sandbox.reset_mock()
            self.run_request(self.request(language))
            first = self.sandbox.execute.call_args_list[0]
            self.assertEqual(first.args[0][0], compiler)
            self.assertEqual(first.kwargs, {'compiler': True})
            self.assertEqual(first.args[2:], (10, 1024))
            self.sandbox.reset.assert_any_call('solution', b'ELF fixture', executable=True)

    def test_compile_error_returns_no_fake_cases(self):
        self.sandbox.execute.return_value['verdict'] = 'RUNTIME_ERROR'
        result = self.run_request(self.request('cpp'))
        self.assertTrue(result['compileError']); self.assertEqual(result['cases'], [])
        self.sandbox.binary.assert_not_called()

    def test_broken_sandbox_is_failure_not_compile_error(self):
        self.sandbox.execute.side_effect = ExecutionFailure('setup failed')
        with self.assertRaises(ExecutionFailure): self.run_request(self.request('c'))

    def test_expired_or_oversized_request_never_constructs_sandbox(self):
        request = self.request(); request['deadline'] = 1000
        with self.assertRaises(ExecutionFailure): self.run_request(request)
        request = self.request(); request['memoryLimitMb'] = 2048
        with self.assertRaises(ExecutionFailure): self.run_request(request)
        self.factory.assert_not_called()

    def test_transport_rejects_non_s3_plaintext_credentials_and_redirect_hosts(self):
        self.assertEqual(s3_url('https://bucket.s3.ap-northeast-2.amazonaws.com/key?signature=fixture'),
                         'https://bucket.s3.ap-northeast-2.amazonaws.com/key?signature=fixture')
        for url in ['http://bucket.s3.ap-northeast-2.amazonaws.com/key', 'https://169.254.169.254/',
                    'https://evil.example/', 'https://u:p@bucket.s3.ap-northeast-2.amazonaws.com/key',
                    'https://bucket.s3.ap-northeast-2.amazonaws.com:8443/key']:
            with self.assertRaises(ExecutionFailure): s3_url(url)


if __name__ == '__main__': unittest.main()
