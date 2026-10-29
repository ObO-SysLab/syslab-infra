"""Bounded, credential-free execution protocol shared by coordinator and runner."""
import base64
import binascii
import json
import re

MAX_SOURCE = 1024 * 1024
MAX_INPUT = 1024 * 1024
MAX_OUTPUT = 1024 * 1024  # stdout + stderr per execution
MAX_CASES = 100
MAX_BUNDLE = 16 * 1024 * 1024
MAX_RESULT = 16 * 1024 * 1024
MAX_TIME = 30
MAX_MEMORY_MB = 1024
MAX_TOTAL_SECONDS = 600
VERDICTS = {'OK', 'TIME_LIMIT_EXCEEDED', 'RUNTIME_ERROR', 'OUTPUT_LIMIT_EXCEEDED'}


class ExecutionFailure(RuntimeError):
    """Infrastructure/protocol failure, never a judged submission verdict."""


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ExecutionFailure('Invalid execution limit')
    return value


def encode(data):
    return base64.b64encode(data).decode('ascii')


def decode(value, limit):
    if not isinstance(value, str) or len(value) > 4 * ((limit + 2) // 3):
        raise ExecutionFailure('Payload exceeds limit')
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ExecutionFailure('Invalid execution payload') from None
    if len(data) > limit:
        raise ExecutionFailure('Payload exceeds limit')
    return data


def serialize(value, limit):
    data = json.dumps(value, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > limit:
        raise ExecutionFailure('Execution message exceeds limit')
    return data


def parse(data, limit):
    if len(data) > limit:
        raise ExecutionFailure('Execution message exceeds limit')
    try:
        return json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise ExecutionFailure('Invalid execution response') from None


def make_request(request_id, language, source, inputs, time_limit, memory_mb, deadline):
    request = dict(version=1, requestId=request_id, language=language, source=encode(source),
                   inputs=[encode(item) for item in inputs], timeLimit=time_limit,
                   memoryLimitMb=memory_mb, deadline=deadline)
    validate_request(request)
    serialize(request, MAX_BUNDLE)
    return request


def validate_request(request):
    fields = {'version', 'requestId', 'language', 'source', 'inputs', 'timeLimit', 'memoryLimitMb', 'deadline'}
    if not isinstance(request, dict) or set(request) != fields or type(request['version']) is not int or request['version'] != 1:
        raise ExecutionFailure('Unsupported execution request')
    if not isinstance(request['requestId'], str) or not re.fullmatch(r'[a-f0-9]{32}', request['requestId']):
        raise ExecutionFailure('Invalid execution identity')
    if request['language'] not in ('python', 'c', 'cpp'):
        raise ExecutionFailure('Unsupported language')
    integer(request['timeLimit'], 1, MAX_TIME)
    integer(request['memoryLimitMb'], 16, MAX_MEMORY_MB)
    integer(request['deadline'], 1, 2**53 - 1)
    if not isinstance(request['inputs'], list):
        raise ExecutionFailure('Invalid testcases')
    integer(len(request['inputs']), 1, MAX_CASES)
    if 20 + len(request['inputs']) * (request['timeLimit'] + 2) > MAX_TOTAL_SECONDS:
        raise ExecutionFailure('Total execution budget exceeds limit')
    decode(request['source'], MAX_SOURCE)
    for item in request['inputs']:
        decode(item, MAX_INPUT)


def validate_result(result, request):
    if not isinstance(result, dict) or set(result) != {'version', 'requestId', 'compileError', 'cases'}:
        raise ExecutionFailure('Invalid execution result')
    if type(result['version']) is not int or result['version'] != 1 or result['requestId'] != request['requestId']:
        raise ExecutionFailure('Execution identity mismatch')
    if type(result['compileError']) is not bool or not isinstance(result['cases'], list):
        raise ExecutionFailure('Invalid execution result')
    expected_count = 0 if result['compileError'] else len(request['inputs'])
    if len(result['cases']) != expected_count:
        raise ExecutionFailure('Incomplete execution result')
    for index, item in enumerate(result['cases']):
        if not isinstance(item, dict) or set(item) != {'index', 'verdict', 'runtimeMs', 'memoryKb', 'stdout'}:
            raise ExecutionFailure('Invalid testcase result')
        if type(item['index']) is not int or item['index'] != index or item['verdict'] not in VERDICTS:
            raise ExecutionFailure('Invalid testcase identity or verdict')
        integer(item['runtimeMs'], 0, (request['timeLimit'] + 2) * 1000)
        integer(item['memoryKb'], 0, MAX_MEMORY_MB * 1024)
        decode(item['stdout'], MAX_OUTPUT)
    serialize(result, MAX_RESULT)
    return result
