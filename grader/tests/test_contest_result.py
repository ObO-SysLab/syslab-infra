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
            cur.execute('ALTER TABLE contest_participant DROP CONSTRAINT IF EXISTS injected_failure')
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



if __name__ == '__main__':
    unittest.main()
