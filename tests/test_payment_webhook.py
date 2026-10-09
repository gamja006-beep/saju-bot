"""결제 직후 즉시 운영자 알림 + 토스 웹훅 + 관리자 수동 재전송 테스트.

실 네트워크·실 DB·실 발송 없이: InMemory 저장소 + 모의 토스 조회/확인 + 모의 n8n HTTP.
합성 주문만 사용하며 비밀값/개인정보를 단언에 쓰지 않는다.
"""

import os
import sys
import base64
import datetime
import unittest

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


def _now():
    return datetime.datetime(2026, 10, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _enable_payments():
    os.environ["PAYMENTS_ENABLED"] = "true"
    os.environ["PAYMENT_MODE"] = "test"
    os.environ["TOSS_CLIENT_KEY"] = "test_ck_sampleclient"
    os.environ["TOSS_SECRET_KEY"] = "test_sk_samplesecret"
    os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY


def _enable_notify():
    os.environ["OPERATOR_NOTIFICATIONS_ENABLED"] = "true"
    os.environ["N8N_NOTIFICATION_WEBHOOK_URL"] = "https://n8n.example.test/webhook/xyz"
    os.environ["N8N_NOTIFICATION_HEADER_SECRET"] = "header-secret-value"


def _clear_env():
    for k in _PAY_ENV + _NOTIFY_ENV + _ADMIN_ENV:
        os.environ.pop(k, None)


def _n8n_ok(body):
    return 200, {"event_type": "order.paid", "event_id": body["event_id"],
                 "order_id": body["order_id"], "channel": body["channel"], "status": "sent"}


def _auth(u, p):
    token = base64.b64encode(("%s:%s" % (u, p)).encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + token}


class _Base(unittest.TestCase):
    def setUp(self):
        _clear_env()
        _enable_payments()
        self.store = payments.InMemoryOrderStore()
        saju_bot.ORDER_STORE = self.store
        self.c = saju_bot.app.test_client()
        o = payments.create_order(self.store, "EXPERT", "customer@example.com",
                                  {"birth_date": "1990-05-15", "gender": "남"})
        self.oid, self.amount = o["orderId"], o["amount"]

    def tearDown(self):
        _clear_env()

    def _status(self, channel):
        rows = {r["channel"]: r for r in self.store.get_notifications_for_order(self.oid)}
        return rows.get(channel)


# ---- 1. 결제 승인 경로(/payment/success)에서 DB commit 후 즉시 알림 ----
class ImmediateNotifyTest(_Base):
    def _toss_done(self, **kw):
        return {"orderId": self.oid, "totalAmount": self.amount, "status": "DONE"}

    def _hit_success(self, http_post):
        """payments._toss_confirm 와 notifier._http_post 를 모의하고 /payment/success 호출."""
        saved_confirm = payments._toss_confirm
        saved_http = notifier._http_post
        payments._toss_confirm = self._toss_done
        notifier._http_post = lambda url, body, secret, timeout=10: http_post(body)
        try:
            return self.c.get("/payment/success?paymentKey=pk_1&orderId=%s&amount=%d"
                              % (self.oid, self.amount))
        finally:
            payments._toss_confirm = saved_confirm
            notifier._http_post = saved_http

    def test_paid_triggers_both_channels_immediately(self):
        _enable_notify()
        r = self._hit_success(_n8n_ok)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.get_order(self.oid)["status"], "PAID")
        self.assertEqual(self._status("email")["status"], "SENT")
        self.assertEqual(self._status("telegram")["status"], "SENT")

    def test_notify_failure_keeps_paid_and_success_page(self):
        _enable_notify()

        def boom(body):
            raise OSError("n8n unreachable")

        r = self._hit_success(boom)
        self.assertEqual(r.status_code, 200)  # 결제 성공 화면 정상
        self.assertEqual(self.store.get_order(self.oid)["status"], "PAID")  # PAID 유지
        self.assertEqual(self._status("email")["status"], "FAILED")  # 아웃박스에 기록
        self.assertEqual(self._status("telegram")["status"], "FAILED")

    def test_partial_failure_keeps_paid(self):
        _enable_notify()

        def mixed(body):
            if body["channel"] == "telegram":
                raise OSError("telegram down")
            return _n8n_ok(body)

        r = self._hit_success(mixed)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.get_order(self.oid)["status"], "PAID")
        self.assertEqual(self._status("email")["status"], "SENT")
        self.assertEqual(self._status("telegram")["status"], "FAILED")

    def test_timeout_keeps_paid_and_unknown(self):
        _enable_notify()

        def timeout(body):
            raise notifier.NotifyAmbiguous("timeout")

        r = self._hit_success(timeout)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.get_order(self.oid)["status"], "PAID")
        self.assertEqual(self._status("email")["status"], "UNKNOWN")

    def test_duplicate_success_no_double_send(self):
        _enable_notify()
        seen = []

        def counting(body):
            seen.append(body["channel"])
            return _n8n_ok(body)

        self._hit_success(counting)
        first = sorted(seen)
        self._hit_success(counting)  # 같은 결제 재호출(멱등)
        self.assertEqual(sorted(seen), first)  # 추가 발송 없음
        self.assertEqual(self._status("email")["status"], "SENT")
        self.assertEqual(self._status("telegram")["status"], "SENT")

    def test_notify_disabled_still_pays_no_calls(self):
        # 알림 미설정: 결제는 PAID, 외부 호출 0.
        calls = []
        r = self._hit_success(lambda body: calls.append(body) or _n8n_ok(body))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.get_order(self.oid)["status"], "PAID")
        self.assertEqual(calls, [])  # 설정 비활성 → 발송 시도 없음
        self.assertEqual(self._status("email")["status"], "PENDING")

    def test_failed_approval_no_notify(self):
        # 승인 실패(비 DONE): PAID 아님, 아웃박스/발송 없음.
        _enable_notify()
        saved_confirm = payments._toss_confirm
        saved_http = notifier._http_post
        calls = []
        payments._toss_confirm = lambda **kw: {"orderId": self.oid,
                                               "totalAmount": self.amount, "status": "CANCELED"}
        notifier._http_post = lambda url, body, secret, timeout=10: calls.append(body)
        try:
            r = self.c.get("/payment/success?paymentKey=pk_1&orderId=%s&amount=%d"
                           % (self.oid, self.amount))
        finally:
            payments._toss_confirm = saved_confirm
            notifier._http_post = saved_http
        self.assertEqual(r.status_code, 400)
        self.assertNotEqual(self.store.get_order(self.oid)["status"], "PAID")
        self.assertEqual(calls, [])
        self.assertEqual(self.store.get_notifications_for_order(self.oid), [])


