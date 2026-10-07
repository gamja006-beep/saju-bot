"""결제 스키마 초기화 진입점 (Railway Pre-deploy용, 멱등).

DATABASE_URL 의 PostgreSQL 에 payments.SCHEMA_SQL 을 적용한다.
- SCHEMA_SQL 은 CREATE TABLE IF NOT EXISTS 기반이라 재실행 안전(멱등).
- 성공 시 commit 후 `schema ok` 만 출력.
- 실패 시 rollback 하고 non-zero 종료. DSN·비밀번호·SQL 원문은 출력하지 않는다.
- 실제 DB/네트워크는 운영 환경에서만 발생하며, 테스트는 connect 주입(mock)으로 검증한다.
"""

import os
import sys


def init_schema(connect=None):
    """스키마를 생성한다.

    connect: psycopg.connect 호환 콜러블(테스트 주입용). None 이면 psycopg 사용.
    반환: 실행한 문장 수.
    """
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        # 비밀값을 출력하지 않고 변수 부재 사실만 알린다.
        raise RuntimeError("DATABASE_URL is not set")

    import payments
    statements = [s.strip() for s in payments.SCHEMA_SQL.split(";") if s.strip()]

    if connect is None:
        import psycopg
        connect = psycopg.connect

    conn = connect(dsn)
    try:
        for stmt in statements:
            conn.execute(stmt)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return len(statements)


def main():
    try:
        init_schema()
    except Exception as e:
        # 예외 종류만 출력한다(메시지에 DSN/비밀번호/SQL 원문이 섞이지 않도록).
        sys.stderr.write("schema init failed: %s\n" % type(e).__name__)
        return 1
    print("schema ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
