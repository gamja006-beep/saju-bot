"""운영자 알림(아웃박스 + n8n 요청) 테스트.

실 DB·실 네트워크·실 발송 없이: InMemory 저장소 + 모의 HTTP 로 검증한다.
합성 주문만 사용하며 비밀값/개인정보를 단언에 쓰지 않는다.
"""

import os
import sys
import base64
import datetime
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import payments
import notifier
import saju_bot
from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode("ascii")
_PAY_ENV = ["PAYMENTS_ENABLED", "PAYMENT_MODE", "TOSS_CLIENT_KEY",
            "TOSS_SECRET_KEY", "ORDER_ENCRYPTION_KEY", "DATABASE_URL"]
_NOTIFY_ENV = ["OPERATOR_NOTIFICATIONS_ENABLED", "N8N_NOTIFICATION_WEBHOOK_URL",
               "N8N_NOTIFICATION_HEADER_SECRET"]
_ADMIN_ENV = ["ADMIN_USERNAME", "ADMIN_PASSWORD"]
_ADMIN_USER, _ADMIN_PW = "alpha", "s3cret-pw-2026"


def _toss_ok(**kw):
    return {"orderId": kw["order_id"], "totalAmount": kw["amount"], "status": "DONE"}


def _now():
    return datetime.datetime(2026, 10, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _enable_payments():
    os.environ["PAYMENTS_ENABLED"] = "true"
    os.environ["PAYMENT_MODE"] = "test"
    os.environ["TOSS_CLIENT_KEY"] = "test_ck_sampleclient"
    os.environ["TOSS_SECRET_KEY"] = "test_sk_samplesecret"
    os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY


def _clear_env():
    for k in _PAY_ENV + _NOTIFY_ENV + _ADMIN_ENV:
        os.environ.pop(k, None)


# ---- 1. 저장소 선택 + 영속성 가드 ----
class StoreSelectionTest(unittest.TestCase):
    def tearDown(self):
        _clear_env()

    def test_postgres_selected_when_dsn_set_even_if_payments_disabled(self):
        _clear_env()
        os.environ["DATABASE_URL"] = "postgresql://fake-dsn-for-test"
        captured = {}

        class _FakePG:
            def __init__(self, dsn):
                captured["dsn"] = dsn

        saved = payments.PostgresOrderStore
        payments.PostgresOrderStore = _FakePG
        try:
            store = payments.make_default_store()
        finally:
            payments.PostgresOrderStore = saved
        self.assertIsInstance(store, _FakePG)           # 결제 비활성이어도 Postgres 선택
        self.assertIn("dsn", captured)

    def test_store_failure_does_not_fall_back_to_inmemory(self):
        _clear_env()
        os.environ["DATABASE_URL"] = "postgresql://fake-dsn-for-test"

        def _boom(dsn):
            raise RuntimeError("connect failed")

        saved = payments.PostgresOrderStore
        payments.PostgresOrderStore = _boom
        try:
            with self.assertRaises(Exception):          # 조용히 InMemory 로 전환하지 않는다
                payments.make_default_store()
        finally:
            payments.PostgresOrderStore = saved

    def test_payments_enabled_without_dsn_is_blocked(self):
        _clear_env()
        _enable_payments()  # DATABASE_URL 없음
        with self.assertRaises(payments.PaymentConfigError):
            payments.make_default_store()

    def test_inmemory_for_dev_when_disabled_and_no_dsn(self):
        _clear_env()
        store = payments.make_default_store()
        self.assertIsInstance(store, payments.InMemoryOrderStore)


# ---- 2. PAID 전환 시 아웃박스 기록 ----
class OutboxEnqueueTest(unittest.TestCase):
    def setUp(self):
        _clear_env()
        _enable_payments()
        self.store = payments.InMemoryOrderStore()

    def tearDown(self):
        _clear_env()

    def _paid(self, pk="pk_1", amount=None):
        o = payments.create_order(self.store, "EXPERT", "customer@example.com",
                                  {"birth_date": "1990-05-15", "gender": "남"})
        payments.approve_payment(self.store, pk, o["orderId"], o["amount"], confirm_fn=_toss_ok)
        return o["orderId"]

    def test_paid_creates_two_channel_jobs(self):
        oid = self._paid()
        rows = {r["channel"]: r for r in self.store.get_notifications_for_order(oid)}
        self.assertEqual(set(rows), {"email", "telegram"})
        for ch in ("email", "telegram"):
            self.assertEqual(rows[ch]["status"], "PENDING")
            self.assertEqual(rows[ch]["event_id"], "order.paid:%s:%s" % (oid, ch))
            self.assertEqual(rows[ch]["attempts"], 0)

    def test_idempotent_reconfirm_no_duplicates(self):
        o = payments.create_order(self.store, "EXPERT", "c@example.com",
                                  {"birth_date": "1990-05-15", "gender": "남"})
        oid, amt = o["orderId"], o["amount"]
        payments.approve_payment(self.store, "pk_1", oid, amt, confirm_fn=_toss_ok)
        res = payments.approve_payment(self.store, "pk_1", oid, amt, confirm_fn=_toss_ok)
        self.assertTrue(res.get("idempotent"))
        self.assertEqual(len(self.store.get_notifications_for_order(oid)), 2)

    def test_concurrent_enqueue_unique(self):
        o = payments.create_order(self.store, "EXPERT", "c@example.com",
                                  {"birth_date": "1990-05-15", "gender": "남"})
        oid = o["orderId"]
        notifs = [(ch, payments.notification_event_id(oid, ch)) for ch in payments.NOTIFY_CHANNELS]
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            self.store.mark_paid_with_notifications(oid, "pk_1", _now(), notifs)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.store.get_notifications_for_order(oid)), 2)


