import os
import sys
import subprocess
import time
import boto3
import psycopg2

# ─────────────────────────────────────────
# 환경변수 수신 (Lambda RunTask 시 주입)
# ─────────────────────────────────────────
SUBMISSION_ID  = os.environ['SUBMISSION_ID']
CODE_S3_KEY    = os.environ['CODE_S3_KEY']       # e.g. submissions/uuid/Main.py
LANGUAGE       = os.environ['LANGUAGE']           # python | c | cpp
PROBLEM_ID     = os.environ['PROBLEM_ID']
TIME_LIMIT     = int(os.environ['TIME_LIMIT'])    # 초 단위
TESTCASE_COUNT = int(os.environ['TESTCASE_COUNT'])

DB_HOST     = os.environ['DB_HOST']
DB_NAME     = os.environ.get('DB_NAME', 'syslab')
DB_USER     = os.environ.get('DB_USER', 'postgres')
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_PORT     = int(os.environ.get('DB_PORT', '5432'))

S3_BUCKET   = os.environ.get('S3_BUCKET', 'syslab-code')

# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────
WORK_DIR        = '/tmp'
COMPILE_TIMEOUT = 10  # 컴파일 제한 시간 (초)

# ─────────────────────────────────────────
# DB 연결
# ─────────────────────────────────────────
def get_db_conn():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
        connect_timeout=5
    )

# ─────────────────────────────────────────
# submissions 상태 업데이트
# ─────────────────────────────────────────
def update_status(conn, status, result=None, runtime_ms=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE submissions
            SET status     = %s,
                result     = %s,
                runtime_ms = %s
            WHERE id = %s
            """,
            (status, result, runtime_ms, SUBMISSION_ID)
        )
    conn.commit()

# ─────────────────────────────────────────
# S3 다운로드
# ─────────────────────────────────────────
def download_from_s3(s3_client, s3_key, local_path):
    s3_client.download_file(S3_BUCKET, s3_key, local_path)

# ─────────────────────────────────────────
# 언어별 컴파일
# 반환값: (success: bool, error_message: str)
# ─────────────────────────────────────────
def compile_code(language, code_path):
    try:
        if language == 'c':
            result = subprocess.run(
                ['gcc', code_path, '-o', f'{WORK_DIR}/solution', '-lm'],
                capture_output=True, text=True, timeout=COMPILE_TIMEOUT
            )
        elif language == 'cpp':
            result = subprocess.run(
                ['g++', '-std=c++17', code_path, '-o', f'{WORK_DIR}/solution', '-lm'],
                capture_output=True, text=True, timeout=COMPILE_TIMEOUT
            )
        else:
            # python은 컴파일 불필요
            return True, ''

        if result.returncode != 0:
            return False, result.stderr[:500]
        return True, ''

    except subprocess.TimeoutExpired:
        return False, 'Compile timeout'

# ─────────────────────────────────────────
# 단일 테스트케이스 실행
# 반환값: (verdict, runtime_ms, stdout)
# ─────────────────────────────────────────
def run_testcase(language, code_path, input_data, time_limit):
    cmd = ['python3', code_path] if language == 'python' else [f'{WORK_DIR}/solution']

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=time_limit
        )
        runtime_ms = int((time.time() - start) * 1000)

        if proc.returncode != 0:
            return 'RUNTIME_ERROR', runtime_ms, ''

        return 'OK', runtime_ms, proc.stdout

    except subprocess.TimeoutExpired:
        return 'TIME_LIMIT_EXCEEDED', time_limit * 1000, ''

# ─────────────────────────────────────────
# 정답 비교 (공백/줄바꿈 정규화)
# ─────────────────────────────────────────
def compare_output(actual: str, expected: str) -> bool:
    actual_lines   = [line.rstrip() for line in actual.strip().splitlines()]
    expected_lines = [line.rstrip() for line in expected.strip().splitlines()]
    return actual_lines == expected_lines

# ─────────────────────────────────────────
# 메인 채점 로직
# ─────────────────────────────────────────
def main():
    s3   = boto3.client('s3', region_name='ap-northeast-2')
    conn = get_db_conn()

    try:
        # 채점 시작 — JUDGING 상태로 변경
        update_status(conn, 'JUDGING')

        # ① 코드 파일 S3에서 다운로드
        ext_map   = {'python': 'py', 'c': 'c', 'cpp': 'cpp'}
        code_path = f'{WORK_DIR}/code.{ext_map[LANGUAGE]}'
        download_from_s3(s3, CODE_S3_KEY, code_path)

        # ② 컴파일
        compile_ok, compile_err = compile_code(LANGUAGE, code_path)
        if not compile_ok:
            update_status(conn, 'COMPLETED', result='COMPILE_ERROR')
            print(f'[COMPILE_ERROR] {compile_err}')
            return

        # ③ 테스트케이스 순회
        final_verdict  = 'ACCEPTED'
        max_runtime_ms = 0

        for i in range(1, TESTCASE_COUNT + 1):
            input_path  = f'{WORK_DIR}/input_{i}.txt'
            output_path = f'{WORK_DIR}/output_{i}.txt'

            download_from_s3(s3, f'testcases/prob-{PROBLEM_ID}/input_{i}.txt',  input_path)
            download_from_s3(s3, f'testcases/prob-{PROBLEM_ID}/output_{i}.txt', output_path)

            with open(input_path,  'r') as f:
                input_data = f.read()
            with open(output_path, 'r') as f:
                expected = f.read()

            verdict, runtime_ms, actual_output = run_testcase(
                LANGUAGE, code_path, input_data, TIME_LIMIT
            )
            max_runtime_ms = max(max_runtime_ms, runtime_ms)

            print(f'  [TC {i}/{TESTCASE_COUNT}] {verdict} ({runtime_ms}ms)')

            if verdict != 'OK':
                final_verdict = verdict
                break

            if not compare_output(actual_output, expected):
                final_verdict = 'WRONG_ANSWER'
                break

        # ④ 최종 결과 RDS 저장
        update_status(conn, 'COMPLETED', result=final_verdict, runtime_ms=max_runtime_ms)
        print(f'[DONE] submissionId={SUBMISSION_ID} result={final_verdict} runtime={max_runtime_ms}ms')

    except Exception as e:
        print(f'[ERROR] {e}', file=sys.stderr)
        try:
            update_status(conn, 'COMPLETED', result='SYSTEM_ERROR')
        except Exception:
            pass
        sys.exit(1)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
