"""비회원 주문·결제 기반 (토스페이먼츠 테스트 모드 + Railway PostgreSQL 예정).

보안 원칙:
- 상품 가격은 서버 PRODUCTS 정본에서만 결정한다(클라이언트 금액 불신).
- 주문번호는 secrets 기반 예측 불가 난수.
- 개인정보(이메일·상담자료)는 평문 저장 금지 → Fernet 대칭 암호화.
- 상담정보와 결제정보 테이블 분리(order_private_data / orders).
- 시크릿/암호화 키는 환경변수만 사용. 로그·오류응답에 노출 금지.
- 환경변수 미비 시 결제 자동 비활성(무료 명식은 영향 없음).
- 토스 승인 URL은 고정 상수만 사용(사용자 입력 URL 호출 금지), timeout 적용.
- 실제 라이브 결제/실 키/실 DB 접속은 이번 단계에서 하지 않는다.
"""

import os
import json
import base64
import secrets
import datetime
import urllib.request

# ---- 상품 서버 정본 (금액: 원, 정수) ----
PRODUCTS = {
    "BASIC": {"name": "기본 해석", "amount": 9900},
    "DEEP": {"name": "심층 보고서", "amount": 39000},
    "EXPERT": {"name": "전문가 보고서", "amount": 99000},
    "LIFE_DESIGN": {"name": "인생설계 보고서", "amount": 290000},
    "RELATION_BUSINESS": {"name": "관계·사업 보고서", "amount": 590000},
    "ANNUAL_VIP": {"name": "연간 VIP", "amount": 990000},
}
CURRENCY = "KRW"

# 토스페이먼츠 결제 승인 공식 고정 URL (사용자 입력 금지)
TOSS_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"
HTTP_TIMEOUT_SEC = 10
PRIVATE_DATA_RETENTION_DAYS = 90

_SUCCESS_STATES = ("DONE", "APPROVED", "PAID")

import re as _re
_EMAIL_RE = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class PaymentError(Exception):
    """결제 처리 일반 오류(내부)."""


class PaymentConfigError(PaymentError):
    """결제 설정(키/모드) 미비. 사용자에게는 '준비 중'으로 안내."""


class OrderValidationError(PaymentError):
    """주문 입력 검증 실패. HTTP 400."""


def _env(name):
    return os.environ.get(name) or ""


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def payment_mode():
    return (_env("PAYMENT_MODE") or "test").lower()


def _mode_prefix(mode):
    return "test_" if mode == "test" else "live_"


def config_status():
    """결제 설정 상태. enabled는 플래그+키+모드 일관성 모두 충족 시에만 True."""
    flag = _env("PAYMENTS_ENABLED").lower() == "true"
    mode = payment_mode()
    client = _env("TOSS_CLIENT_KEY")
    secret = _env("TOSS_SECRET_KEY")
    enc = _env("ORDER_ENCRYPTION_KEY")
    has_keys = bool(client and secret and enc)
    mode_ok = mode in ("test", "live")
    # 테스트/라이브 혼용 차단: 키 프리픽스가 모드와 일치해야 한다.
    prefix = _mode_prefix(mode)
    keys_match_mode = bool(client.startswith(prefix) and secret.startswith(prefix)) if has_keys else False
    enabled = flag and has_keys and mode_ok and keys_match_mode
    return {"enabled": enabled, "mode": mode, "has_keys": has_keys, "keys_match_mode": keys_match_mode}


def payments_enabled():
    return config_status()["enabled"]


def client_config():
    """브라우저로 전달 가능한 설정만. 시크릿/암호화 키는 절대 포함하지 않는다."""
    st = config_status()
    return {
        "enabled": st["enabled"],
        "mode": st["mode"],
        "clientKey": _env("TOSS_CLIENT_KEY") if st["enabled"] else "",
    }


# ---- 암호화 (Fernet: AES128-CBC + HMAC) ----
def _fernet():
    from cryptography.fernet import Fernet
    key = _env("ORDER_ENCRYPTION_KEY")
    if not key:
        raise PaymentConfigError("encryption key missing")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token):
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def valid_email(email):
    email = (email or "").strip()
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email))


def generate_order_id():
    """예측 불가능한 주문번호. 토스 orderId 허용 문자/길이(6~64) 범위."""
    return "ord_" + secrets.token_hex(16)  # prefix + 32 hex = 36 chars