# ---- 3. drain 결과 처리 / 재시도 / 비활성 ----
class DrainTest(unittest.TestCase):
    def setUp(self):
        _clear_env()
        _enable_payments()
        self.store = payments.InMemoryOrderStore()
        # 주문 생성·결제 승인을 테스트 고정 시각(_now())으로 실행한다. 이렇게 하지 않으면
        # 알림 next_retry_at 이 실제 현재 시각으로 기록되어, 테스트의 과거 drain 시각(_now())이
        # 작업을 선점하지 못한다. patch.object 컨텍스트는 이 구간에서만 고정하고 자동 복원한다.
        with mock.patch.object(payments, "_now", return_value=_now()):
            o = payments.create_order(self.store, "EXPERT", "customer@example.com",
                                      {"birth_date": "1990-05-15", "gender": "남"})
            self.oid, amt = o["orderId"], o["amount"]
            payments.approve_payment(self.store, "pk_1", self.oid, amt, confirm_fn=_toss_ok)

    def tearDown(self):
        _clear_env()

    def test_next_retry_at_not_future_of_drain_time(self):
        # 회귀 가드: setUp 이 주문/승인을 고정 시각으로 기록하므로, 각 알림의 next_retry_at 은
        # 생성 시각(_now())과 같고, 테스트 drain 실행 시각(_now())보다 미래가 아니어야
        # (= 선점 가능) 한다. 이 관계가 깨지면 DrainTest 전체가 선점 실패로 무너진다.
        for ch in ("email", "telegram"):
            row = self._status(ch)
            self.assertEqual(row["next_retry_at"], _now())
            self.assertLessEqual(row["next_retry_at"], _now())

    def _enable_notify(self):
        os.environ["OPERATOR_NOTIFICATIONS_ENABLED"] = "true"
        os.environ["N8N_NOTIFICATION_WEBHOOK_URL"] = "https://n8n.example.test/webhook/xyz"
        os.environ["N8N_NOTIFICATION_HEADER_SECRET"] = "header-secret-value"

    def _status(self, channel):
        return {r["channel"]: r for r in self.store.get_notifications_for_order(self.oid)}[channel]

    def test_disabled_makes_no_external_calls(self):
        # 설정 비활성: http_post 가 호출되지 않아야 한다.
        calls = []
        notifier.drain(self.store, now=_now(), http_post=lambda body: calls.append(body))
        self.assertEqual(calls, [])
        # 작업은 여전히 PENDING(유실 없음)
        self.assertEqual(self._status("email")["status"], "PENDING")

    def test_sent_when_response_matches(self):
        self._enable_notify()

        def ok(body):
            return 200, {"event_type": "order.paid", "event_id": body["event_id"],
                         "order_id": body["order_id"], "channel": body["channel"], "status": "sent"}

        summary = notifier.drain(self.store, now=_now(), http_post=ok)
        self.assertEqual(summary["sent"], 2)
        self.assertEqual(self._status("email")["status"], "SENT")
        self.assertEqual(self._status("telegram")["status"], "SENT")

    def test_sent_channel_not_resent(self):
        self._enable_notify()
        seen = []

        def ok(body):
            seen.append(body["channel"])
            return 200, {"event_id": body["event_id"], "order_id": body["order_id"],
                         "channel": body["channel"], "status": "sent"}

        notifier.drain(self.store, now=_now(), http_post=ok)
        first = list(seen)
        # 두 번째 drain: 이미 SENT 라 다시 보내지 않는다.
        notifier.drain(self.store, now=_now() + datetime.timedelta(hours=1), http_post=ok)
        self.assertEqual(seen, first)  # 추가 호출 없음

    def test_http_5xx_is_unknown_not_retried(self):
        # 5xx 는 발송 여부 불명확 → UNKNOWN(자동 재시도 제외), 미발송으로 단정하지 않는다.
        self._enable_notify()
        calls = []

        def resp(body):
            calls.append(body)
            return 500, {"status": "error"}

        notifier.drain(self.store, now=_now(), http_post=resp)
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "http_500")
        # 다음 drain 에서 재발송하지 않는다.
        notifier.drain(self.store, now=_now() + datetime.timedelta(hours=1), http_post=resp)
        self.assertEqual(len(calls), 2)  # 이메일+텔레그램 1회씩. 재시도 없음.

    def test_response_mismatch_is_unknown(self):
        self._enable_notify()
        notifier.drain(self.store, now=_now(),
                       http_post=lambda body: (200, {"status": "queued"}))  # status != sent
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "response_unconfirmed")

    def test_event_id_mismatch_is_unknown(self):
        # HTTP 200 이지만 event_id 불일치 → 성공 단정 금지(UNKNOWN).
        self._enable_notify()
        notifier.drain(self.store, now=_now(), http_post=lambda body: (
            200, {"event_id": "order.paid:ord_WRONG:%s" % body["channel"],
                  "order_id": body["order_id"], "channel": body["channel"], "status": "sent"}))
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "response_unconfirmed")

    def test_order_id_mismatch_is_unknown(self):
        # HTTP 200 이지만 order_id 불일치 → UNKNOWN.
        self._enable_notify()
        notifier.drain(self.store, now=_now(), http_post=lambda body: (
            200, {"event_id": body["event_id"], "order_id": "ord_WRONG",
                  "channel": body["channel"], "status": "sent"}))
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "response_unconfirmed")

    def test_channel_mismatch_is_unknown(self):
        # HTTP 200 이지만 channel 불일치 → UNKNOWN.
        self._enable_notify()
        notifier.drain(self.store, now=_now(), http_post=lambda body: (
            200, {"event_id": body["event_id"], "order_id": body["order_id"],
                  "channel": "sms", "status": "sent"}))
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "response_unconfirmed")

    def _drain_once_with_code(self, code):
        self._enable_notify()
        notifier.drain(self.store, now=_now(),
                       http_post=lambda body: (code, {"status": "error"}))

    def _assert_not_auto_retried(self):
        # UNKNOWN 은 claim 대상이 아니므로 다음 drain 에서 외부 호출이 없어야 한다.
        calls = []

        def rec(body):
            calls.append(body)
            return 200, {"event_id": body["event_id"], "order_id": body["order_id"],
                         "channel": body["channel"], "status": "sent"}

        notifier.drain(self.store, now=_now() + datetime.timedelta(hours=2), http_post=rec)
        self.assertEqual(calls, [])

    def test_http_400_is_unknown_not_retried(self):
        # 400: 요청 계약 오류 → 자동 재시도 금지(UNKNOWN, 코드 보존).
        self._drain_once_with_code(400)
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "http_400")
        self._assert_not_auto_retried()

    def test_http_401_is_unknown_not_retried(self):
        # 401: 인증 설정 오류 → 자동 재시도 금지.
        self._drain_once_with_code(401)
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "http_401")
        self._assert_not_auto_retried()

    def test_http_403_is_unknown_not_retried(self):
        # 403: 인증 설정 오류 → 자동 재시도 금지.
        self._drain_once_with_code(403)
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "http_403")
        self._assert_not_auto_retried()

    def test_http_429_is_unknown_not_auto_retried(self):
        # 429: n8n 이 HTTP 응답을 돌려준 경우이므로, 발송 여부를 단정할 수 없어 UNKNOWN 으로
        # 둔다(중복 발송 방지 우선). 자동 재시도 대상이 아니며 운영자가 수동 확인한다.
        self._drain_once_with_code(429)
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        self.assertEqual(self._status("email")["error_type"], "http_429")
        self._assert_not_auto_retried()

    def test_email_success_telegram_failure_independent(self):
        # 채널 독립: email 성공(SENT) 과 telegram 실패(FAILED, 재시도)가 서로 간섭하지 않는다.
        self._enable_notify()

        def mixed(body):
            if body["channel"] == "telegram":
                raise OSError("telegram transport down")
            return 200, {"event_id": body["event_id"], "order_id": body["order_id"],
                         "channel": body["channel"], "status": "sent"}

        notifier.drain(self.store, now=_now(), http_post=mixed)
        self.assertEqual(self._status("email")["status"], "SENT")
        self.assertEqual(self._status("telegram")["status"], "FAILED")
        self.assertEqual(self._status("telegram")["error_type"], "transport_error")

    def test_telegram_success_email_failure_independent(self):
        # 반대 방향: telegram 성공, email 실패.
        self._enable_notify()

        def mixed(body):
            if body["channel"] == "email":
                raise OSError("email transport down")
            return 200, {"event_id": body["event_id"], "order_id": body["order_id"],
                         "channel": body["channel"], "status": "sent"}

        notifier.drain(self.store, now=_now(), http_post=mixed)
        self.assertEqual(self._status("telegram")["status"], "SENT")
        self.assertEqual(self._status("email")["status"], "FAILED")
        self.assertEqual(self._status("email")["error_type"], "transport_error")

    def test_timeout_is_unknown_not_resent(self):
        self._enable_notify()

        def timeout(body):
            raise notifier.NotifyAmbiguous("timeout")

        notifier.drain(self.store, now=_now(), http_post=timeout)
        self.assertEqual(self._status("email")["status"], "UNKNOWN")
        # UNKNOWN 은 자동 재시도 대상이 아니다 → 다음 drain 에서 호출 안 함.
        calls = []

        def rec(body):
            calls.append(body)
            raise notifier.NotifyAmbiguous("timeout")

        notifier.drain(self.store, now=_now() + datetime.timedelta(hours=1), http_post=rec)
        self.assertEqual(calls, [])

    def test_transport_error_is_failed_and_retried(self):
        # 전송 계층 실패(서버 미도달)만 '명확한 미발송' → FAILED(재시도).
        self._enable_notify()

        def boom(body):
            raise OSError("connection refused")

        notifier.drain(self.store, now=_now(), http_post=boom)
        row = self._status("email")
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["error_type"], "transport_error")
        self.assertIsNotNone(row["next_retry_at"])
        # next_retry 이후 재시도 → 이번엔 성공.
        later = _now() + datetime.timedelta(hours=1)
        notifier.drain(self.store, now=later, http_post=lambda body: (
            200, {"event_id": body["event_id"], "order_id": body["order_id"],
                  "channel": body["channel"], "status": "sent"}))
        self.assertEqual(self._status("email")["status"], "SENT")

    def test_second_claim_skips_leased(self):
        # 동시 경합: 첫 선점이 lease 를 잡으면 같은 시각의 두 번째 선점은 비어 있다.
        now = _now()
        first = self.store.claim_pending_notifications(now, now + datetime.timedelta(seconds=120),
                                                       "tok-A", limit=50)
        second = self.store.claim_pending_notifications(now, now + datetime.timedelta(seconds=120),
                                                        "tok-B", limit=50)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])

    def test_stale_worker_cannot_overwrite(self):
        # lease 만료 후 B 가 재선점·성공 처리한 뒤, 뒤늦게 깨어난 A(옛 토큰)의 기록은 무시된다.
        now = _now()
        self.store.claim_pending_notifications(now, now + datetime.timedelta(seconds=120),
                                               "tok-A", limit=50)
        later = now + datetime.timedelta(seconds=200)  # A 의 lease 만료
        self.store.claim_pending_notifications(later, later + datetime.timedelta(seconds=120),
                                               "tok-B", limit=50)
        self.store.mark_notification_sent(self.oid, "email", later, "tok-B")   # B 성공
        self.assertEqual(self._status("email")["status"], "SENT")
        # A 가 옛 토큰으로 실패 기록 시도 → 반영되지 않아야 한다.
        self.store.mark_notification_failed(self.oid, "email", now, 1,
                                            now + datetime.timedelta(minutes=5),
                                            "transport_error", "tok-A")
        self.assertEqual(self._status("email")["status"], "SENT")  # 덮어쓰기 차단됨

    def test_payload_has_no_pii_or_secret(self):
        self._enable_notify()
        captured = []

        def ok(body):
            captured.append(body)
            return 200, {"event_id": body["event_id"], "order_id": body["order_id"],
                         "channel": body["channel"], "status": "sent"}

        notifier.drain(self.store, now=_now(), http_post=ok)
        self.assertTrue(captured)
        for body in captured:
            self.assertEqual(set(body), {"event_type", "event_id", "order_id", "channel"})
            blob = str(body)
            for bad in ["customer@example.com", "header-secret-value", _FERNET_KEY,
                        "samplesecret", "pk_1", "EXPERT", "99000"]:
                self.assertNotIn(bad, blob)