# ---- 2. 토스 웹훅(/webhooks/toss) ----
class TossWebhookTest(_Base):
    def _event(self, order_id=None):
        return {"eventType": "PAYMENT_STATUS_CHANGED", "createdAt": "2026-10-09T12:00:00.000000",
                "data": {"orderId": order_id or self.oid}}

    def _lookup_done(self, total=None, order_id=None, status="DONE"):
        total = self.amount if total is None else total
        oid = order_id or self.oid

        def fn(order_id_arg):
            return {"orderId": oid, "paymentKey": "pk_webhook",
                    "totalAmount": total, "status": status}
        return fn

    def _post_webhook(self, event, lookup_fn, http_post=_n8n_ok):
        saved_lookup = payments._toss_get_payment_by_order
        saved_http = notifier._http_post
        payments._toss_get_payment_by_order = lookup_fn
        notifier._http_post = lambda url, body, secret, timeout=10: http_post(body)
        try:
            return self.c.post("/webhooks/toss", json=event)
        finally:
            payments._toss_get_payment_by_order = saved_lookup
            notifier._http_post = saved_http

    def test_done_marks_paid_and_notifies(self):
        _enable_notify()
        r = self._post_webhook(self._event(), self._lookup_done())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "verified")
        self.assertEqual(self.store.get_order(self.oid)["status"], "PAID")
        self.assertEqual(self._status("email")["status"], "SENT")
        self.assertEqual(self._status("telegram")["status"], "SENT")

    def test_amount_mismatch_no_paid(self):
        _enable_notify()
        calls = []
        r = self._post_webhook(self._event(), self._lookup_done(total=self.amount + 1000),
                               http_post=lambda body: calls.append(body) or _n8n_ok(body))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["status"], "rejected_amount")
        self.assertNotEqual(self.store.get_order(self.oid)["status"], "PAID")
        self.assertEqual(calls, [])

    def test_order_mismatch_no_paid(self):
        _enable_notify()
        # 조회 결과의 orderId 가 요청 주문과 다름 → 거절.
        r = self._post_webhook(self._event(), self._lookup_done(order_id="ord_OTHER"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["status"], "rejected_order")
        self.assertNotEqual(self.store.get_order(self.oid)["status"], "PAID")

    def test_lookup_failure_no_paid(self):
        _enable_notify()

        def boom(order_id_arg):
            raise OSError("toss lookup timeout")

        r = self._post_webhook(self._event(), boom)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["status"], "lookup_failed")
        self.assertNotEqual(self.store.get_order(self.oid)["status"], "PAID")

    def test_non_done_status_ignored_2xx(self):
        _enable_notify()
        r = self._post_webhook(self._event(), self._lookup_done(status="IN_PROGRESS"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ignored_status")
        self.assertNotEqual(self.store.get_order(self.oid)["status"], "PAID")

    def test_non_target_event_ignored_without_lookup(self):
        _enable_notify()
        called = []

        def lookup(order_id_arg):
            called.append(order_id_arg)
            return {}

        r = self._post_webhook({"eventType": "DEPOSIT_CALLBACK", "data": {"orderId": self.oid}},
                               lookup)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ignored_event")
        self.assertEqual(called, [])  # 조회조차 하지 않음
        self.assertNotEqual(self.store.get_order(self.oid)["status"], "PAID")

    def test_unknown_order_ignored(self):
        _enable_notify()
        called = []

        def lookup(order_id_arg):
            called.append(order_id_arg)
            return self._lookup_done()(order_id_arg)

        r = self._post_webhook(self._event(order_id="ord_NOT_OURS"), lookup)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ignored_unknown_order")
        self.assertEqual(called, [])  # 우리 주문 아님 → 조회 안 함

    def test_already_paid_processes_pending_only_no_resend(self):
        # 먼저 성공 승인으로 PAID + email 만 SENT 로 만든 뒤, 웹훅이 와도
        # 재결제 처리 없이 남은 telegram PENDING 만 처리한다.
        _enable_notify()
        payments.approve_payment(self.store, "pk_1", self.oid, self.amount,
                                 confirm_fn=lambda **kw: {"orderId": self.oid,
                                                          "totalAmount": self.amount, "status": "DONE"})
        # email 은 이미 SENT 로 둔다(웹훅이 재발송하면 안 됨). telegram 은 PENDING 유지.
        self.store.notifications[(self.oid, "email")].update(
            status="SENT", lease_until=None, lease_token=None, next_retry_at=None)
        seen = []
        r = self._post_webhook(self._event(), self._lookup_done(),
                               http_post=lambda body: seen.append(body["channel"]) or _n8n_ok(body))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "already_paid")
        self.assertEqual(seen, ["telegram"])  # email 재발송 없음
        self.assertEqual(self._status("email")["status"], "SENT")
        self.assertEqual(self._status("telegram")["status"], "SENT")

    def test_webhook_response_has_no_pii_or_secret(self):
        _enable_notify()
        r = self._post_webhook(self._event(), self._lookup_done())
        blob = r.get_data(as_text=True)
        for bad in ["customer@example.com", "header-secret-value", _FERNET_KEY,
                    "samplesecret", "pk_webhook", str(self.amount)]:
            self.assertNotIn(bad, blob)


# ---- 3. 관리자 수동 재전송(/admin/orders/<id>/notifications/retry) ----
class AdminRetryTest(_Base):
    def setUp(self):
        super().setUp()
        os.environ["ADMIN_USERNAME"] = _ADMIN_USER
        os.environ["ADMIN_PASSWORD"] = _ADMIN_PW
        # 주문을 PAID 로 만들어 상세/재전송 대상이 되게 한다.
        payments.approve_payment(self.store, "pk_1", self.oid, self.amount,
                                 confirm_fn=lambda **kw: {"orderId": self.oid,
                                                          "totalAmount": self.amount, "status": "DONE"})

    def _url(self):
        return "/admin/orders/%s/notifications/retry" % self.oid

    def test_requires_auth(self):
        r = self.c.post(self._url())
        self.assertEqual(r.status_code, 401)

    def test_get_not_allowed(self):
        r = self.c.get(self._url(), headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 405)  # POST 전용

    def test_no_store_header(self):
        _enable_notify()
        r = self.c.post(self._url(), headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertIn("no-store", r.headers.get("Cache-Control", ""))

    def test_resends_failed_channel(self):
        _enable_notify()
        # email 을 FAILED(재시도 시각 도래: next_retry_at=None → 즉시 대상)로 만든 뒤 재전송.
        self.store.notifications[(self.oid, "email")].update(
            status="FAILED", next_retry_at=None,
            attempts=1, error_type="transport_error", lease_until=None, lease_token=None)
        saved_http = notifier._http_post
        notifier._http_post = lambda url, body, secret, timeout=10: _n8n_ok(body)
        try:
            r = self.c.post(self._url(), headers=_auth(_ADMIN_USER, _ADMIN_PW))
        finally:
            notifier._http_post = saved_http
        self.assertEqual(r.status_code, 200)
        self.assertIn("재전송", r.get_data(as_text=True))
        self.assertEqual(self._status("email")["status"], "SENT")

    def test_unknown_not_resent(self):
        _enable_notify()
        # 두 채널 모두 UNKNOWN → 재전송 대상 아님(호출 0).
        for ch in ("email", "telegram"):
            self.store.notifications[(self.oid, ch)].update(
                status="UNKNOWN", next_retry_at=None, lease_until=None, lease_token=None)
        calls = []
        saved_http = notifier._http_post
        notifier._http_post = lambda url, body, secret, timeout=10: calls.append(body) or _n8n_ok(body)
        try:
            r = self.c.post(self._url(), headers=_auth(_ADMIN_USER, _ADMIN_PW))
        finally:
            notifier._http_post = saved_http
        self.assertEqual(r.status_code, 200)
        self.assertEqual(calls, [])  # UNKNOWN 은 claim 제외
        self.assertEqual(self._status("email")["status"], "UNKNOWN")

    def test_detail_shows_retry_button_when_ready(self):
        _enable_notify()
        body = self.c.get("/admin/orders/%s" % self.oid,
                          headers=_auth(_ADMIN_USER, _ADMIN_PW)).get_data(as_text=True)
        self.assertIn("실패 알림 재전송", body)
        self.assertIn("/notifications/retry", body)


# ---- 4. 외부통신 timeout 상한 ----
class TimeoutBoundsTest(unittest.TestCase):
    def test_toss_lookup_timeout_at_most_2s(self):
        self.assertLessEqual(payments.TOSS_WEBHOOK_TIMEOUT_SEC, 2)

    def test_n8n_telegram_timeout_at_most_3s(self):
        self.assertLessEqual(notifier.NOTIFY_HTTP_TIMEOUT, 3)

    def test_n8n_email_timeout_allows_slow_gmail_response(self):
        self.assertEqual(notifier.NOTIFY_EMAIL_TIMEOUT, 6)

    def test_webhook_total_external_budget_within_8s(self):
        # 조회 1회 + n8n 두 채널 병렬 = 최악 외부통신 상한.
        budget = payments.TOSS_WEBHOOK_TIMEOUT_SEC + max(
            notifier.NOTIFY_EMAIL_TIMEOUT, notifier.NOTIFY_HTTP_TIMEOUT)
        self.assertLessEqual(budget, 8)

    def test_immediate_notify_passes_channel_specific_timeouts(self):
        _clear_env()
        _enable_payments()
        _enable_notify()
        store = payments.InMemoryOrderStore()
        order = payments.create_order(store, "EXPERT", "customer@example.com", {})
        oid = order["orderId"]
        payments.approve_payment(store, "pk_test", oid, order["amount"],
                                 confirm_fn=lambda **kw: {"orderId": oid,
                                     "totalAmount": order["amount"], "status": "DONE"})
        seen = {}
        saved = notifier._http_post
        def fake(url, body, secret, timeout):
            seen[body["channel"]] = timeout
            return _n8n_ok(body)
        notifier._http_post = fake
        try:
            result = notifier.notify_order(store, oid)
        finally:
            notifier._http_post = saved
            _clear_env()
        self.assertEqual(result["sent"], 2)
        self.assertEqual(seen, {"email": 6, "telegram": 3})

    def test_two_channels_run_together_and_store_updates_after_response(self):
        import threading
        _clear_env()
        _enable_payments()
        _enable_notify()
        store = payments.InMemoryOrderStore()
        order = payments.create_order(store, "EXPERT", "customer@example.com", {})
        oid = order["orderId"]
        payments.approve_payment(store, "pk_test", oid, order["amount"],
                                 confirm_fn=lambda **kw: {"orderId": oid,
                                     "totalAmount": order["amount"], "status": "DONE"})
        together = threading.Barrier(2)
        def fake(body):
            together.wait(timeout=2)
            return _n8n_ok(body)
        try:
            result = notifier.notify_order(store, oid, http_post=fake)
        finally:
            _clear_env()
        self.assertEqual(result["sent"], 2)
        self.assertEqual({r["status"] for r in store.get_notifications_for_order(oid)}, {"SENT"})

    def test_toss_lookup_passes_bounded_timeout(self):
        # _toss_get_payment_by_order 가 urlopen 에 <=2초 timeout 을 전달하는지(네트워크 없이 확인).
        import urllib.request
        os.environ["TOSS_SECRET_KEY"] = "test_sk_samplesecret"
        seen = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"orderId":"o","status":"DONE","totalAmount":1,"paymentKey":"k"}'

        saved = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None, **kw: seen.update(timeout=timeout) or _Resp()
        try:
            payments._toss_get_payment_by_order("ord_x")
        finally:
            urllib.request.urlopen = saved
            os.environ.pop("TOSS_SECRET_KEY", None)
        self.assertLessEqual(seen["timeout"], 2)

    def test_n8n_post_passes_bounded_timeout(self):
        import urllib.request
        seen = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"status":"sent"}'
            def getcode(self): return 200

        saved = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None, **kw: seen.update(timeout=timeout) or _Resp()
        try:
            notifier._http_post("https://n8n.example.test/x",
                                {"event_type": "order.paid", "event_id": "e", "order_id": "o",
                                 "channel": "email"}, "secret")
        finally:
            urllib.request.urlopen = saved
        self.assertLessEqual(seen["timeout"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
