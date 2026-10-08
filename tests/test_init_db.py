"""init_db.py 테스트 — 실제 DB/네트워크 없이 connect 를 mock 한다."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import init_db
import payments


class FakeConn:
    def __init__(self, fail_on=None):
        self.executed = []
        self.committed = False
        self.rolledback = False
        self.closed = False
        self.fail_on = fail_on  # 1-based 실행 순번에서 예외 발생

    def execute(self, stmt):
        self.executed.append(stmt)
        if self.fail_on is not None and len(self.executed) == self.fail_on:
            raise RuntimeError("boom at %d" % self.fail_on)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolledback = True

    def close(self):
        self.closed = True


class InitDbTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://fake-dsn-for-test"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._saved

    def test_missing_database_url_nonzero(self):
        os.environ.pop("DATABASE_URL", None)
        with self.assertRaises(RuntimeError):
            init_db.init_schema(connect=lambda dsn: FakeConn())
        # main() 은 non-zero 반환
        rc = init_db.main()
        self.assertNotEqual(rc, 0)

    def test_create_statements_executed_once(self):
        fake = FakeConn()
        n = init_db.init_schema(connect=lambda dsn: fake)
        # orders, order_private_data, order_notifications 3개 테이블.
        self.assertEqual(n, 3)
        self.assertEqual(len(fake.executed), 3)
        creates = [s for s in fake.executed if "CREATE TABLE" in s]
        self.assertEqual(len(creates), 3)
        self.assertTrue(any("orders" in s for s in fake.executed))
        self.assertTrue(any("order_private_data" in s for s in fake.executed))
        self.assertTrue(any("order_notifications" in s for s in fake.executed))
        self.assertTrue(fake.committed)
        self.assertFalse(fake.rolledback)
        self.assertTrue(fake.closed)

    def test_schema_idempotent_markers_preserved(self):
        # SCHEMA_SQL 멱등(IF NOT EXISTS) 유지 확인(테이블 3개).
        self.assertEqual(payments.SCHEMA_SQL.count("CREATE TABLE IF NOT EXISTS"), 3)

    def test_main_success_outputs_schema_ok(self):
        fake = FakeConn()
        import psycopg
        saved = psycopg.connect
        psycopg.connect = lambda dsn: fake
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = init_db.main()
        finally:
            psycopg.connect = saved
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "schema ok")
        self.assertTrue(fake.committed)

    def test_failure_rolls_back_and_not_success(self):
        fake = FakeConn(fail_on=2)  # 두 번째 문장에서 실패
        with self.assertRaises(RuntimeError):
            init_db.init_schema(connect=lambda dsn: fake)
        self.assertFalse(fake.committed)
        self.assertTrue(fake.rolledback)
        # main() 은 실패 시 non-zero + 'schema ok' 미출력
        import psycopg
        saved = psycopg.connect
        psycopg.connect = lambda dsn: FakeConn(fail_on=2)
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = init_db.main()
        finally:
            psycopg.connect = saved
        self.assertNotEqual(rc, 0)
        self.assertNotIn("schema ok", out.getvalue())

    def test_no_secret_in_output_on_error(self):
        import psycopg
        saved = psycopg.connect

        def bad_connect(dsn):
            raise RuntimeError("postgresql://user:SECRETPW@dbhost:5432/railway refused")
        psycopg.connect = bad_connect
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = init_db.main()
        finally:
            psycopg.connect = saved
        combined = out.getvalue() + err.getvalue()
        self.assertNotEqual(rc, 0)
        self.assertNotIn("SECRETPW", combined)
        self.assertNotIn("postgresql://", combined)
        self.assertNotIn("dbhost", combined)
        self.assertIn("RuntimeError", combined)  # 예외 종류만

    def test_no_hardcoded_secrets_in_source(self):
        with open(os.path.join(ROOT, "init_db.py"), "r", encoding="utf-8") as f:
            src = f.read()
        for bad in ["postgresql://", "postgres://", "sk_live", "sk_test", "password="]:
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
