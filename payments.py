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
import threading
import urllib.request

import legal_pages  # 법적 고지 미확정 여부 확인(순환 의존 없음: legal_pages 는 표준 라이브러리만 사용)

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
# 결제 조회(orderId 기준) 공식 고정 URL prefix. 웹훅 본문을 신뢰하지 않고 재검증할 때 사용.
TOSS_PAYMENT_BY_ORDER_URL = "https://api.tosspayments.com/v1/payments/orders/"
HTTP_TIMEOUT_SEC = 10
# 웹훅은 10초 안에 응답해야 한다. 외부통신 상한을 8초 이내로 묶기 위해
# 조회 2초 + n8n 두 채널(각 3초) = 최악 8초. 조회는 최대 2초.
TOSS_WEBHOOK_TIMEOUT_SEC = 2
PRIVATE_DATA_RETENTION_DAYS = 90

_SUCCESS_STATES = ("DONE", "APPROVED", "PAID")

# 운영자 알림 채널. PAID 전환 시 채널별 아웃박스 작업을 1건씩 기록한다.
NOTIFY_CHANNELS = ("email", "telegram")
# 처리 잠금(lease) 유효시간: 만료되면 다른 drain 이 회수할 수 있다(재시작·중단 복구).
NOTIFY_LEASE_SECONDS = 120
# 재시도 백오프(분). attempts 수에 따라 next_retry_at 을 뒤로 민다.
_NOTIFY_BACKOFF_MIN = (1, 5, 15, 60, 180)


def notification_event_id(order_id, channel):
    """재시도해도 동일하게 유지되는 멱등 이벤트 ID."""
    return "order.paid:%s:%s" % (order_id, channel)


def notification_next_retry(now, attempts):
    """실패(명확) 채널의 다음 재시도 시각. attempts 는 증가 후 값."""
    idx = min(max(attempts - 1, 0), len(_NOTIFY_BACKOFF_MIN) - 1)
    return now + datetime.timedelta(minutes=_NOTIFY_BACKOFF_MIN[idx])

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


def launch_blockers():
    """라이브(live) 결제를 열기 전 반드시 해소해야 하는 미확정 운영 항목.

    - 사업장 주소 공개 표기: 현재 동·호수 없는 초안만 있으므로, 최종 공개 표기를
      BIZ_ADDRESS 로 명시 설정하기 전에는 차단한다(기본 표시값 존재와 무관).
    - 고객 문의 전화: 아직 미정이므로 BIZ_PHONE 설정 전에는 차단한다.
    비대면 이메일 운영은 유지하며, 전화·화상 상담 기능을 요구하지는 않는다(연락 수단 표기만).
    반환: 미확정 사유 문자열 리스트(없으면 빈 리스트).
    """
    reasons = []
    if not _env("BIZ_ADDRESS"):
        reasons.append("사업장 주소 공개 표기 미확정(BIZ_ADDRESS)")
    if not _env("BIZ_PHONE"):
        reasons.append("고객 문의 전화 미정(BIZ_PHONE)")
    if legal_pages.any_pending():
        reasons.append("법적 고지(약관·개인정보·환불) 미확정 문구 존재")
    return reasons


# 라이브 판매를 상품별로 더 확인해야 하는 상품(전문가 검토·복수 대상·연간). 확인 전까지 서버에서
# 차단하며, 상품별 환경변수 PRODUCT_LIVE_READY_<CODE>=true 로만 개별 해제한다.
LIVE_HOLD_PRODUCTS = ("EXPERT", "LIFE_DESIGN", "RELATION_BUSINESS", "ANNUAL_VIP")


def product_live_blocked(product_code):
    """상품별 라이브 판매 차단 사유(없으면 None).

    전문가·복수 대상·연간 상품은 운영 준비 확인 전까지 차단한다. 빠른 출시 대상(기본·심층)과
    알 수 없는 코드는 여기서 막지 않는다(후자는 create_order 가 검증). test 모드 호출자는
    이 함수를 적용하지 않는다(합성 테스트로 모든 상품 흐름 유지)."""
    if product_code in LIVE_HOLD_PRODUCTS and _env("PRODUCT_LIVE_READY_%s" % product_code).lower() != "true":
        return "상품 '%s' 라이브 판매 준비 미확정" % product_code
    return None


