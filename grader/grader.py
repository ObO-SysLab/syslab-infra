import os
import sys
import subprocess
import time
import resource
import boto3
import psycopg2
import redis as redis_module

# ───────────────────────────────────────────
# 환경변수 읽기 (Lambda RunTask 시 주입)
# ───────────────────────────────────────────
SUBMISSION_ID  = os.environ['SUBMISSION_ID']   # solve_submission.id (bigint, 문자열로 수신)
CODE_S3_KEY    = os.environ['CODE_S3_KEY']     # e.g. submissions/123/Main.py
LANGUAGE       = os.environ['LANGUAGE']         # python | c | cpp
PROBLEM_ID     = os.environ['PROBLEM_ID']
TIME_LIMIT     = int(os.environ['TIME_LIMIT'])  # 초 단위
TESTCASE_COUNT = int(os.environ['TESTCASE_COUNT'])

# 대회 컨텍스트 (대회 제출일 때만 세팅, 일반 제출이면 None)
CONTEST_ID         = os.environ.get('CONTEST_ID')
CONTEST_PROBLEM_ID = os.environ.get('CONTEST_PROBLEM_ID')
CONTEST_POINTS     = int(os.environ['CONTEST_POINTS']) if os.environ.get('CONTEST_POINTS') else None

IS_CONTEST = CONTEST_ID is not None

DB_HOST     = os.environ['DB_HOST']
DB_NAME     = os.environ.get('DB_NAME', 'syslab')
DB_USER     = os.environ.get('DB_USER', 'postgres')
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_PORT     = int(os.environ.get('DB_PORT', '5432'))

S3_BUCKET  = os.environ.get('S3_BUCKET', 'syslab-code')
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', '6379'))

# ───────────────────────────────────────────
# 상수
# ───────────────────────────────────────────
WORK_DIR        = '/tmp'
COMPILE_TIMEOUT = 10

# ───────────────────────────────────────────
# DB 연결
# ───────────────────────────────────────────
def get_db_conn():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
        connect_timeout=5
    )

# ───────────────────────────────────────────
# [공통] solve_submission 상태 업데이트
# ───────────────────────────────────────────
def update_submission_state(conn, state):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE solve_submission SET submission_state = %s WHERE id = %s",
            (state, SUBMISSION_ID)
        )
    conn.commit()

# ───────────────────────────────────────────
# [공통] solve_result + solve_result_coding INSERT
# ───────────────────────────────────────────
def insert_result(conn, result_status, memory_usage, runtime, code_size):
    memory_mb = memory_usage // 1024
    score = 100 if result_status == 'CORRECT' else 0

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO solve_result (submission_id, result_status, created_at)
            VALUES (%s, %s, NOW())
            RETURNING id
            """,
            (SUBMISSION_ID, result_status)
        )
        result_id = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO solve_result_coding (result_id, memory_usage, runtime, code_size, score)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (result_id, memory_mb, runtime, code_size, score)
        )
    conn.commit()

# ───────────────────────────────────────────
# [공통] problem_summary 제출/정답 카운트 증가
# ───────────────────────────────────────────
def update_problem_counts(conn, is_correct):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE problem_summary
            SET submitted_count = submitted_count + 1,
                solved_count = solved_count + CASE WHEN %s THEN 1 ELSE 0 END
            WHERE id = %s
            """,
            (is_correct, PROBLEM_ID)
        )
    conn.commit()

# ───────────────────────────────────────────
# [대회] solve_submission_id로 contest_submission 조회
# ───────────────────────────────────────────
def get_contest_submission(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id FROM contest_submission
            WHERE submission_id = %s     
            """,
            (SUBMISSION_ID,)
        )
        row = cur.fetchone()
    return row

# ───────────────────────────────────────────
# [대회] contest_submission 결과 업데이트
# ───────────────────────────────────────────
def update_contest_submission_result(conn, contest_submission_id, is_correct):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contest_submission
            SET is_correct = %s, submission_status = 'COMPLETED'
            WHERE id = %s
            """,
            (is_correct, contest_submission_id)
        )
    conn.commit()