# ---- 4. 관리자 drain 경로 + 상태 표시 ----
def _auth(u, p):
    token = base64.b64encode(("%s:%s" % (u, p)).encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + token}


class AdminNotifyRouteTest(unittest.TestCase):
    def setUp(self):
        _clear_env()
        _enable_payments()
        os.environ["ADMIN_USERNAME"] = _ADMIN_USER
        os.environ["ADMIN_PASSWORD"] = _ADMIN_PW
        self.store = payments.InMemoryOrderStore()
        saju_bot.ORDER_STORE = self.store
        self.c = saju_bot.app.test_client()
        o = payments.create_order(self.store, "EXPERT", "customer@example.com",
                                  {"birth_date": "1990-05-15", "gender": "남"})
        self.oid, amt = o["orderId"], o["amount"]
        payments.approve_payment(self.store, "pk_1", self.oid, amt, confirm_fn=_toss_ok)

    def tearDown(self):
        _clear_env()

    def test_drain_requires_auth(self):
        r = self.c.post("/admin/notifications/drain")
        self.assertEqual(r.status_code, 401)

    def test_drain_disabled_no_external_call(self):
        # 알림 미설정: 외부 호출 없이 disabled 상태 반환.
        r = self.c.post("/admin/notifications/drain", headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("status"), "disabled")

    def test_drain_sends_when_enabled(self):
        os.environ["OPERATOR_NOTIFICATIONS_ENABLED"] = "true"
        os.environ["N8N_NOTIFICATION_WEBHOOK_URL"] = "https://n8n.example.test/webhook/xyz"
        os.environ["N8N_NOTIFICATION_HEADER_SECRET"] = "header-secret-value"
        seen = []
        saved = notifier._http_post

        def fake(url, body, secret, timeout=10):
            seen.append((url, body, secret))
            return 200, {"event_id": body["event_id"], "order_id": body["order_id"],
                         "channel": body["channel"], "status": "sent"}

        notifier._http_post = fake
        try:
            r = self.c.post("/admin/notifications/drain", headers=_auth(_ADMIN_USER, _ADMIN_PW))
        finally:
            notifier._http_post = saved
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("sent"), 2)
        self.assertEqual(len(seen), 2)  # 실제 모의 발송 2회(이메일+텔레그램)

    def test_detail_shows_notification_status(self):
        body = self.c.get("/admin/orders/%s" % self.oid,
                          headers=_auth(_ADMIN_USER, _ADMIN_PW)).get_data(as_text=True)
        self.assertIn("운영자 알림 상태", body)
        self.assertIn("이메일", body)
        self.assertIn("텔레그램", body)
        self.assertIn("대기 중", body)  # PENDING 라벨
        # 비밀값/수신 이메일 설정은 화면에 노출하지 않는다.
        self.assertNotIn("header-secret-value", body)


