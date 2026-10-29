import io
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_client import RunnerClient, validate_definition, read_object
from execution_protocol import ExecutionFailure, serialize, MAX_RESULT
from test_execution_protocol import result

ARN = 'arn:aws:ecs:ap-northeast-2:123456789012:task-definition/runner:1'
ENV = dict(RUNNER_BUCKET='runner-only', RUNNER_CLUSTER='test', RUNNER_TASK_DEFINITION=ARN,
           RUNNER_SUBNET_ID='subnet-test', RUNNER_SECURITY_GROUP='sg-test')


def definition():
    return dict(taskDefinitionArn=ARN, status='ACTIVE', networkMode='awsvpc', requiresCompatibilities=['FARGATE'],
        runtimePlatform={'cpuArchitecture': 'X86_64', 'operatingSystemFamily': 'LINUX'}, cpu='1024', memory='2048',
        volumes=[{'name': 'work'}], containerDefinitions=[dict(name='runner', essential=True, user='0',
            image='example/runner@sha256:' + 'a' * 64, readonlyRootFilesystem=True,
            linuxParameters={'initProcessEnabled': True, 'capabilities': {'drop': [
                'NET_RAW', 'NET_BIND_SERVICE', 'MKNOD', 'SETFCAP', 'SETPCAP', 'SYS_PTRACE']}},
            mountPoints=[{'sourceVolume': 'work', 'containerPath': '/work', 'readOnly': False}])])


