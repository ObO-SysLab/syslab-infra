"""Real Linux probes. Run only in the approved disposable Docker image, never on a host."""
import os
import sys
import unittest
import uuid
from sandbox import Sandbox
from execution_protocol import make_request, decode, MAX_OUTPUT
from worker import execute_request
import time
import shutil


class LinuxSandboxTest(unittest.TestCase):
    def run_code(self, source, language='python', seconds=2, memory=256, inputs=None):
        root = '/work/test-' + uuid.uuid4().hex
        request = make_request(uuid.uuid4().hex, language, source.encode(), inputs or [b''], seconds, memory, int(time.time()) + 500)
        try:
            return execute_request(request, lambda: Sandbox(root=root))
        finally:
            if os.path.isdir(root): shutil.rmtree(root)

    def output(self, result):
        self.assertFalse(result['compileError']); self.assertEqual(result['cases'][0]['verdict'], 'OK')
        return decode(result['cases'][0]['stdout'], MAX_OUTPUT).decode()

    def test_python_c_cpp_normal_io(self):
        self.assertEqual(self.output(self.run_code('print(input())', inputs=[b'hello\n'])), 'hello\n')
        for language, source in [('c', '#include <stdio.h>\nint main(){puts("hello");}'),
                ('cpp', '#include <iostream>\nint main(){std::cout << "hello\\n";}')]:
            self.assertEqual(self.output(self.run_code(source, language)), 'hello\n')

    def test_environment_proc_parent_files_and_network_are_denied(self):
        os.environ['DB_PASSWORD'] = 'fake-test-marker'
        with open('/work/fake-answer', 'w') as stream: stream.write('fake-answer-marker')
        source = '''import os, socket
assert 'DB_PASSWORD' not in os.environ
assert os.getuid() == 10001
for path in ['/proc/self/environ', '/proc/1/root/work/fake-answer', '/work/fake-answer', '/opt/runner/worker.py']:
    try: open(path).read()
    except OSError: pass
    else: raise AssertionError(path)
try: socket.socket()
except PermissionError: pass
else: raise AssertionError('socket allowed')
try: os.fork()
except PermissionError: pass
else: raise AssertionError('fork allowed')
print('denied')'''
        self.assertEqual(self.output(self.run_code(source)), 'denied\n')

    def test_each_case_has_fresh_files(self):
        result = self.run_code("import os; assert not os.path.exists('/job/marker'); open('/job/marker','w').write('x'); print('fresh')", inputs=[b'', b''])
        self.assertEqual([decode(c['stdout'], MAX_OUTPUT) for c in result['cases']], [b'fresh\n', b'fresh\n'])

    def test_time_output_and_memory_limits(self):
        self.assertEqual(self.run_code('while True: pass', seconds=1)['cases'][0]['verdict'], 'TIME_LIMIT_EXCEEDED')
        self.assertEqual(self.run_code("import os; os.write(1,b'x'*2000000)")['cases'][0]['verdict'], 'OUTPUT_LIMIT_EXCEEDED')
        self.assertEqual(self.run_code("x=bytearray(128*1024*1024)", memory=64)['cases'][0]['verdict'], 'RUNTIME_ERROR')

    def test_compiler_cannot_read_trusted_worker_or_answers(self):
        result = self.run_code('#include "/opt/runner/worker.py"\nint main(){}', 'c')
        self.assertTrue(result['compileError'])


if __name__ == '__main__': unittest.main()