# ---- 5. 관리 커맨드(러너) ----
import drain_notifications


class RunnerTest(unittest.TestCase):
    def setUp(self):
        _clear_env()
        _enable_payments()
        self.store = payments.InMemoryOrderStore()
        o = payments.create_order(self.store, "EXPERT", "customer@example.com",
                                  {"birth_date": "1990-05-15", "gender": "남"})
        self.oid, amt = o["orderId"], o["amount"]
        payments.approve_payment(self.store, "pk_1", self.oid, amt, confirm_fn=_toss_ok)

    def tearDown(self):
        _clear_env()

    def test_disabled_builds_no_store_and_no_calls(self):
        # 비활성: make_default_store 를 호출하지 않아야 한다(DB 연결 시도 0).
        saved = payments.make_default_store

        def _should_not_run():
            raise AssertionError("store must not be built when notifications disabled")

        payments.make_default_store = _should_not_run
        try:
            summary = drain_notifications.run()
        finally:
            payments.make_default_store = saved
        self.assertEqual(summary["status"], "disabled")
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(summary["batches"], 0)

    def test_enabled_runs_and_limits_batches(self):
        os.environ["OPERATOR_NOTIFICATIONS_ENABLED"] = "true"
        os.environ["N8N_NOTIFICATION_WEBHOOK_URL"] = "https://n8n.example.test/webhook/xyz"
        os.environ["N8N_NOTIFICATION_HEADER_SECRET"] = "header-secret-value"
        saved_store = payments.make_default_store
        saved_http = notifier._http_post
        payments.make_default_store = lambda: self.store

        def fake(url, body, secret, timeout=10):
            return 200, {"event_id": body["event_id"], "order_id": body["order_id"],
                         "channel": body["channel"], "status": "sent"}

        notifier._http_post = fake
        try:
            summary = drain_notifications.run()
        finally:
            payments.make_default_store = saved_store
            notifier._http_post = saved_http
        self.assertEqual(summary["status"], "ran")
        self.assertEqual(summary["sent"], 2)
        self.assertGreaterEqual(summary["batches"], 1)

    def test_main_output_has_no_secret(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = drain_notifications.main()  # 비활성(환경 미설정)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("drain disabled", out)
        for bad in ["header-secret-value", "https://", _FERNET_KEY, "customer@example.com"]:
            self.assertNotIn(bad, out)


# ---- 6. Postgres 결합 메서드 트랜잭션(모의 연결로 rollback 검증) ----
class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append(sql)
        if self.conn.fail_on is not None and len(self.conn.executed) == self.conn.fail_on:
            raise RuntimeError("boom at %d" % self.conn.fail_on)


class _FakeConn:
    def __init__(self, fail_on=None):
        self.executed = []
        self.committed = False
        self.rolledback = False
        self.fail_on = fail_on

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rolledback = True
        else:
            self.committed = True
        return False  # 예외를 전파

    def cursor(self):
        return _FakeCursor(self)


class PgTransactionTest(unittest.TestCase):
    """실 DB 없이 결합 메서드가 단일 트랜잭션을 쓰는지(중간 실패 시 전체 rollback) 모의 검증."""

    def _store_with(self, fake):
        store = payments.PostgresOrderStore.__new__(payments.PostgresOrderStore)
        store._connect = lambda: fake
        return store

    def test_mark_paid_with_notifications_commits_once(self):
        fake = _FakeConn()
        store = self._store_with(fake)
        notifs = [("email", "order.paid:ord_x:email"), ("telegram", "order.paid:ord_x:telegram")]
        store.mark_paid_with_notifications("ord_x", "pk_1", _now(), notifs)
        # UPDATE 1 + INSERT 2 = 3 문장, 단일 트랜잭션 commit.
        self.assertEqual(len(fake.executed), 3)
        self.assertTrue(fake.committed)
        self.assertFalse(fake.rolledback)

    def test_mark_paid_with_notifications_rolls_back_on_midway_failure(self):
        fake = _FakeConn(fail_on=2)  # 첫 INSERT 에서 실패
        store = self._store_with(fake)
        notifs = [("email", "e1"), ("telegram", "t1")]
        with self.assertRaises(RuntimeError):
            store.mark_paid_with_notifications("ord_x", "pk_1", _now(), notifs)
        self.assertTrue(fake.rolledback)       # 전체 rollback
        self.assertFalse(fake.committed)       # PAID 도 아웃박스도 커밋되지 않음


if __name__ == "__main__":
    unittest.main(verbosity=2)
