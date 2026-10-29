import os
import sys
import time
import psycopg2
import redis as redis_module
from runner_client import create_runner, create_storage_client, read_object
from execution_protocol import MAX_SOURCE, MAX_INPUT, MAX_CASES, MAX_MEMORY_MB, MAX_TIME, integer, decode, MAX_OUTPUT

# ───────────────────────────────────────────
# 제출 문맥은 Lambda에서, DB/실행 설정은 조정기 태스크에서 주입
# ───────────────────────────────────────────
SUBMISSION_ID  = os.environ['SUBMISSION_ID']   # solve_submission.id (bigint, 문자열로 수신)
CODE_S3_KEY    = os.environ['CODE_S3_KEY']     # e.g. submissions/123/Main.py
LANGUAGE       = os.environ['LANGUAGE']         # python | c | cpp
PROBLEM_ID     = os.environ['PROBLEM_ID']
TIME_LIMIT     = int(os.environ['TIME_LIMIT'])  # 초 단위
TESTCASE_COUNT = int(os.environ['TESTCASE_COUNT'])
MEMORY_LIMIT = int(os.environ['MEMORY_LIMIT'])
GRADING_ATTEMPT = int(os.environ['GRADING_ATTEMPT']) if os.environ.get('GRADING_ATTEMPT') else None

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
# Submitted code is never compiled or run in this image.

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
# [공통] 제출별 결과 저장 직렬화
# ───────────────────────────────────────────
def lock_submission(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT submission_state, prob_id FROM solve_submission WHERE id = %s FOR UPDATE",
            (SUBMISSION_ID,)
        )
        row = cur.fetchone()
    if row is None or str(row[1]) != str(PROBLEM_ID):
        raise ValueError('Submission not found or problem does not match')
    return row[0]


def begin_grading(conn):
    with conn:
        if lock_submission(conn) in ('COMPLETED', 'FAILED') or not current_attempt(conn):
            return False
        with conn.cursor() as cur:
            cur.execute("UPDATE solve_submission SET submission_state = 'JUDGING' WHERE id = %s",
                        (SUBMISSION_ID,))
            if IS_CONTEST:
                cur.execute("UPDATE contest_submission SET submission_status = 'JUDGING' "
                            "WHERE submission_id = %s AND submission_status = 'PENDING'", (SUBMISSION_ID,))
    return True


def current_attempt(conn):
    """Caller holds solve_submission lock. Fence late/expired workers before any writes."""
    if GRADING_ATTEMPT is None:
        return True  # Drain legacy requests before enabling durable dispatch.
    with conn.cursor() as cur:
        cur.execute('''SELECT attempt_no, finished_at, deadline_at > clock_timestamp()
                       FROM coding_grade_job WHERE submission_id = %s FOR UPDATE''', (SUBMISSION_ID,))
        row = cur.fetchone()
    return row is not None and row[0] == GRADING_ATTEMPT and row[1] is None and row[2]


def report_attempt_failure(conn):
    if GRADING_ATTEMPT is None:
        return
    with conn:
        if lock_submission(conn) in ('COMPLETED', 'FAILED'):
            return
        with conn.cursor() as cur:
            cur.execute('''UPDATE coding_grade_job
                           SET deadline_at = clock_timestamp(), failure_code = 'WORKER_FAILED'
                           WHERE submission_id = %s AND attempt_no = %s AND finished_at IS NULL''',
                        (SUBMISSION_ID, GRADING_ATTEMPT))

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

# ───────────────────────────────────────────
# [공통] 정답 통계만 반영 (제출 횟수는 백엔드 접수 시 집계)
# ───────────────────────────────────────────
def update_solved_count(conn, is_correct):
    if not is_correct:
        return
    with conn.cursor() as cur:
        cur.execute(
            """WITH added AS (
                INSERT INTO problem_solver(prob_id, user_id)
                SELECT prob_id, user_id FROM solve_submission WHERE id = %s AND prob_id = %s
                ON CONFLICT (prob_id, user_id) DO NOTHING RETURNING prob_id
            )
            UPDATE problem_summary SET solved_count = solved_count + 1
            WHERE id = %s AND EXISTS (SELECT 1 FROM added)""",
            (SUBMISSION_ID, PROBLEM_ID, PROBLEM_ID)
        )

