"""Trusted coordinator adapter. Never compiles or executes submitted code."""
import os
import re
import time
import uuid
from contextlib import closing
from execution_protocol import (ExecutionFailure, MAX_BUNDLE, MAX_RESULT, MAX_TOTAL_SECONDS,
                                make_request, serialize, parse, validate_result)


def read_object(s3, bucket, key, limit):
    response = s3.get_object(Bucket=bucket, Key=key)
    with closing(response['Body']) as body:
        if response.get('ContentLength', 0) > limit:
            raise ExecutionFailure('Stored object exceeds execution limit')
        data = body.read(limit + 1)
    if len(data) > limit:
        raise ExecutionFailure('Stored object exceeds execution limit')
    return data


def validate_definition(definition, pinned_arn):
    # A sibling container sharing the grader's task role is not an isolation boundary.
    if (definition.get('taskDefinitionArn') != pinned_arn or definition.get('status') != 'ACTIVE'
            or definition.get('taskRoleArn') or definition.get('networkMode') != 'awsvpc'
            or definition.get('pidMode') or definition.get('ipcMode')
            or definition.get('requiresCompatibilities') != ['FARGATE']
            or definition.get('runtimePlatform') != {'cpuArchitecture': 'X86_64', 'operatingSystemFamily': 'LINUX'}
            or str(definition.get('cpu')) != '1024' or str(definition.get('memory')) != '2048'):
        raise ExecutionFailure('Unsafe runner task definition')
    containers = definition.get('containerDefinitions', [])
    if len(containers) != 1:
        raise ExecutionFailure('Runner requires a separate single-container task')
    c = containers[0]
    if (c.get('name') != 'runner' or not c.get('essential') or c.get('user') != '0'
            or not c.get('readonlyRootFilesystem') or not re.fullmatch(r'.+@sha256:[a-f0-9]{64}', c.get('image', ''))
            or c.get('environment') or c.get('secrets') or c.get('environmentFiles')
            or c.get('privileged') or c.get('portMappings') or c.get('volumesFrom')
            or c.get('entryPoint') or c.get('command')):
        raise ExecutionFailure('Unsafe runner container configuration')
    linux = c.get('linuxParameters', {})
    if linux.get('capabilities', {}).get('add') or not linux.get('initProcessEnabled'):
        raise ExecutionFailure('Unsafe runner privileges')
    if not {'NET_RAW', 'NET_BIND_SERVICE', 'MKNOD', 'SETFCAP', 'SETPCAP', 'SYS_PTRACE'}.issubset(
            set(linux.get('capabilities', {}).get('drop', []))):
        raise ExecutionFailure('Runner capability reduction is missing')
    if c.get('mountPoints') != [{'sourceVolume': 'work', 'containerPath': '/work', 'readOnly': False}]:
        raise ExecutionFailure('Unexpected runner mount')
    if definition.get('volumes') != [{'name': 'work'}]:
        raise ExecutionFailure('Runner cannot mount shared storage')


