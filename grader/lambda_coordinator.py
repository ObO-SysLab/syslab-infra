"""Lambda coordinator: grades one SQS job per record without starting a coordinator Fargate task.

Same trusted code as the ECS coordinator (grader.main); only how the job arrives changes.
The isolated runner is still a fresh Fargate task per submission (runner_client).
Grading failures keep the existing DB attempt/deadline recovery: they are not returned to SQS.
Only a message that could not be started (bad body, missing secret) is returned for SQS retry.
"""
import json
import os
import sys
import time

import grader

# Same admission rules as launch.mjs buildRunTaskInput (the Fargate trigger this replaces).
FIELDS = (('submissionId', 'SUBMISSION_ID'), ('s3Key', 'CODE_S3_KEY'), ('language', 'LANGUAGE'),
          ('problemId', 'PROBLEM_ID'), ('timeLimit', 'TIME_LIMIT'), ('testcaseCount', 'TESTCASE_COUNT'),
          ('memoryLimit', 'MEMORY_LIMIT'))
RANGES = (('timeLimit', 1, 30), ('memoryLimit', 16, 1024), ('testcaseCount', 1, 100))
DATABASE_KEYS = ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')
PASSTHROUGH = ('S3_BUCKET', 'REDIS_HOST', 'REDIS_PORT')

_database = None


def warm_up(invoke=None, clock=time.monotonic):
    """Start node and load the OBO runtime once while the Lambda container starts.

    2026-11-12: the first grading in a fresh container failed as an infrastructure error. The
    checker has a 5 s limit, and a cold container still pages in node and the runtime modules
    from the image. A warm container graded the same problem on the first attempt. checker.mjs
    imports every runtime module before it reads the request, so any request warms it. An unknown
    operation is rejected without doing any work.
    """
    started = clock()
    try:
        if invoke is None:
            from obo_checker import invoke
        invoke({'operation': 'warmup'}, timeout=30)
        state = 'ready'
    except Exception as error:
        # obo_checker raises fixed messages only (no request data); rejection means node ran.
        state = f'{type(error).__name__}: {error}'
    print(f'[WARMUP] node checker {state} in {int((clock() - started) * 1000)} ms')
    return state


def database_settings(load=None):
    """Read the grading DB secret once per warm container (the ECS task got it injected)."""
    global _database
    if _database is None:
        if load is None:
            import boto3
            client = boto3.client('secretsmanager', region_name='ap-northeast-2')
            load = lambda arn: client.get_secret_value(SecretId=arn)['SecretString']
        values = json.loads(load(os.environ['DB_SECRET_ARN']))
        if not values.get('DB_HOST') or not values.get('DB_PASSWORD'):
            raise ValueError('Grading database secret is incomplete')
        _database = {k: str(values[k]) for k in DATABASE_KEYS if values.get(k) is not None}
    return _database


def job_settings(job, env, database):
    for key, _ in FIELDS:
        if job.get(key) is None:
            raise ValueError(f'Missing field: {key}')
    if job['language'] not in ('python', 'c', 'cpp'):
        raise ValueError('Unsupported language')
    for key, low, high in RANGES:
        value = job[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'Invalid {key}')
    if 20 + job['testcaseCount'] * (job['timeLimit'] + 2) > 600:
        raise ValueError('Execution budget exceeded')
    attempt = job.get('attemptNo')
    if attempt is not None and (type(attempt) is not int or not 1 <= attempt <= 3):
        raise ValueError('Invalid grading attempt')

    values = {name: str(job[key]) for key, name in FIELDS}
    if attempt is not None:
        values['GRADING_ATTEMPT'] = str(attempt)
    contest = job.get('contestContext')
    if contest is not None:
        if any(contest.get(k) is None for k in ('contestId', 'contestProblemId', 'points')):
            raise ValueError('Invalid contest context')
        values.update(CONTEST_ID=str(contest['contestId']), CONTEST_PROBLEM_ID=str(contest['contestProblemId']),
                      CONTEST_POINTS=str(contest['points']))
    values.update({k: env[k] for k in PASSTHROUGH if env.get(k)})
    values.update(database)
    return values


def handler(event, context, grade=None, settings=None):
    grade = grade or grader.main
    settings = settings or database_settings
    failures = []
    for record in event.get('Records', []):
        try:
            values = job_settings(json.loads(record['body']), os.environ, settings())
        except Exception as error:
            # Never print the body, source keys or credentials.
            print(f'[REJECTED] message={record.get("messageId")} type={type(error).__name__}', file=sys.stderr)
            failures.append({'itemIdentifier': record['messageId']})
            continue
        grader.configure(values)
        # 0: graded or skipped (terminal/stale attempt). 1: infrastructure failure already reported
        # to coding_grade_job, which the backend re-dispatches as the next attempt.
        try:
            status = grade()
        except Exception as error:
            # e.g. the DB was unreachable before grading began: nothing was recorded, so let SQS retry.
            # Attempt fencing in begin_grading keeps a redelivered message from grading twice.
            print(f'[RETRY] submissionId={values["SUBMISSION_ID"]} type={type(error).__name__}', file=sys.stderr)
            failures.append({'itemIdentifier': record['messageId']})
            continue
        print(f'[COORDINATED] submissionId={values["SUBMISSION_ID"]} attempt={values.get("GRADING_ATTEMPT", "legacy")} status={status}')
    return {'batchItemFailures': failures}


# Lambda container start (init phase): warm the checker before the first job arrives.
if os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
    warm_up()
