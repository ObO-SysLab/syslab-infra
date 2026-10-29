"""One submission per task; no boto3, DB client, answers or application IAM role."""
import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
import uuid
from execution_protocol import (ExecutionFailure, MAX_BUNDLE, MAX_RESULT, MAX_SOURCE,
    MAX_INPUT, MAX_TOTAL_SECONDS, decode, encode, parse, serialize, validate_request, validate_result)
from sandbox import Sandbox


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ExecutionFailure('Transport redirect refused')


def s3_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443) or not parsed.hostname
            or not parsed.hostname.endswith('.s3.ap-northeast-2.amazonaws.com')):
        raise ExecutionFailure('Unapproved execution transport')
    return url


def transfer(request, limit):
    # Ignore proxy settings; never forward signed capabilities to redirects/proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=10) as response:
        value = response.read(limit + 1)
    if len(value) > limit:
        raise ExecutionFailure('Transport size exceeded')
    return value


def upload_result(post, result):
    if not isinstance(post, dict) or set(post) != {'url', 'fields'} or not isinstance(post['fields'], dict):
        raise ExecutionFailure('Invalid result upload capability')
    boundary = uuid.uuid4().hex
    chunks = []
    for key, value in post['fields'].items():
        if not isinstance(key, str) or not isinstance(value, str) or any(c in key + value for c in '\r\n"'):
            raise ExecutionFailure('Invalid upload field')
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="result.json"\r\nContent-Type: application/json\r\n\r\n'.encode(),
                   serialize(result, MAX_RESULT), f'\r\n--{boundary}--\r\n'.encode()])
    transfer(urllib.request.Request(s3_url(post['url']), data=b''.join(chunks), method='POST',
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}), 65536)


def execute_request(request, sandbox_factory=Sandbox, now=time.time):
    validate_request(request)
    remaining = request['deadline'] - int(now())
    if remaining <= 0 or remaining > 180 + MAX_TOTAL_SECONDS:
        raise ExecutionFailure('Expired or invalid execution deadline')
    sandbox = sandbox_factory()
    language = request['language']
    source = decode(request['source'], MAX_SOURCE)
    filename = {'python': 'code.py', 'c': 'code.c', 'cpp': 'code.cpp'}[language]
    result = dict(version=1, requestId=request['requestId'], compileError=False, cases=[])
    if language != 'python':
        sandbox.reset(filename, source)
        compiler = ['/usr/bin/gcc', '/job/code.c', '-o', '/job/solution', '-lm'] if language == 'c' else [
            '/usr/bin/g++', '-std=c++17', '/job/code.cpp', '-o', '/job/solution', '-lm']
        compiled = sandbox.execute(compiler, b'', 10, 1024, compiler=True)
        if compiled['verdict'] != 'OK':
            result['compileError'] = True
            return result
        binary = sandbox.binary()
    total_output = 0
    for index, stdin in enumerate(request['inputs']):
        if now() >= request['deadline']:
            raise ExecutionFailure('Execution deadline exceeded')
        if total_output >= 8 * 1024 * 1024:
            outcome = dict(verdict='OUTPUT_LIMIT_EXCEEDED', runtimeMs=0, memoryKb=0, output=b'')
        else:
            if language == 'python':
                sandbox.reset(filename, source)
                command = ['/usr/bin/python3', '-I', '-B', '/job/code.py']
            else:
                sandbox.reset('solution', binary, executable=True)
                command = ['/job/solution']
            outcome = sandbox.execute(command, decode(stdin, MAX_INPUT), request['timeLimit'], request['memoryLimitMb'])
            total_output += len(outcome['output'])
        result['cases'].append(dict(index=index, verdict=outcome['verdict'], runtimeMs=outcome['runtimeMs'],
                                    memoryKb=outcome['memoryKb'], stdout=encode(outcome['output'])))
    return validate_result(result, request)


def main():
    # Independent task-wide watchdog also bounds transfer, sandbox setup and cleanup.
    # Killing PID 1's child worker exits the task; ECS init reaps remaining children.
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(MAX_TOTAL_SECONDS + 30)
    try:
        if any(os.environ.get(name) for name in ('AWS_CONTAINER_CREDENTIALS_RELATIVE_URI',
                'AWS_CONTAINER_CREDENTIALS_FULL_URI', 'AWS_ACCESS_KEY_ID', 'DB_PASSWORD')):
            raise ExecutionFailure('Unexpected runner credentials')
        request_url = os.environ.pop('REQUEST_URL')
        post = parse(os.environ.pop('RESULT_POST').encode(), 7000)
        request = parse(transfer(urllib.request.Request(s3_url(request_url)), MAX_BUNDLE), MAX_BUNDLE)
        remaining = request.get('deadline', 0) - int(time.time())
        if type(remaining) is not int or remaining <= 0:
            raise ExecutionFailure('Execution deadline exceeded')
        signal.alarm(min(MAX_TOTAL_SECONDS + 30, remaining))
        result = execute_request(request)
        upload_result(post, result)
        return 0
    except Exception:
        # Never print a signed URL, source, stdin, stdout, or exception payload.
        print('Isolated execution failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
