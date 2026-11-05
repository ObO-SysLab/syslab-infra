"""Trusted semantic checker; student programs remain in the isolated execution task."""
import hashlib
import json
from pathlib import Path
import subprocess


class OboCheckerError(RuntimeError):
    """Configuration or checker infrastructure failure, never a student's wrong answer."""


RUNTIME = Path(__file__).resolve().parent / 'obo_runtime'


def verify_release():
    manifest = json.loads((RUNTIME / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        if Path(name).name != name or not (name.endswith('.mjs') or name == 'diagram-contract.json'):
            raise OboCheckerError('Invalid checker manifest')
        if hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest() != expected:
            raise OboCheckerError('Checker release checksum mismatch')


def invoke(request, *, node='node', timeout=5):
    verify_release()
    payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(payload) > 8 * 1048576:
        raise OboCheckerError('Checker request exceeds its budget')
    try:
        result = subprocess.run(
            [node, '--max-old-space-size=96', str(RUNTIME / 'checker.mjs')],
            input=payload, capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OboCheckerError('Checker process unavailable') from error
    if result.returncode != 0 or len(result.stdout) > 2 * 1048576:
        raise OboCheckerError('Checker process failed')
    try:
        response = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise OboCheckerError('Invalid checker response') from error
    if not isinstance(response, dict) or response.get('ok') is not True:
        raise OboCheckerError('Checker rejected configuration')
    return response


def grade_output(scenario_id, model_input, mode, stdout):
    response = invoke({'operation': 'grade', 'scenarioId': scenario_id,
                       'input': model_input, 'mode': mode, 'stdout': stdout})
    if response.get('kind') not in ('correct', 'wrong'):
        raise OboCheckerError('Invalid model or grading configuration')
    if type(response.get('correct')) is not bool or type(response.get('traceValid')) is not bool:
        raise OboCheckerError('Invalid checker verdict')
    return response