def live_payments_ready():
    """실(live) 결제를 열어도 되는지(공통 조건). 설정상 enabled 이고, live 모드라면 공통 미확정
    운영 항목(주소·전화·법적 고지)이 없어야 True. test 모드는 이 가드의 영향을 받지 않는다."""
    st = config_status()
    if not st["enabled"]:
        return False
    if st["mode"] == "live" and launch_blockers():
        return False
    return True


def client_config():
    """브라우저로 전달 가능한 설정만. 시크릿/암호화 키는 절대 포함하지 않는다.

    공통 출시 조건(주소·전화·법적 고지)이 미확정이면 live 에서 브라우저 결제도 비활성으로
    내려 결제창/키를 내보내지 않는다. test 모드는 영향받지 않는다."""
    st = config_status()
    ready = st["enabled"] and live_payments_ready()
    return {
        "enabled": ready,
        "mode": st["mode"],
        "clientKey": _env("TOSS_CLIENT_KEY") if ready else "",
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
        self.notifications = {}  # (order_id, channel) -> 작업 dict (UNIQUE 보장)
        self._lock = threading.Lock()
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

    # ---- 알림 아웃박스 (PAID 전환과 원자적으로 기록) ----
    def mark_paid_with_notifications(self, order_id, payment_key, now, notifications):
        """주문을 PAID 로 올리고 채널별 알림 작업을 같은 임계구역에서 기록한다.

        notifications: [(channel, event_id), ...]. UNIQUE(order_id, channel) 이므로
        이미 있는 (order_id, channel) 은 덮어쓰지 않는다(멱등·중복 방지).
        """
        self.calls += 1
        with self._lock:
            if order_id in self.orders:
                self.orders[order_id].update(
                    status="PAID", payment_key=payment_key, paid_at=now, updated_at=now)
            for channel, event_id in notifications:
                key = (order_id, channel)
                if key not in self.notifications:
                    self.notifications[key] = {
                        "order_id": order_id, "channel": channel, "event_id": event_id,
                        "status": "PENDING", "attempts": 0, "error_type": None,
                        "lease_until": None, "lease_token": None, "next_retry_at": now,
                        "created_at": now, "updated_at": now,
                    }

    def get_notifications_for_order(self, order_id):
        with self._lock:
            return [dict(v) for (oid, _), v in self.notifications.items() if oid == order_id]

    def claim_pending_notifications(self, now, lease_until, lease_token, limit=50, order_id=None):
        """재시도 대상(PENDING/FAILED, next_retry 도래, lease 만료)을 원자적으로 선점한다.

        선점 시 lease_token 을 각인한다(펜싱). 결과 기록은 이 토큰을 제시해야 반영된다.
        order_id 가 주어지면 그 주문의 작업만 선점한다(결제 직후 즉시 알림/수동 재전송).
        SENT/UNKNOWN 은 어떤 경우에도 선점하지 않는다(중복 발송·UNKNOWN 자동 재전송 방지).
        """
        self.calls += 1
        claimed = []
        with self._lock:
            items = sorted(self.notifications.values(), key=lambda r: r["created_at"])
            for r in items:
                if len(claimed) >= limit:
                    break
                if order_id is not None and r["order_id"] != order_id:
                    continue  # 특정 주문만 대상
                if r["status"] not in ("PENDING", "FAILED"):
                    continue  # SENT/UNKNOWN 은 자동 재시도 대상이 아니다
                if r.get("next_retry_at") and r["next_retry_at"] > now:
                    continue
                if r.get("lease_until") and r["lease_until"] > now:
                    continue  # 다른 worker 가 선점 중
                r["lease_until"] = lease_until
                r["lease_token"] = lease_token
                r["updated_at"] = now
                claimed.append({"order_id": r["order_id"], "channel": r["channel"],
                                "event_id": r["event_id"], "attempts": r["attempts"]})
        return claimed

    def _apply_if_leased(self, order_id, channel, lease_token, **fields):
        """lease_token 이 현재 각인된 토큰과 같을 때만 기록한다(만료된 이전 worker 차단)."""
        with self._lock:
            r = self.notifications.get((order_id, channel))
            if r and r.get("lease_token") == lease_token:
                r.update(fields)
                r["lease_token"] = None  # lease 해제

    def mark_notification_sent(self, order_id, channel, now, lease_token):
        self._apply_if_leased(order_id, channel, lease_token, status="SENT",
                              lease_until=None, next_retry_at=None, error_type=None,
                              updated_at=now)

    def mark_notification_failed(self, order_id, channel, now, attempts, next_retry_at,
                                 error_type, lease_token):
        self._apply_if_leased(order_id, channel, lease_token, status="FAILED",
                              attempts=attempts, next_retry_at=next_retry_at, lease_until=None,
                              error_type=error_type, updated_at=now)

    def mark_notification_unknown(self, order_id, channel, now, attempts, error_type, lease_token):
        # 발송 여부 불명확: 자동 재시도하지 않고 별도 상태로 둔다(수동 확인).
        self._apply_if_leased(order_id, channel, lease_token, status="UNKNOWN",
                              attempts=attempts, next_retry_at=None, lease_until=None,
                              error_type=error_type, updated_at=now)


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
CREATE TABLE IF NOT EXISTS order_notifications (
    order_id TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    lease_until TIMESTAMPTZ,
    lease_token TEXT,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (order_id, channel)
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

    # ---- 알림 아웃박스 ----
    def mark_paid_with_notifications(self, order_id, payment_key, now, notifications):
        """PAID 전환 + 채널별 알림 작업 기록을 단일 트랜잭션으로 수행(원자성).

        `with conn` 블록이 정상 종료 시 commit, 예외 시 rollback 한다.
        ON CONFLICT 로 UNIQUE(order_id, channel) 중복 생성을 막는다.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE orders SET status=%s, payment_key=%s, paid_at=%s, updated_at=%s"
                " WHERE order_id=%s",
                ("PAID", payment_key, now, now, order_id))
            for channel, event_id in notifications:
                cur.execute(
                    "INSERT INTO order_notifications (order_id, channel, event_id, status,"
                    " attempts, next_retry_at, created_at, updated_at)"
                    " VALUES (%s,%s,%s,'PENDING',0,%s,%s,%s)"
                    " ON CONFLICT (order_id, channel) DO NOTHING",
                    (order_id, channel, event_id, now, now, now))

    def get_notifications_for_order(self, order_id):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, channel, event_id, status, attempts, error_type,"
                " lease_until, next_retry_at FROM order_notifications WHERE order_id=%s"
                " ORDER BY channel", (order_id,))
            cols = ["order_id", "channel", "event_id", "status", "attempts", "error_type",
                    "lease_until", "next_retry_at"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def claim_pending_notifications(self, now, lease_until, lease_token, limit=50, order_id=None):
        """재시도 대상을 원자적으로 선점(FOR UPDATE SKIP LOCKED + lease + 펜싱 토큰).

        order_id 가 주어지면 그 주문만 대상으로 한다(즉시 알림/수동 재전송). NULL 이면 전체 대기열.
        SENT/UNKNOWN 은 선점하지 않는다.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE order_notifications o SET lease_until=%s, lease_token=%s, updated_at=%s"
                " FROM ("
                "   SELECT order_id, channel FROM order_notifications"
                "   WHERE status IN ('PENDING','FAILED')"
                "     AND (%s::text IS NULL OR order_id=%s)"
                "     AND (next_retry_at IS NULL OR next_retry_at<=%s)"
                "     AND (lease_until IS NULL OR lease_until<=%s)"
                "   ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED) s"
                " WHERE o.order_id=s.order_id AND o.channel=s.channel"
                " RETURNING o.order_id, o.channel, o.event_id, o.attempts",
                (lease_until, lease_token, now, order_id, order_id, now, now, int(limit)))
            cols = ["order_id", "channel", "event_id", "attempts"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def mark_notification_sent(self, order_id, channel, now, lease_token):
        # 펜싱: 현재 lease_token 이 일치할 때만 기록(만료된 이전 worker 덮어쓰기 차단).
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE order_notifications SET status='SENT', lease_until=NULL,"
                " lease_token=NULL, next_retry_at=NULL, error_type=NULL, updated_at=%s"
                " WHERE order_id=%s AND channel=%s AND lease_token=%s",
                (now, order_id, channel, lease_token))

    def mark_notification_failed(self, order_id, channel, now, attempts, next_retry_at,
                                 error_type, lease_token):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE order_notifications SET status='FAILED', attempts=%s, next_retry_at=%s,"
                " lease_until=NULL, lease_token=NULL, error_type=%s, updated_at=%s"
                " WHERE order_id=%s AND channel=%s AND lease_token=%s",
                (int(attempts), next_retry_at, error_type, now, order_id, channel, lease_token))

    def mark_notification_unknown(self, order_id, channel, now, attempts, error_type, lease_token):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE order_notifications SET status='UNKNOWN', attempts=%s, next_retry_at=NULL,"
                " lease_until=NULL, lease_token=NULL, error_type=%s, updated_at=%s"
                " WHERE order_id=%s AND channel=%s AND lease_token=%s",
                (int(attempts), error_type, now, order_id, channel, lease_token))


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