class RunnerClientTest(unittest.TestCase):
    def setUp(self):
        self.s3, self.ecs = Mock(), Mock()
        self.now = 0
        self.request = None
        def put(**args):
            import json
            self.request = json.loads(args['Body'])
        self.s3.put_object.side_effect = put
        self.s3.generate_presigned_url.return_value = 'https://runner-only.s3.ap-northeast-2.amazonaws.com/request?fake'
        self.s3.generate_presigned_post.return_value = {'url': 'https://runner-only.s3.ap-northeast-2.amazonaws.com/', 'fields': {'key': 'result'}}
        self.s3.get_object.side_effect = lambda **kw: {'Body': io.BytesIO(serialize(result(self.request), MAX_RESULT))}
        self.ecs.describe_task_definition.return_value = {'taskDefinition': definition()}
        self.ecs.run_task.return_value = {'tasks': [{'taskArn': 'task-1'}]}
        self.ecs.describe_tasks.return_value = {'tasks': [dict(taskArn='task-1', taskDefinitionArn=ARN,
            lastStatus='STOPPED', containers=[dict(name='runner', exitCode=0)])]}
        self.client = RunnerClient(self.s3, self.ecs, ENV, clock=lambda: self.now, wall=lambda: 1000,
                                   sleep=lambda n: setattr(self, 'now', self.now + n))

    def execute(self): return self.client.execute('python', b'print(1)', [b''], 1, 256)

    def test_separate_task_has_no_db_or_aws_credentials_and_is_pinned(self):
        output = self.execute()
        self.assertEqual(output['cases'][0]['verdict'], 'OK')
        call = self.ecs.run_task.call_args.kwargs
        self.assertEqual(call['taskDefinition'], ARN)
        self.assertFalse(call['enableExecuteCommand'])
        self.assertEqual(call['platformVersion'], '1.4.0')
        values = call['overrides']['containerOverrides'][0]['environment']
        self.assertEqual({v['name'] for v in values}, {'REQUEST_URL', 'RESULT_POST'})
        self.assertNotIn('expected', self.request)
        self.assertEqual(self.request['memoryLimitMb'], 256)
        self.s3.generate_presigned_post.assert_called_once()
        self.assertIn(['content-length-range', 1, MAX_RESULT], self.s3.generate_presigned_post.call_args.kwargs['Conditions'])
        self.assertEqual(self.s3.delete_object.call_count, 2)
        self.ecs.stop_task.assert_not_called()

    def test_unsafe_role_secret_shared_mount_or_image_fails_before_launch(self):
        changes = []
        for key, value in [('taskRoleArn', 'admin'), ('pidMode', 'task'), ('volumes', [{'name': 'work', 'efsVolumeConfiguration': {}}])]:
            bad = definition(); bad[key] = value; changes.append(bad)
        for key, value in [('secrets', [{'name': 'DB_PASSWORD'}]), ('environment', [{'name': 'DB_PASSWORD', 'value': 'fake'}]),
            ('image', 'example:latest'), ('readonlyRootFilesystem', False), ('command', ['sh']), ('portMappings', [{'containerPort': 80}])]:
            bad = definition(); bad['containerDefinitions'][0][key] = value; changes.append(bad)
        for bad in changes:
            self.ecs.describe_task_definition.return_value = {'taskDefinition': bad}
            with self.assertRaises(ExecutionFailure): self.execute()
        self.ecs.run_task.assert_not_called(); self.s3.put_object.assert_not_called()

    def test_missing_config_or_shared_bucket_fails_closed(self):
        for env in ({}, {**ENV, 'RUNNER_BUCKET': 'syslab-code'}, {**ENV, 'RUNNER_TASK_DEFINITION': 'runner:latest'}):
            with self.assertRaises(ExecutionFailure): RunnerClient(self.s3, self.ecs, env)

    def test_running_task_deadline_stops_only_owned_task(self):
        self.ecs.describe_tasks.return_value['tasks'][0]['lastStatus'] = 'RUNNING'
        with self.assertRaisesRegex(ExecutionFailure, 'deadline'): self.execute()
        self.ecs.stop_task.assert_called_once_with(cluster='test', task='task-1', reason='Execution coordinator finished')

    def test_failed_runner_is_infrastructure_failure_not_compile_verdict(self):
        self.ecs.describe_tasks.return_value['tasks'][0]['containers'][0]['exitCode'] = 124
        with self.assertRaises(ExecutionFailure): self.execute()
        self.s3.get_object.assert_not_called()

    def test_unknown_launch_recovers_by_unique_started_by(self):
        self.ecs.run_task.side_effect = TimeoutError('no response')
        self.ecs.get_paginator.return_value.paginate.return_value = [{'taskArns': ['uncertain-task']}]
        with self.assertRaises(TimeoutError): self.execute()
        self.ecs.stop_task.assert_called_once_with(cluster='test', task='uncertain-task', reason='Recover uncertain execution launch')
        self.assertEqual(self.ecs.get_paginator.return_value.paginate.call_args.kwargs['startedBy'], self.request['requestId'])

    def test_cleanup_error_is_not_swallowed(self):
        self.ecs.describe_tasks.side_effect = RuntimeError('status unavailable')
        self.ecs.stop_task.side_effect = RuntimeError('stop unavailable')
        with self.assertRaisesRegex(RuntimeError, 'stop unavailable'): self.execute()
        self.assertEqual(self.s3.delete_object.call_count, 2)

    def test_wrong_task_identity_stops_without_accepting_result(self):
        self.ecs.describe_tasks.return_value['tasks'][0]['taskDefinitionArn'] = 'other'
        with self.assertRaises(ExecutionFailure): self.execute()
        self.ecs.stop_task.assert_called_once(); self.s3.get_object.assert_not_called()

    def test_failed_input_delete_still_attempts_result_cleanup(self):
        self.s3.delete_object.side_effect = [RuntimeError('delete unavailable'), None]
        with self.assertRaisesRegex(RuntimeError, 'delete unavailable'): self.execute()
        self.assertEqual(self.s3.delete_object.call_count, 2)
        keys = [call.kwargs['Key'] for call in self.s3.delete_object.call_args_list]
        self.assertTrue(keys[0].endswith('/request.json'))
        self.assertTrue(keys[1].endswith('/result.json'))

    def test_bounded_read_closes_stream_even_on_rejection(self):
        body = io.BytesIO(b'longer')
        self.s3.get_object.side_effect = None; self.s3.get_object.return_value = {'Body': body, 'ContentLength': 6}
        with self.assertRaises(ExecutionFailure): read_object(self.s3, 'x', 'y', 3)
        self.assertTrue(body.closed)


if __name__ == '__main__': unittest.main()