# ───────────────────────────────────────────
# [대회] 첫 정답 여부 확인
# ───────────────────────────────────────────
def already_solved(conn, contest_id, contest_problem_id, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM contest_submission
            WHERE contest_id = %s AND contest_problem_id = %s
              AND user_id = %s AND is_correct = true
            """,
            (contest_id, contest_problem_id, user_id)
        )
        count = cur.fetchone()[0]
    return count > 0

# ───────────────────────────────────────────
# [대회] 점수 반영 → 갱신된 총점 반환
# ───────────────────────────────────────────
def add_contest_score(conn, contest_id, user_id, points):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contest_participant
            SET score = score + %s, last_solved_at = NOW()
            WHERE contest_id = %s AND user_id = %s
            RETURNING score
            """,
            (points, contest_id, user_id)
        )
        new_score = cur.fetchone()[0]
    conn.commit()
    return new_score

# ───────────────────────────────────────────
# [대회] Redis 스코어보드 갱신
# ───────────────────────────────────────────
def update_scoreboard(contest_id, user_id, new_total_score):
    r = redis_module.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    epoch_second    = int(time.time())
    composite_score = new_total_score * 1_000_000 - epoch_second
    r.zadd(f"scoreboard:{contest_id}", {str(user_id): composite_score})

# ───────────────────────────────────────────
# [대회] 채점 완료 후 대회 전용 후처리
# ───────────────────────────────────────────
def handle_contest_result(conn, is_correct):
    row = get_contest_submission(conn)
    if row is None:
        print('[WARN] contest_submission not found for solve_submission_id=' + SUBMISSION_ID, file=sys.stderr)
        return

    contest_submission_id, user_id = row
    update_contest_submission_result(conn, contest_submission_id, is_correct)

    if not is_correct:
        return

    # 첫 정답일 때만 점수 반영 + Redis 갱신
    if already_solved(conn, CONTEST_ID, CONTEST_PROBLEM_ID, user_id):
        return

    new_total_score = add_contest_score(conn, CONTEST_ID, user_id, CONTEST_POINTS)

    try:
        update_scoreboard(CONTEST_ID, user_id, new_total_score)
    except Exception as e:
        # Redis 장애 시 DB는 이미 커밋됐으므로 무시 (스코어보드는 DB fallback으로 복구 가능)
        print(f'[WARN] Redis scoreboard update failed: {e}', file=sys.stderr)

# ───────────────────────────────────────────
# S3 다운로드
# ───────────────────────────────────────────
def download_from_s3(s3_client, s3_key, local_path):
    s3_client.download_file(S3_BUCKET, s3_key, local_path)

# ───────────────────────────────────────────
# 언어별 컴파일
# 반환값: (success: bool, error_message: str)
# ───────────────────────────────────────────
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
            return True, ''
        if result.returncode != 0:
            return False, result.stderr[:500]
        return True, ''

    except subprocess.TimeoutExpired:
        return False, 'Compile timeout'

# ───────────────────────────────────────────
# 단일 테스트케이스 실행
# 반환값: (verdict, runtime_ms, memory_kb, stdout)
# ───────────────────────────────────────────
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
        memory_kb  = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

        if proc.returncode != 0:
            return 'RUNTIME_ERROR', runtime_ms, memory_kb, ''

        return 'OK', runtime_ms, memory_kb, proc.stdout

    except subprocess.TimeoutExpired:
        return 'TIME_LIMIT_EXCEEDED', time_limit * 1000, 0, ''

# ───────────────────────────────────────────
# 정답 비교 (공백/줄바꿈 정규화)
# ───────────────────────────────────────────
def compare_output(actual: str, expected: str) -> bool:
    actual_lines   = [line.rstrip() for line in actual.strip().splitlines()]
    expected_lines = [line.rstrip() for line in expected.strip().splitlines()]
    return actual_lines == expected_lines