# ───────────────────────────────────────────
# [대회] solve_submission_id로 contest_submission 조회
# ───────────────────────────────────────────
def get_contest_submission(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id FROM contest_submission
            WHERE submission_id = %s
              AND contest_id = %s AND contest_problem_id = %s
            """,
            (SUBMISSION_ID, CONTEST_ID, CONTEST_PROBLEM_ID)
        )
        row = cur.fetchone()
    return row

# ───────────────────────────────────────────
# [대회] contest_submission 결과 업데이트
# ───────────────────────────────────────────
def update_contest_submission_result(conn, contest_submission_id, is_correct):
    # 호출자가 점수 갱신과 함께 커밋합니다.
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contest_submission
            SET is_correct = %s, submission_status = 'COMPLETED'
            WHERE id = %s
            """,
            (is_correct, contest_submission_id)
        )

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
    # 호출자가 제출 결과 갱신과 함께 커밋합니다.
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contest_participant
            SET score = score + %s, last_solved_at = clock_timestamp() AT TIME ZONE 'UTC'
            WHERE contest_id = %s AND user_id = %s
            RETURNING score
            """,
            (points, contest_id, user_id)
        )
        new_score = cur.fetchone()[0]
    return new_score

# ───────────────────────────────────────────
# [대회] Redis 스코어보드 갱신
# ───────────────────────────────────────────
def update_scoreboard(contest_id, user_id, new_total_score):
    # Compatibility hook for result persistence tests. Ranking now reads committed
    # DB rows; no Redis write can roll the visible ordering back to an older score.
    return None

# ───────────────────────────────────────────
# [대회] 채점 완료 후 대회 전용 후처리
# ───────────────────────────────────────────
def apply_contest_result(conn, is_correct):
    # Caller owns the transaction; never commit or publish Redis here.
    new_total_score = None
    row = get_contest_submission(conn)
    if row is None:
        raise ValueError('Matching contest_submission not found for solve_submission_id=' + SUBMISSION_ID)

    contest_submission_id, user_id = row
    with conn.cursor() as cur:
        # 같은 참가자의 채점 워커를 직렬화한 뒤 첫 정답을 판단합니다.
        # 이 잠금은 결과와 점수가 함께 커밋될 때까지 유지됩니다.
        cur.execute(
            """SELECT score FROM contest_participant
               WHERE contest_id = %s AND user_id = %s FOR UPDATE""",
            (CONTEST_ID, user_id)
        )
        if cur.fetchone() is None:
            raise ValueError('Contest participant not found')

        cur.execute(
            "SELECT submission_status FROM contest_submission WHERE id = %s FOR UPDATE",
            (contest_submission_id,)
        )
        submission = cur.fetchone()
        if submission is None:
            raise ValueError('Contest submission disappeared during grading')
        # 중복 전달이나 오류 후처리로 확정된 결과를 덮어쓰지 않습니다.
        if submission[0] == 'COMPLETED':
            return

    first_correct = is_correct and not already_solved(
        conn, CONTEST_ID, CONTEST_PROBLEM_ID, user_id
    )
    update_contest_submission_result(conn, contest_submission_id, is_correct)
    if first_correct:
        new_total_score = add_contest_score(conn, CONTEST_ID, user_id, CONTEST_POINTS)

    if new_total_score is not None:
        return (user_id, new_total_score)


def publish_scoreboard(change):
    if change is None:
        return
    user_id, new_total_score = change
    try:
        update_scoreboard(CONTEST_ID, user_id, new_total_score)
    except Exception as e:
        print(f'[WARN] Redis scoreboard update failed: {e}', file=sys.stderr)


def handle_contest_result(conn, is_correct):
    with conn:
        change = apply_contest_result(conn, is_correct)
    publish_scoreboard(change)


def finalize_submission(conn, result_status, memory_usage, runtime, code_size):
    """Persist one result, its statistics, contest score and completion atomically."""
    with conn:
        if lock_submission(conn) in ('COMPLETED', 'FAILED') or not current_attempt(conn):
            return False
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM solve_result WHERE submission_id = %s', (SUBMISSION_ID,))
            if cur.fetchone() is not None:
                # Historical partial writes need review, not another result row.
                raise ValueError('An unfinished submission already has a result')
        is_correct = result_status == 'CORRECT'
        insert_result(conn, result_status, memory_usage, runtime, code_size)
        change = apply_contest_result(conn, is_correct) if IS_CONTEST else None
        update_solved_count(conn, is_correct)
        with conn.cursor() as cur:
            cur.execute("UPDATE solve_submission SET submission_state = 'COMPLETED' WHERE id = %s",
                        (SUBMISSION_ID,))
            if GRADING_ATTEMPT is not None:
                cur.execute('''UPDATE coding_grade_job SET finished_at = clock_timestamp(), failure_code = NULL
                               WHERE submission_id = %s AND attempt_no = %s''',
                            (SUBMISSION_ID, GRADING_ATTEMPT))
    publish_scoreboard(change)
    return True

# ───────────────────────────────────────────
# S3 다운로드
# ───────────────────────────────────────────
def compare_output(actual: str, expected: str) -> bool:
    actual_lines   = [line.rstrip() for line in actual.strip().splitlines()]
    expected_lines = [line.rstrip() for line in expected.strip().splitlines()]
    return actual_lines == expected_lines

# ───────────────────────────────────────────
# 메인 채점 로직
# ───────────────────────────────────────────
def main():
    s3   = create_storage_client()
    conn = get_db_conn()

    try:
        # 채점 시작 → JUDGING 상태로 변경 (solve_submission은 항상 업데이트)
        if not begin_grading(conn):
            print(f'[SKIP] submissionId={SUBMISSION_ID} terminal or stale attempt')
            return 0

        integer(TESTCASE_COUNT, 1, MAX_CASES)
        integer(TIME_LIMIT, 1, MAX_TIME)
        integer(MEMORY_LIMIT, 16, MAX_MEMORY_MB)
        source = read_object(s3, S3_BUCKET, CODE_S3_KEY, MAX_SOURCE)
        code_size = len(source)
        inputs, expected_outputs = [], []
        for i in range(1, TESTCASE_COUNT + 1):
            inputs.append(read_object(s3, S3_BUCKET, f'testcases/prob-{PROBLEM_ID}/input_{i}.txt', MAX_INPUT))
            expected_outputs.append(read_object(s3, S3_BUCKET, f'testcases/prob-{PROBLEM_ID}/output_{i}.txt', MAX_OUTPUT).decode('utf-8'))

        # Only code and stdin cross into the separate, credential-free task.
        execution = create_runner(s3).execute(LANGUAGE, source, inputs, TIME_LIMIT, MEMORY_LIMIT)
        if execution['compileError']:
            finalize_submission(conn, 'ERROR', 0, 0, code_size)
            return 0
        final_verdict = 'CORRECT'
        max_runtime_ms = 0
        max_memory_kb = 0
        for item, expected in zip(execution['cases'], expected_outputs):
            max_runtime_ms = max(max_runtime_ms, item['runtimeMs'])
            max_memory_kb = max(max_memory_kb, item['memoryKb'])
            if item['verdict'] == 'TIME_LIMIT_EXCEEDED':
                final_verdict = 'WRONG'  # Preserve the existing persisted verdict contract.
                break
            if item['verdict'] != 'OK':
                final_verdict = 'ERROR'
                break
            try:
                actual = decode(item['stdout'], MAX_OUTPUT).decode('utf-8')
            except UnicodeError:
                final_verdict = 'ERROR'
                break
            if not compare_output(actual, expected):
                final_verdict = 'WRONG'
                break

        # ④ 최종 결과 저장 (solve_submission + solve_result 는 항상)
        finalize_submission(conn, final_verdict, max_memory_kb, max_runtime_ms, code_size)

        print(f'[DONE] submissionId={SUBMISSION_ID} result={final_verdict} '
              f'runtime={max_runtime_ms}ms memory={max_memory_kb}KB contest={IS_CONTEST}')

    except Exception as e:
        # Infrastructure/persistence failures are not a judged ERROR verdict.
        # Leave the uncommitted result retryable; never replace a committed result.
        conn.rollback()
        try:
            report_attempt_failure(conn)
        except Exception:
            conn.rollback()  # Unreachable DB / process death is recovered by the durable deadline.
        print('[ERROR] Grading infrastructure failure', file=sys.stderr)
        return 1

    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
