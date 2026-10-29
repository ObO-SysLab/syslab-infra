"""Regression tests using a disposable, loopback-only PostgreSQL cluster.

Requires psycopg2 and local PostgreSQL binaries (PG_BIN can override the path).
Never reads production credentials or connects to an existing database.
Cluster files and logs remain in the OS temp directory for diagnosis.
"""
import ast
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

import psycopg2


def load_handler(submission_id):
    # Load the actual functions without importing AWS clients, Linux resource,
    # or reading deployment environment variables on the test machine.
    source = Path(__file__).resolve().parents[1] / 'grader.py'
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    namespace = {
        'sys': sys, 'time': time, 'os': os, 'SUBMISSION_ID': str(submission_id),
        'CONTEST_ID': '1', 'CONTEST_PROBLEM_ID': '2', 'CONTEST_POINTS': 100,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    namespace['update_scoreboard'] = Mock()
    return namespace


class ContestResultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = '.exe' if os.name == 'nt' else ''
        default_bin = 'C:/Program Files/PostgreSQL/17/bin' if os.name == 'nt' else ''
        pg_bin = Path(os.environ.get('PG_BIN', default_bin))
        cls.pg_ctl = str(pg_bin / ('pg_ctl' + suffix))
        initdb = str(pg_bin / ('initdb' + suffix))
        if not (Path(initdb).is_file() or shutil.which(initdb)):
            raise RuntimeError('Set PG_BIN to a PostgreSQL binary directory.')
        cls.directory = Path(tempfile.mkdtemp(prefix='syslab-grader-pg-'))
        cls.data = cls.directory / 'data'
        cls.process_options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        cls.addClassCleanup(cls.stop_database)
        print('Temporary PostgreSQL files:', cls.directory, flush=True)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            cls.port = listener.getsockname()[1]
        cls.run_pg(
            [initdb, '-D', str(cls.data), '-U', 'postgres', '-A', 'trust', '--no-locale', '-E', 'UTF8'],
            check=True, capture_output=True, timeout=60, **cls.process_options,
        )
        cls.run_pg(
            [cls.pg_ctl, '-D', str(cls.data), '-l', str(cls.directory / 'postgres.log'),
             '-o', f'-h 127.0.0.1 -p {cls.port}', '-w', '-t', '30', 'start'],
            # A background Windows postgres can inherit PIPE handles and keep
            # communicate() waiting after pg_ctl exits. Server output is in -l.
            check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=40, **cls.process_options,
        )
        with closing(cls.connect()) as conn, conn:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE contest_participant (
                        contest_id bigint, user_id bigint, score integer NOT NULL DEFAULT 0,
                        last_solved_at timestamp, PRIMARY KEY (contest_id, user_id)
                    );
                    CREATE TABLE contest_submission (
                        id bigint PRIMARY KEY, submission_id bigint UNIQUE,
                        contest_id bigint, contest_problem_id bigint, user_id bigint,
                        is_correct boolean, submission_status text NOT NULL
                    );
                    CREATE TABLE problem_summary (
                        id bigint PRIMARY KEY, submitted_count integer NOT NULL,
                        solved_count integer NOT NULL
                    );
                    CREATE TABLE solve_submission (
                        id bigint PRIMARY KEY, prob_id bigint REFERENCES problem_summary,
                        submission_state text NOT NULL
                    );
                    CREATE TABLE solve_result (
                        id bigserial PRIMARY KEY, submission_id bigint REFERENCES solve_submission,
                        result_status text NOT NULL, created_at timestamp NOT NULL
                    );
                    CREATE TABLE solve_result_coding (
                        result_id bigint PRIMARY KEY REFERENCES solve_result,
                        memory_usage integer, runtime integer, code_size integer, score integer
                    );
                ''')

    @classmethod
    def run_pg(cls, *args, **kwargs):
        try:
            return subprocess.run(*args, **kwargs)
        except subprocess.CalledProcessError as error:
            print((error.stdout or b'').decode(errors='replace'))
            print((error.stderr or b'').decode(errors='replace'))
            raise

    @classmethod
    def connect(cls):
        return psycopg2.connect(host='127.0.0.1', port=cls.port,
                                user='postgres', dbname='postgres', connect_timeout=5)

    @classmethod
    def stop_database(cls):
        if (cls.data / 'postmaster.pid').exists():
            subprocess.run([cls.pg_ctl, '-D', str(cls.data), '-m', 'immediate',
                            '-w', '-t', '30', 'stop'], check=True, capture_output=True,
                           timeout=40, **cls.process_options)
        print('Temporary PostgreSQL stopped; logs retained:', cls.directory, flush=True)

    def setUp(self):
        self.conn = self.connect()
        self.addCleanup(self.conn.close)
        with self.conn, self.conn.cursor() as cur:
            cur.execute('TRUNCATE contest_submission, contest_participant')
            cur.execute('TRUNCATE solve_result_coding, solve_result, solve_submission, problem_summary')
            cur.execute('ALTER TABLE contest_participant DROP CONSTRAINT IF EXISTS injected_failure')
            cur.execute('ALTER TABLE problem_summary DROP CONSTRAINT IF EXISTS injected_failure')
            cur.execute('ALTER TABLE solve_result_coding DROP CONSTRAINT IF EXISTS injected_failure')
            cur.execute('INSERT INTO contest_participant (contest_id, user_id) VALUES (1, 7)')
        self.add_submission(101)
        self.scope = load_handler(101)

    def add_submission(self, submission_id, problem_id=2):
        with self.conn, self.conn.cursor() as cur:
            cur.execute('''INSERT INTO contest_submission
                (id, submission_id, contest_id, contest_problem_id, user_id, submission_status)
                VALUES (%s, %s, 1, %s, 7, 'PENDING')''',
                        (submission_id, submission_id, problem_id))

    def score(self):
        with self.conn, self.conn.cursor() as cur:
            cur.execute('SELECT score FROM contest_participant WHERE contest_id=1 AND user_id=7')
            return cur.fetchone()[0]

    def result(self, submission_id=101):
        with self.conn, self.conn.cursor() as cur:
            cur.execute('SELECT is_correct, submission_status FROM contest_submission WHERE id=%s',
                        (submission_id,))
            return cur.fetchone()

    def handle(self, correct=True):
        self.scope['handle_contest_result'](self.conn, correct)

    def test_first_correct_awards_points(self):
        self.handle()
        self.assertEqual(self.score(), 100)
        self.assertEqual(self.result(), (True, 'COMPLETED'))
        self.scope['update_scoreboard'].assert_called_once_with('1', 7, 100)

    def test_wrong_answer_does_not_award_points(self):
        self.handle(False)
        self.assertEqual(self.score(), 0)
        self.assertEqual(self.result(), (False, 'COMPLETED'))
        self.scope['update_scoreboard'].assert_not_called()

    def test_duplicate_delivery_does_not_award_twice(self):
        self.handle()
        self.handle()
        self.assertEqual(self.score(), 100)
        self.scope['update_scoreboard'].assert_called_once()

    def test_completed_correct_is_not_overwritten_by_error_retry(self):
        self.handle()
        self.handle(False)
        self.assertEqual(self.result(), (True, 'COMPLETED'))
        self.assertEqual(self.score(), 100)

    def test_new_correct_submission_for_solved_problem_does_not_award_twice(self):
        self.handle()
        self.add_submission(102)
        load_handler(102)['handle_contest_result'](self.conn, True)
        self.assertEqual(self.score(), 100)
        self.assertEqual(self.result(102), (True, 'COMPLETED'))

    def test_correct_after_wrong_submission_awards_points(self):
        self.handle(False)
        self.add_submission(102)
        load_handler(102)['handle_contest_result'](self.conn, True)
        self.assertEqual(self.score(), 100)

    def test_different_problem_adds_its_own_points(self):
        self.handle()
        self.add_submission(102, problem_id=3)
        other = load_handler(102)
        other['CONTEST_PROBLEM_ID'] = '3'
        other['CONTEST_POINTS'] = 50
        other['handle_contest_result'](self.conn, True)
        self.assertEqual(self.score(), 150)
        other['update_scoreboard'].assert_called_once_with('1', 7, 150)

    def test_mismatched_contest_context_does_not_modify_data(self):
        for key in ['CONTEST_ID', 'CONTEST_PROBLEM_ID']:
            with self.subTest(key=key):
                other = load_handler(101)
                other[key] = '999'
                with self.assertRaises(ValueError):
                    other['handle_contest_result'](self.conn, True)
                self.assertEqual(self.score(), 0)
                self.assertEqual(self.result(), (None, 'PENDING'))
                other['update_scoreboard'].assert_not_called()

    def test_missing_participant_leaves_submission_pending(self):
        with self.conn, self.conn.cursor() as cur:
            cur.execute('DELETE FROM contest_participant')
        with self.assertRaises(ValueError):
            self.handle()
        self.assertEqual(self.result(), (None, 'PENDING'))
        self.scope['update_scoreboard'].assert_not_called()

    def test_score_write_failure_rolls_back_submission_result(self):
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE contest_participant ADD CONSTRAINT injected_failure CHECK (score < 50)')
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.handle()
        self.assertEqual(self.score(), 0)
        self.assertEqual(self.result(), (None, 'PENDING'))
        self.scope['update_scoreboard'].assert_not_called()

    def test_redis_failure_keeps_committed_score(self):
        self.scope['update_scoreboard'].side_effect = RuntimeError('Redis unavailable in test')
        self.handle()
        self.assertEqual(self.score(), 100)
        self.assertEqual(self.result(), (True, 'COMPLETED'))

    def test_concurrent_correct_submissions_award_once(self):
        self.add_submission(102)
        ready = threading.Barrier(2)

        def worker(submission_id):
            conn = self.connect()
            try:
                # Bound lock waits so a regression cannot hang the test suite.
                with conn, conn.cursor() as cur:
                    cur.execute("SET statement_timeout = '5s'")
                scope = load_handler(submission_id)
                check = scope['already_solved']

                def slow_check(*args):
                    answer = check(*args)
                    time.sleep(0.1)
                    return answer

                scope['already_solved'] = slow_check
                ready.wait(timeout=5)
                scope['handle_contest_result'](conn, True)
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, [101, 102]))
        self.assertEqual(self.score(), 100)
        self.assertEqual(self.result(), (True, 'COMPLETED'))
        self.assertEqual(self.result(102), (True, 'COMPLETED'))


    def prepare_full_submission(self, contest=False):
        self.scope.update(PROBLEM_ID='2', IS_CONTEST=contest)
        with self.conn, self.conn.cursor() as cur:
            cur.execute('INSERT INTO problem_summary VALUES (2, 1, 0)')
            cur.execute("INSERT INTO solve_submission VALUES (101, 2, 'PENDING')")
            if not contest:
                cur.execute('DELETE FROM contest_submission')

    def full_state(self, conn=None):
        with (conn or self.conn).cursor() as cur:
            cur.execute('''SELECT s.submission_state, p.submitted_count, p.solved_count,
                        (SELECT count(*) FROM solve_result),
                        (SELECT count(*) FROM solve_result_coding)
                        FROM solve_submission s JOIN problem_summary p ON p.id=s.prob_id
                        WHERE s.id=101''')
            return cur.fetchone()

    def finalize(self, verdict='CORRECT', scope=None, conn=None):
        return (scope or self.scope)['finalize_submission'](
            conn or self.conn, verdict, 1024, 10, 20)

    def test_finalization_counts_result_once_without_recounting_submission(self):
        self.prepare_full_submission()
        self.assertTrue(self.finalize())
        self.assertFalse(self.finalize())
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 1, 1, 1))

    def test_completed_submission_is_not_reopened(self):
        self.prepare_full_submission()
        self.finalize()
        self.assertFalse(self.scope['begin_grading'](self.conn))
        self.assertFalse(self.finalize('ERROR'))
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 1, 1, 1))

    def test_wrong_result_completes_without_increasing_solved_count(self):
        self.prepare_full_submission()
        self.finalize('WRONG')
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 0, 1, 1))

    def test_observer_cannot_see_partial_completion(self):
        self.prepare_full_submission(contest=True)
        save_stats = self.scope['update_solved_count']
        with closing(self.connect()) as observer:
            def check_before_commit(*args):
                save_stats(*args)
                self.assertEqual(self.full_state(observer), ('PENDING', 1, 0, 0, 0))
                with observer.cursor() as cur:
                    cur.execute('SELECT is_correct, submission_status FROM contest_submission WHERE id=101')
                    self.assertEqual(cur.fetchone(), (None, 'PENDING'))
                    cur.execute('SELECT score FROM contest_participant WHERE user_id=7')
                    self.assertEqual(cur.fetchone()[0], 0)
            self.scope['update_solved_count'] = check_before_commit
            self.finalize()
            self.assertEqual(self.full_state(observer), ('COMPLETED', 1, 1, 1, 1))
        self.assertEqual(self.score(), 100)

    def test_detail_write_failure_rolls_back_all_and_can_retry(self):
        self.prepare_full_submission()
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE solve_result_coding ADD CONSTRAINT injected_failure CHECK (runtime < 5)')
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.finalize()
        self.assertEqual(self.full_state(), ('PENDING', 1, 0, 0, 0))
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE solve_result_coding DROP CONSTRAINT injected_failure')
        self.finalize()
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 1, 1, 1))

    def test_statistics_failure_rolls_back_contest_score_and_result(self):
        self.prepare_full_submission(contest=True)
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE problem_summary ADD CONSTRAINT injected_failure CHECK (solved_count = 0)')
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.finalize()
        self.assertEqual(self.full_state(), ('PENDING', 1, 0, 0, 0))
        self.assertEqual(self.score(), 0)
        self.assertEqual(self.result(), (None, 'PENDING'))
        self.scope['update_scoreboard'].assert_not_called()
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE problem_summary DROP CONSTRAINT injected_failure')
        self.finalize()
        self.assertEqual(self.score(), 100)

    def test_concurrent_duplicate_workers_finalize_once(self):
        self.prepare_full_submission(contest=True)
        barrier = threading.Barrier(2)
        def worker(_):
            with closing(self.connect()) as conn:
                scope = load_handler(101)
                scope.update(PROBLEM_ID='2', IS_CONTEST=True)
                with conn, conn.cursor() as cur:
                    cur.execute("SET statement_timeout = '5s'")
                barrier.wait(timeout=5)
                return self.finalize(scope=scope, conn=conn)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(worker, [1, 2])), [False, True])
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 1, 1, 1))
        self.assertEqual(self.score(), 100)

    def test_legacy_partial_result_is_not_duplicated(self):
        self.prepare_full_submission()
        with self.conn, self.conn.cursor() as cur:
            cur.execute("INSERT INTO solve_result(submission_id,result_status,created_at) VALUES(101,'CORRECT',now())")
        with self.assertRaises(ValueError):
            self.finalize()
        self.assertEqual(self.full_state(), ('PENDING', 1, 0, 1, 0))

    def setup_main(self):
        self.prepare_full_submission()
        work = self.directory / self._testMethodName
        work.mkdir()
        self.scope.update(
            boto3=Mock(), get_db_conn=self.connect, LANGUAGE='python',
            CODE_S3_KEY='test/Main.py', TIME_LIMIT=1, TESTCASE_COUNT=1, WORK_DIR=str(work),
            compile_code=Mock(return_value=(True, '')),
            run_testcase=Mock(return_value=('OK', 1, 1024, '1\n')),
            download_from_s3=lambda client, key, path: Path(path).write_text('1\n'),
        )

    def test_main_compile_failure_saves_one_error_result(self):
        self.setup_main()
        self.scope['compile_code'].return_value = (False, 'bad syntax')
        self.scope['main']()
        self.scope['main']()
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 0, 1, 1))

    def test_main_storage_failure_does_not_create_fake_error_and_can_retry(self):
        self.setup_main()
        download = self.scope['download_from_s3']
        self.scope['download_from_s3'] = Mock(side_effect=RuntimeError('S3 unavailable in test'))
        self.assertEqual(self.scope['main'](), 1)
        self.assertEqual(self.full_state(), ('JUDGING', 1, 0, 0, 0))
        self.scope['download_from_s3'] = download
        self.scope['main']()
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 1, 1, 1))

    def test_main_db_failure_does_not_replace_verdict_with_error(self):
        self.setup_main()
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE problem_summary ADD CONSTRAINT injected_failure CHECK (solved_count = 0)')
        self.assertEqual(self.scope['main'](), 1)
        self.assertEqual(self.full_state(), ('JUDGING', 1, 0, 0, 0))
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE problem_summary DROP CONSTRAINT injected_failure')
        self.scope['main']()
        self.assertEqual(self.full_state(), ('COMPLETED', 1, 1, 1, 1))

    def test_backend_counter_sql_preserves_concurrent_increments(self):
        self.prepare_full_submission()
        repository = Path(__file__).resolve().parents[3] / 'syslab-backend/src/main/java/net/diveon/backend/domain/problem/repository/ProblemRepository.java'
        if not repository.is_file():
            self.skipTest('Cross-repository SQL check requires the sibling syslab-backend checkout')
        source = repository.read_text(encoding='utf-8-sig')
        queries = re.findall(r'@Query\(value = "(UPDATE problem_summary [^"]+)"', source)
        self.assertEqual(len(queries), 2)
        barrier = threading.Barrier(3)
        def increment(query):
            with closing(self.connect()) as conn:
                barrier.wait(timeout=5)
                for _ in range(10):
                    with conn, conn.cursor() as cur:
                        cur.execute(query.replace(':problemId', '%s'), (2,))
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(increment, [queries[0], queries[0], queries[1]]))
        self.assertEqual(self.full_state(), ('PENDING', 21, 10, 0, 0))

    def test_redis_notification_happens_after_full_commit(self):
        self.prepare_full_submission(contest=True)
        with closing(self.connect()) as observer:
            observed = []
            def check_commit(*args):
                observed.append(self.full_state(observer))
            self.scope['update_scoreboard'] = Mock(side_effect=check_commit)
            self.finalize()
            self.scope['update_scoreboard'].assert_called_once()
            self.assertEqual(observed, [('COMPLETED', 1, 1, 1, 1)])


if __name__ == '__main__':
    unittest.main()