def _toss_get_payment_by_order(order_id, timeout=TOSS_WEBHOOK_TIMEOUT_SEC):
    """orderId 로 결제를 조회한다(서버 시크릿 키, 고정 URL, 짧은 timeout).

    웹훅 본문은 서명이 없어 신뢰하지 않으므로, 상태·금액·주문을 이 조회 결과로 재검증한다.
    시크릿/paymentKey 는 로그·반환 밖으로 노출하지 않는다.
    """
    secret = _env("TOSS_SECRET_KEY")
    if not secret:
        raise PaymentConfigError("secret missing")
    import urllib.parse
    url = TOSS_PAYMENT_BY_ORDER_URL + urllib.parse.quote(str(order_id), safe="")
    auth = base64.b64encode((secret + ":").encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url, method="GET", headers={"Authorization": "Basic " + auth})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (고정 URL)
        return json.loads(resp.read().decode("utf-8"))


# 토스 웹훅에서 처리하는 유일한 이벤트 타입.
TOSS_WEBHOOK_EVENT = "PAYMENT_STATUS_CHANGED"


def handle_toss_webhook(store, event, lookup_fn=None):
    """토스 결제 웹훅 1건을 처리한다(Flask 비의존·순수 로직, 네트워크는 lookup_fn 으로 주입 가능).

    - PAYMENT_STATUS_CHANGED 외 이벤트/상태는 안전하게 무시(2xx).
    - 웹훅 본문을 신뢰하지 않고 orderId 로 토스 결제를 재조회해 status=DONE·orderId·금액을 대조.
    - 검증 성공 시 기존 approve_payment 를 멱등 호출(이미 PAID 면 재처리 없음).
    - 실패·금액/주문 불일치·조회 실패는 PAID 로 올리지 않는다.
    - 반환: {"status": <label>, "http": <code>, "notify_order_id": <order_id 또는 None>}.
      notify_order_id 가 있으면 호출측(route)에서 해당 주문의 PENDING 알림을 즉시 처리한다.
      이 함수는 알림을 직접 보내지 않는다(notifier 순환 import 회피).
    """
    if not isinstance(event, dict) or event.get("eventType") != TOSS_WEBHOOK_EVENT:
        return {"status": "ignored_event", "http": 200, "notify_order_id": None}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    order_id = data.get("orderId")
    if not order_id or not isinstance(order_id, str):
        return {"status": "ignored_event", "http": 200, "notify_order_id": None}

    order = store.get_order(order_id)
    if not order:
        # 우리 주문이 아니면(또는 아직 없음) 안전하게 무시. PAID 처리 금지.
        return {"status": "ignored_unknown_order", "http": 200, "notify_order_id": None}

    fn = lookup_fn or _toss_get_payment_by_order
    try:
        payment = fn(order_id)
    except Exception:
        # 네트워크/timeout/조회 실패: PAID 처리 금지. 2xx 아님 → 토스가 재시도.
        return {"status": "lookup_failed", "http": 502, "notify_order_id": None}

    if not isinstance(payment, dict):
        return {"status": "lookup_failed", "http": 502, "notify_order_id": None}
    status = payment.get("status")
    if status not in _SUCCESS_STATES:
        # DONE 등 성공 상태가 아니면 안전 무시(결제 취소/대기 등). PAID 처리 금지.
        return {"status": "ignored_status", "http": 200, "notify_order_id": None}
    if str(payment.get("orderId")) != order_id:
        return {"status": "rejected_order", "http": 400, "notify_order_id": None}
    try:
        total = int(payment.get("totalAmount"))
    except (TypeError, ValueError):
        return {"status": "rejected_amount", "http": 400, "notify_order_id": None}
    if total != order["amount"]:
        return {"status": "rejected_amount", "http": 400, "notify_order_id": None}
    payment_key = payment.get("paymentKey")
    if not payment_key:
        return {"status": "rejected_order", "http": 400, "notify_order_id": None}

    # 멱등 PAID 전환: confirm_fn 은 방금 재조회한(신뢰 가능한) 결제 객체를 그대로 반환.
    # 이미 PAID 인 주문은 approve_payment 가 재처리 없이 idempotent 로 반환한다.
    try:
        result = approve_payment(store, payment_key, order_id, total,
                                 confirm_fn=lambda **kw: payment)
    except OrderValidationError:
        # 이미 다른 경로로 처리됐거나 PAID 불가 상태: PAID 상태는 건드리지 않고
        # 혹시 남은 PENDING 알림만 처리하도록 한다(중복 결제처리 없음).
        return {"status": "already_paid", "http": 200, "notify_order_id": order_id}
    label = "already_paid" if result.get("idempotent") else "verified"
    return {"status": label, "http": 200, "notify_order_id": order_id}


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
    # PAID 전환과 채널별 알림 작업을 원자적으로 기록한다(외부 발송은 drain 에서 분리 실행).
    # 저장 실패 시 예외를 삼키지 않고 전파한다 → 주문은 PAID 로 올라가지 않고,
    # 성공 URL 재호출 시 approve_payment 가 멱등 재확인으로 복구한다.
    notifications = [(ch, notification_event_id(order_id, ch)) for ch in NOTIFY_CHANNELS]
    store.mark_paid_with_notifications(order_id, payment_key, now, notifications)
    return {"status": "PAID", "orderId": order_id, "idempotent": False}


def make_default_store():
    """운영 저장소 선택.

    - DATABASE_URL 이 있으면 결제 활성 여부와 무관하게 Postgres 를 사용한다
      (결제 비활성 상태에서도 기존 주문을 조회할 수 있어야 하므로).
    - Postgres 생성 실패는 조용히 InMemory 로 전환하지 않고 예외를 전파한다
      (영속 저장소를 기대하는데 휘발성으로 바뀌면 주문 유실이 은폐되기 때문).
    - DSN 이 없는데 실결제가 활성화되어 있으면 영속 저장소 부재이므로 차단한다
      (이 함수는 PAYMENTS_ENABLED 를 바꾸지 않는다; 설정 불일치를 기동 시 드러낸다).
    - DSN 이 없고 결제가 비활성이면 개발·합성 테스트용 InMemory 를 사용한다.
    """
    dsn = _env("DATABASE_URL")
    if dsn:
        return PostgresOrderStore(dsn)
    if payments_enabled():
        raise PaymentConfigError(
            "real payments require a persistent store (DATABASE_URL); refusing volatile InMemory")
    return InMemoryOrderStore()
