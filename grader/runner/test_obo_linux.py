"""Actual student code in the Linux jail, then application-owned semantic checking outside it."""
import json
import shutil
import time
import unittest
import uuid
from pathlib import Path
from sandbox import Sandbox
from worker import execute_request
from execution_protocol import make_request, decode, MAX_OUTPUT
from obo_checker import grade_output, verify_release


class OboLinuxTest(unittest.TestCase):
    def execute(self, source):
        root='/work/obo-'+uuid.uuid4().hex
        request=make_request(uuid.uuid4().hex,'python',source.encode(),[b'{"processes":{"P":"New"},"actions":[]}'],2,128,int(time.time())+300)
        try:
            result=execute_request(request,lambda:Sandbox(root=root))
            self.assertFalse(result['compileError'])
            self.assertEqual(result['cases'][0]['verdict'],'OK')
            return decode(result['cases'][0]['stdout'],MAX_OUTPUT).decode()
        finally:
            if Path(root).is_dir():shutil.rmtree(root)

    def test_real_program_output_result_and_process_verdicts(self):
        verify_release()
        stdin={'processes':{'P':'New'},'actions':[]}
        stdout=self.execute('import json,sys; x=json.load(sys.stdin); print(json.dumps({"v":1,"result":{"states":x["processes"]}}))')
        self.assertTrue(grade_output('ST2',stdin,'result',stdout)['correct'])
        self.assertFalse(grade_output('ST2',stdin,'process',stdout)['correct'])

    def test_wrong_student_output_remains_wrong(self):
        stdout=self.execute('print("not a valid answer")')
        self.assertFalse(grade_output('ST2',{'processes':{'P':'New'},'actions':[]},'result',stdout)['correct'])

    def test_trusted_checker_and_reference_are_absent_from_student_jail(self):
        stdout=self.execute('import os; assert not os.path.exists("/opt/runner/obo_checker.py"); assert not os.path.exists("/opt/runner/obo_runtime"); print("isolated")')
        self.assertEqual(stdout,'isolated\n')