# ---- 주문 저장소 ----
class InMemoryOrderStore:
    """기본/테스트용 저장소(실 DB 미접속). 호출 횟수로 '무료=DB 0회'를 검증 가능."""

    def __init__(self):
        self.orders = {}
        self.private = {}
        self.calls = 0

    def create_order(self, rec):
        self.calls += 1
        self.orders[rec["order_id"]] = dict(rec)

    def get_order(self, order_id):
        self.calls += 1
        r = self.orders.get(order_id)
        return dict(r) if r else None

    def update_order(self, order_id, **fields):
        self.calls += 1
        if order_id in self.orders:
            self.orders[order_id].update(fields)

    def save_private(self, rec):
        self.calls += 1
        self.private[rec["order_id"]] = dict(rec)

    def get_private(self, order_id):
        self.calls += 1
        r = self.private.get(order_id)
        return dict(r) if r else None

    def list_paid_orders(self, limit=100):
        """읽기 전용: PAID 주문만 결제일(paid_at) 최신순으로 최대 limit건."""
        self.calls += 1
        paid = [dict(r) for r in self.orders.values() if r.get("status") == "PAID"]
        paid.sort(
            key=lambda r: r.get("paid_at") or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            reverse=True)
        return paid[:limit]


# Railway PostgreSQL 스키마 (이번 단계에서는 실행하지 않음; 운영 배포 시 적용).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    product_code TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'KRW',
    status TEXT NOT NULL DEFAULT 'PENDING',
    payment_key TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    paid_at TIMESTAMPTZ,
    private_data_expires_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS order_private_data (
    order_id TEXT PRIMARY KEY REFERENCES orders(order_id) ON DELETE CASCADE,
    encrypted_email TEXT NOT NULL,
    encrypted_consultation_payload TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
"""


class PostgresOrderStore:
    """psycopg 기반 저장소(운영용). 파라미터 바인딩으로 SQL injection 방어.

    이번 단계에서는 인스턴스화/실행하지 않는다(실 DB 미접속).
    """

    def __init__(self, dsn):
        import psycopg  # 지연 import
        self._connect = lambda: psycopg.connect(dsn, connect_timeout=HTTP_TIMEOUT_SEC)

    def create_order(self, rec):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO orders (order_id, product_code, amount, currency, status,"
                " payment_key, created_at, updated_at, paid_at, private_data_expires_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rec["order_id"], rec["product_code"], rec["amount"], rec["currency"],
                 rec["status"], rec.get("payment_key"), rec["created_at"], rec["updated_at"],
                 rec.get("paid_at"), rec.get("private_data_expires_at")),
            )

    def get_order(self, order_id):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, product_code, amount, currency, status, payment_key,"
                " created_at, updated_at, paid_at, private_data_expires_at"
                " FROM orders WHERE order_id = %s", (order_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = ["order_id", "product_code", "amount", "currency", "status", "payment_key",
                    "created_at", "updated_at", "paid_at", "private_data_expires_at"]
            return dict(zip(cols, row))

    def update_order(self, order_id, **fields):
        if not fields:
            return
        sets = ", ".join("%s = %%s" % k for k in fields)  # 컬럼명은 코드 상수만
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE orders SET " + sets + " WHERE order_id = %s",
                        tuple(fields.values()) + (order_id,))

    def save_private(self, rec):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO order_private_data (order_id, encrypted_email,"
                " encrypted_consultation_payload, created_at, expires_at)"
                " VALUES (%s,%s,%s,%s,%s)",
                (rec["order_id"], rec["encrypted_email"], rec["encrypted_consultation_payload"],
                 rec["created_at"], rec["expires_at"]),
            )

    def get_private(self, order_id):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT order_id, encrypted_email, encrypted_consultation_payload,"
                        " created_at, expires_at FROM order_private_data WHERE order_id = %s",
                        (order_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = ["order_id", "encrypted_email", "encrypted_consultation_payload",
                    "created_at", "expires_at"]
            return dict(zip(cols, row))

    def list_paid_orders(self, limit=100):
        """읽기 전용: PAID 주문만 결제일 최신순으로 최대 limit건. 파라미터 바인딩."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, product_code, amount, currency, status, payment_key,"
                " created_at, updated_at, paid_at, private_data_expires_at"
                " FROM orders WHERE status = %s ORDER BY paid_at DESC LIMIT %s",
                ("PAID", int(limit)))
            rows = cur.fetchall()
            cols = ["order_id", "product_code", "amount", "currency", "status", "payment_key",
                    "created_at", "updated_at", "paid_at", "private_data_expires_at"]
            return [dict(zip(cols, r)) for r in rows]


