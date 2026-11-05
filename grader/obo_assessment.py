"""Immutable, server-owned grading settings; no cloud clients or student execution."""
import json
from execution_protocol import MAX_INPUT, MAX_OUTPUT, MAX_CASES, MAX_BUNDLE, integer
from obo_checker import grade_output, invoke, OboCheckerError


def load_revision(conn, submission_id, problem_id):
    with conn.cursor() as cursor:
        cursor.execute('''SELECT s.problem_revision_id, r.problem_id, r.snapshot
                          FROM solve_submission s LEFT JOIN problem_revision r ON r.id=s.problem_revision_id
                          WHERE s.id=%s AND s.prob_id=%s''', (submission_id, problem_id))
        row = cursor.fetchone()
    if row is None:
        raise OboCheckerError('Submission context missing')
    if row[0] is None:
        return None
    if row[2] is None or str(row[1]) != str(problem_id):
        raise OboCheckerError('Submission revision missing or mismatched')
    snapshot = row[2]
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    if snapshot.get('v') != 1 or snapshot.get('type') != 'coding':
        raise OboCheckerError('Unsupported grading revision')
    return snapshot


def revision_cases(snapshot):
    cases = snapshot.get('testcases')
    if not isinstance(cases, list):
        raise OboCheckerError('Missing revision testcases')
    integer(len(cases), 1, MAX_CASES)
    inputs, outputs = [], []
    for case in cases:
        if not isinstance(case.get('input'), str) or not isinstance(case.get('output'), str):
            raise OboCheckerError('Invalid testcase')
        stdin = case['input'].encode('utf-8')
        if len(stdin) > MAX_INPUT or len(case['output'].encode('utf-8')) > MAX_OUTPUT:
            raise OboCheckerError('Testcase exceeds execution budget')
        inputs.append(stdin)
        outputs.append(case['output'])
    if sum(map(len, inputs)) > MAX_BUNDLE:
        raise OboCheckerError('Input bundle exceeds budget')
    return inputs, outputs


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result or key in ('__proto__', 'prototype', 'constructor'):
                raise OboCheckerError('Ambiguous or reserved JSON key')
            result[key] = item
        return result
    def constant(_):
        raise OboCheckerError('Non-finite JSON number')
    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as error:
        raise OboCheckerError('Invalid testcase JSON') from error


def semantic_verdict(snapshot, case_index, stdout):
    if snapshot.get('judgeProfile') != 'obo-v1' or snapshot.get('modelVersion') != 1:
        raise OboCheckerError('Unsupported semantic grading profile')
    document = snapshot.get('obo', {}).get('simulation', {})
    question = document.get('question', {})
    instances = [item for item in document.get('instances', []) if item.get('id') == question.get('gradingInstanceId')]
    if len(instances) != 1:
        raise OboCheckerError('Missing grading model')
    if question.get('ioFormat') == 'rr-segments-v1':
        if instances[0]['scenarioId'] != 'RR' or question.get('assessmentMode') != 'result':
            raise OboCheckerError('Invalid RR grading contract')
        verdict = invoke({'operation': 'rrGrade', 'input': snapshot['testcases'][case_index]['input'], 'stdout': stdout})
        if verdict.get('kind') not in ('correct', 'wrong') or type(verdict.get('correct')) is not bool:
            raise OboCheckerError('Invalid RR verdict')
        return verdict
    # Each testcase's stdin is its complete model input; UI inputs are explanation presets only.
    case_input = strict_json(snapshot['testcases'][case_index]['input'])
    return grade_output(instances[0]['scenarioId'], case_input, question.get('assessmentMode'), stdout)


def public_record(snapshot, case_index, verdict):
    case = snapshot['testcases'][case_index]
    if case.get('isSample') is not True:
        return None
    document = snapshot['obo']['simulation']
    instance = next(item for item in document['instances'] if item['id'] == document['question']['gradingInstanceId'])
    rr = document['question'].get('ioFormat') == 'rr-segments-v1'
    if rr:
        parsed = invoke({'operation': 'rrReference', 'input': case['input']})
        model_input, reference = parsed['input'], parsed['reference']
    else:
        model_input = strict_json(case['input'])
        reference = invoke({'operation': 'reference', 'scenarioId': instance['scenarioId'], 'input': model_input})
    reference.pop('ok', None)
    reported = verdict.get('reported')
    if verdict['correct'] and not verdict['traceValid'] and reported and 'events' in reported:
        reported = {'v': 1, 'result': reported['result']}
    return dict(caseIndex=case_index + 1, scenarioId=instance['scenarioId'], input=model_input,
                reference=reference, reported=reported, correct=verdict['correct'],
                traceValid=verdict['traceValid'], message=verdict.get('message', ''), **({'kind':'rr-schedule'} if rr else {}))


def public_diagram_record(snapshot, case_index, stdout):
    """Public execution output for replay only; never changes text-v1 grading."""
    if not snapshot or (snapshot.get('obo') or {}).get('mode') != 'coding_diff':
        return None
    case = snapshot['testcases'][case_index]
    if case.get('isSample') is not True:
        return None
    if len(stdout.encode('utf-8')) > 1048576:
        return {'caseIndex': case_index + 1, 'kind': 'diagram', 'omitted': '출력 재생 기록이 1MiB를 초과했습니다.'}
    return {'caseIndex': case_index + 1, 'kind': 'diagram', 'stdout': stdout}