# ───────────────────────────────────────────
# 메인 채점 로직
# ───────────────────────────────────────────
def main():
    s3   = boto3.client('s3', region_name='ap-northeast-2')
    conn = get_db_conn()

    try:
        # 채점 시작 → JUDGING 상태로 변경 (solve_submission은 항상 업데이트)
        update_submission_state(conn, 'JUDGING')

        # ① 코드 파일 S3에서 다운로드
        ext_map   = {'python': 'py', 'c': 'c', 'cpp': 'cpp'}
        code_path = f'{WORK_DIR}/code.{ext_map[LANGUAGE]}'
        download_from_s3(s3, CODE_S3_KEY, code_path)
        code_size = os.path.getsize(code_path)

        # ② 컴파일
        compile_ok, compile_err = compile_code(LANGUAGE, code_path)
        if not compile_ok:
            update_submission_state(conn, 'COMPLETED')
            insert_result(conn, 'ERROR', 0, 0, 0)
            if IS_CONTEST:
                handle_contest_result(conn, False)
            print(f'[COMPILE_ERROR] {compile_err}')
            return

        # ③ 테스트케이스 순회
        final_verdict  = 'CORRECT'
        max_runtime_ms = 0
        max_memory_kb  = 0

        for i in range(1, TESTCASE_COUNT + 1):
            input_path  = f'{WORK_DIR}/input_{i}.txt'
            output_path = f'{WORK_DIR}/output_{i}.txt'

            download_from_s3(s3, f'testcases/prob-{PROBLEM_ID}/input_{i}.txt',  input_path)
            download_from_s3(s3, f'testcases/prob-{PROBLEM_ID}/output_{i}.txt', output_path)

            with open(input_path,  'r') as f:
                input_data = f.read()
            with open(output_path, 'r') as f:
                expected = f.read()

            verdict, runtime_ms, memory_kb, actual_output = run_testcase(
                LANGUAGE, code_path, input_data, TIME_LIMIT
            )
            max_runtime_ms = max(max_runtime_ms, runtime_ms)
            max_memory_kb  = max(max_memory_kb, memory_kb)

            print(f'  [TC {i}/{TESTCASE_COUNT}] {verdict} ({runtime_ms}ms / {memory_kb}KB)')

            if verdict == 'TIME_LIMIT_EXCEEDED':
                final_verdict = 'WRONG'
                break
            elif verdict == 'RUNTIME_ERROR':
                final_verdict = 'ERROR'
                break
            elif not compare_output(actual_output, expected):
                final_verdict = 'WRONG'
                break

        # ④ 최종 결과 저장 (solve_submission + solve_result 는 항상)
        is_correct = (final_verdict == 'CORRECT')
        update_submission_state(conn, 'COMPLETED')
        insert_result(conn, final_verdict, max_memory_kb, max_runtime_ms, code_size)

        # problem_summary 제출/정답 카운트 증가 (일반 + 대회 공통)
        update_problem_counts(conn, is_correct)

        # 대회 제출이면 contest_submission 업데이트 + 점수/Redis 처리 추가
        if IS_CONTEST:
            handle_contest_result(conn, is_correct)

        print(f'[DONE] submissionId={SUBMISSION_ID} result={final_verdict} '
              f'runtime={max_runtime_ms}ms memory={max_memory_kb}KB contest={IS_CONTEST}')

    except Exception as e:
        print(f'[ERROR] {e}', file=sys.stderr)
        try:
            update_submission_state(conn, 'COMPLETED')
            insert_result(conn, 'ERROR', 0, 0, 0)
            if IS_CONTEST:
                handle_contest_result(conn, False)
        except Exception:
            pass
        sys.exit(1)

    finally:
        conn.close()


if __name__ == '__main__':
    main()