class RunnerClient:
    def __init__(self, s3, ecs, env, clock=time.monotonic, wall=time.time, sleep=time.sleep):
        self.s3, self.ecs, self.clock, self.wall, self.sleep = s3, ecs, clock, wall, sleep
        names = ('RUNNER_BUCKET', 'RUNNER_CLUSTER', 'RUNNER_TASK_DEFINITION', 'RUNNER_SUBNET_ID', 'RUNNER_SECURITY_GROUP')
        if any(not env.get(name) for name in names):
            raise ExecutionFailure('Isolated runner is not configured')
        self.bucket, self.cluster, self.definition, self.subnet, self.sg = (env[name] for name in names)
        if self.bucket == env.get('S3_BUCKET', 'syslab-code'):
            raise ExecutionFailure('Runner exchange must use a separate bucket')
        if not re.fullmatch(r'arn:aws:ecs:[\w-]+:\d{12}:task-definition/[\w-]+:\d+', self.definition):
            raise ExecutionFailure('Runner task definition must pin an ARN revision')

    def execute(self, language, source, inputs, time_limit, memory_mb):
        request_id = uuid.uuid4().hex
        # Includes task startup and transport; worker itself has a shorter hard budget.
        budget = 180 + MAX_TOTAL_SECONDS
        request = make_request(request_id, language, source, inputs, time_limit, memory_mb, int(self.wall()) + budget)
        definition = self.ecs.describe_task_definition(taskDefinition=self.definition)['taskDefinition']
        validate_definition(definition, self.definition)
        prefix = f'executions/{request_id}'
        input_key, output_key = f'{prefix}/request.json', f'{prefix}/result.json'
        task_arn = None
        launched = False
        stopped = False
        try:
            self.s3.put_object(Bucket=self.bucket, Key=input_key, Body=serialize(request, MAX_BUNDLE),
                               ContentType='application/json', ServerSideEncryption='AES256')
            read_url = self.s3.generate_presigned_url('get_object', Params={'Bucket': self.bucket, 'Key': input_key}, ExpiresIn=budget)
            upload = self.s3.generate_presigned_post(Bucket=self.bucket, Key=output_key,
                Fields={'Content-Type': 'application/json', 'x-amz-server-side-encryption': 'AES256'},
                Conditions=[['content-length-range', 1, MAX_RESULT], {'Content-Type': 'application/json'},
                            {'x-amz-server-side-encryption': 'AES256'}], ExpiresIn=budget)
            overrides = {'containerOverrides': [{'name': 'runner', 'environment': [
                {'name': 'REQUEST_URL', 'value': read_url},
                {'name': 'RESULT_POST', 'value': serialize(upload, 7000).decode('utf-8')},
            ]}]}
            serialize(overrides, 8192)  # ECS RunTask overrides limit.
            launched = True  # Even a transport exception may have launched a task.
            reply = self.ecs.run_task(cluster=self.cluster, taskDefinition=self.definition,
                launchType='FARGATE', platformVersion='1.4.0', count=1, clientToken=request_id,
                startedBy=request_id, group='diveon:isolated-runner', enableExecuteCommand=False, overrides=overrides,
                networkConfiguration={'awsvpcConfiguration': {'subnets': [self.subnet],
                    'securityGroups': [self.sg], 'assignPublicIp': 'DISABLED'}})
            tasks = reply.get('tasks', [])
            if len(tasks) == 1:
                task_arn = tasks[0].get('taskArn')
            if reply.get('failures') or not task_arn or len(tasks) != 1:
                raise ExecutionFailure('Runner task was not accepted')
            deadline = self.clock() + budget
            while self.clock() < deadline:
                response = self.ecs.describe_tasks(cluster=self.cluster, tasks=[task_arn])
                if response.get('failures') or len(response.get('tasks', [])) != 1:
                    raise ExecutionFailure('Cannot verify runner status')
                task = response['tasks'][0]
                if task.get('taskArn') != task_arn or task.get('taskDefinitionArn') != self.definition:
                    raise ExecutionFailure('Unexpected runner task identity')
                if task.get('lastStatus') == 'STOPPED':
                    stopped = True
                    containers = task.get('containers', [])
                    if len(containers) != 1 or containers[0].get('name') != 'runner' or containers[0].get('exitCode') != 0:
                        raise ExecutionFailure('Runner failed without a trustworthy result')
                    result = parse(read_object(self.s3, self.bucket, output_key, MAX_RESULT), MAX_RESULT)
                    return validate_result(result, request)
                self.sleep(2)
            raise ExecutionFailure('Runner deadline exceeded')
        finally:
            # Failure to request cleanup propagates; it must not become a judged ERROR.
            # Worker deadline plus the separately deployed stale-runner monitor bound orphan work.
            try:
                if launched and not stopped:
                    if task_arn:
                        self.ecs.stop_task(cluster=self.cluster, task=task_arn, reason='Execution coordinator finished')
                    else:
                        for page in self.ecs.get_paginator('list_tasks').paginate(cluster=self.cluster, startedBy=request_id):
                            for arn in page.get('taskArns', []):
                                self.ecs.stop_task(cluster=self.cluster, task=arn, reason='Recover uncertain execution launch')
            finally:
                try:
                    self.s3.delete_object(Bucket=self.bucket, Key=input_key)
                finally:
                    self.s3.delete_object(Bucket=self.bucket, Key=output_key)


def create_storage_client():
    import boto3
    from botocore.config import Config
    # Match the worker's regional HTTPS allowlist; avoid SDK endpoint/signing defaults.
    return boto3.client('s3', region_name='ap-northeast-2',
        endpoint_url='https://s3.ap-northeast-2.amazonaws.com', config=Config(
            signature_version='s3v4', s3={'addressing_style': 'virtual'},
            connect_timeout=3, read_timeout=10, retries={'total_max_attempts': 2, 'mode': 'standard'}))


def create_runner(s3):
    import boto3
    from botocore.config import Config
    ecs = boto3.client('ecs', region_name='ap-northeast-2', config=Config(
        connect_timeout=3, read_timeout=10, retries={'total_max_attempts': 2, 'mode': 'standard'}))
    return RunnerClient(s3, ecs, os.environ)