# ---- 주문 생성 / 결제 승인 ----
def create_order(store, product_code, email, consultation_payload):
    """유료 주문을 준비한다. 금액은 서버 정본에서 확정. 개인정보는 암호화 저장."""
    if not payments_enabled():
        raise PaymentConfigError("payments disabled")
    if product_code == "FREE" or product_code not in PRODUCTS:
        raise OrderValidationError("무효한 상품 코드입니다.")
    if not valid_email(email):
        raise OrderValidationError("이메일 형식이 올바르지 않습니다.")

    product = PRODUCTS[product_code]
    amount = product["amount"]  # 서버 고정가 (클라이언트 금액 무시)
    order_id = generate_order_id()
    now = _now()
    expires = now + datetime.timedelta(days=PRIVATE_DATA_RETENTION_DAYS)

    if not isinstance(consultation_payload, (dict, list, str)):
        consultation_payload = {}
    enc_email = encrypt(email.strip())
    enc_payload = encrypt(json.dumps(consultation_payload, ensure_ascii=False))

    store.create_order({
        "order_id": order_id,
        "product_code": product_code,
        "amount": amount,
        "currency": CURRENCY,
        "status": "PENDING",
        "payment_key": None,
        "created_at": now,
        "updated_at": now,
        "paid_at": None,
        "private_data_expires_at": expires,
    })
    store.save_private({
        "order_id": order_id,
        "encrypted_email": enc_email,
        "encrypted_consultation_payload": enc_payload,
        "created_at": now,
        "expires_at": expires,
    })
    return {"orderId": order_id, "amount": amount, "orderName": product["name"], "currency": CURRENCY}


def _toss_confirm(payment_key, order_id, amount):
    """토스 결제 승인 호출(고정 URL, timeout). 키는 로그/반환에 노출하지 않는다."""
    secret = _env("TOSS_SECRET_KEY")
    if not secret:
        raise PaymentConfigError("secret missing")
    body = json.dumps({"paymentKey": payment_key, "orderId": order_id, "amount": amount}).encode("utf-8")
    auth = base64.b64encode((secret + ":").encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        TOSS_CONFIRM_URL, data=body, method="POST",
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310 (고정 URL)
        return json.loads(resp.read().decode("utf-8"))


def approve_payment(store, payment_key, order_id, amount, confirm_fn=None):
    """토스 승인 전 DB 주문/금액 대조 + 멱등 처리. 승인 성공 시에만 PAID."""
    if not payment_key or not order_id:
        raise OrderValidationError("결제 정보가 올바르지 않습니다.")
    order = store.get_order(order_id)
    if not order:
        raise OrderValidationError("존재하지 않는 주문입니다.")

    try:
        amt = int(amount)
    except (TypeError, ValueError):
        raise OrderValidationError("금액이 올바르지 않습니다.")
    if amt != order["amount"]:
        store.update_order(order_id, status="FAILED", updated_at=_now())
        raise OrderValidationError("결제 금액이 주문과 일치하지 않습니다.")

    # 멱등: 이미 승인된 주문
    if order["status"] == "PAID":
        if order.get("payment_key") == payment_key:
            return {"status": "PAID", "orderId": order_id, "idempotent": True}
        raise OrderValidationError("이미 처리된 주문입니다.")
    if order["status"] in ("FAILED", "CANCELED"):
        raise OrderValidationError("결제할 수 없는 주문입니다.")

    fn = confirm_fn or _toss_confirm
    try:
        resp = fn(payment_key=payment_key, order_id=order_id, amount=amt)
    except Exception:
        # 토스 호출 실패/타임아웃: 절대 PAID로 처리하지 않는다.
        store.update_order(order_id, status="FAILED", updated_at=_now())
        raise PaymentError("approval call failed")

    ok = (
        str(resp.get("orderId")) == order_id
        and int(resp.get("totalAmount", -1)) == order["amount"]
        and resp.get("status") in _SUCCESS_STATES
    )
    if not ok:
        store.update_order(order_id, status="FAILED", updated_at=_now())
        raise PaymentError("approval not confirmed")

    now = _now()
    store.update_order(order_id, status="PAID", payment_key=payment_key, paid_at=now, updated_at=now)
    return {"status": "PAID", "orderId": order_id, "idempotent": False}


def make_default_store():
    """운영: DATABASE_URL + 결제 활성 시 Postgres, 그 외 In-Memory."""
    dsn = _env("DATABASE_URL")
    if dsn and payments_enabled():
        try:
            return PostgresOrderStore(dsn)
        except Exception:
            return InMemoryOrderStore()
    return InMemoryOrderStore()
